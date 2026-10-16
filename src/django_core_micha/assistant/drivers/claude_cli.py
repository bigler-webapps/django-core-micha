"""Constrained Claude Code subprocess driver."""
import asyncio
import hmac
import json
import logging
import os
import shutil
import signal
import tempfile
import uuid
from pathlib import Path

from django.conf import settings
from .base import DriverError, map_effort
from ..limits import get_limit

log = logging.getLogger(__name__)
BRIDGE = Path(__file__).resolve().parents[1] / "cli_bridge.py"


def safe_environment(credential, config_dir=None):
    env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "WINDIR", "HOME", "TEMP", "TMP", "LANG", "LC_ALL") if k in os.environ}
    if credential:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = str(credential)
    if config_dir is not None: env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    env.pop("ANTHROPIC_API_KEY", None)
    return env


def kill_process_tree(proc):
    try:
        if os.name == "nt":
            # /T ends the CLI's children (the MCP bridge) too; fall back to the direct process.
            import subprocess
            done = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
            if done.returncode: proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _session_dirs(chat_id, create=False):
    root = Path(getattr(settings, "DCM_ASSISTANT_CLI_ROOT", Path(tempfile.gettempdir()) / "dcm-assistant")).resolve()
    path = root / str(chat_id)
    if path.parent != root:
        raise DriverError("security_violation")
    if create:
        for item in (root, path, path / "config", path / "session"):
            if item.is_symlink():
                raise DriverError("security_violation")
            item.mkdir(mode=0o700, parents=True, exist_ok=True)
            try: item.chmod(0o700)
            except OSError: pass
    if path.is_symlink():
        raise DriverError("security_violation")
    return path, path / "config", path / "session"


class ClaudeCliDriver:
    def __init__(self, process_factory=None, kill_tree=None):
        self.process_factory = process_factory or asyncio.create_subprocess_exec
        self.kill_tree = kill_tree or kill_process_tree

    async def stream(self, *, prompt, model="", tools=(), credential="", effort="medium", chat=None, cancel_event=None,
                     session_id=None, **kwargs):
        binary = shutil.which("claude")
        if not binary or not credential:
            raise DriverError("not_configured")
        chat_id = getattr(chat, "pk", "adhoc")
        root, config, sessions = _session_dirs(chat_id, True)
        names = [t.name for t in tools]
        bridge_config = root / "bridge.json"
        env = safe_environment(credential, config)
        nonce = __import__("secrets").token_urlsafe(24)
        tool_events = []
        tool_calls = 0
        async def handle(reader, writer):
            nonlocal tool_calls
            try:
                line = await asyncio.wait_for(reader.readline(), 30)
                request = json.loads(line) if line and len(line) < 64_000 else {}
                if not hmac.compare_digest(str(request.get("nonce", "")), nonce):
                    answer = {"status": "error", "text": "tool_not_allowed"}
                else:
                    found = next((t for t in tools if t.name == request.get("name")), None)
                    if found is None: answer = {"status": "error", "text": "tool_not_allowed"}
                    elif tool_calls >= get_limit("max_tool_calls"): answer = {"status": "error", "text": "tool_call_limit"}
                    else:
                        tool_calls += 1
                        from .base import invoke_tool
                        tool_events.append({"type":"tool","phase":"start","name":found.name,"label":found.label})
                        content, sources = await invoke_tool(found, kwargs.get("user"), chat, request.get("arguments", {}), "cloud")
                        tool_events.append({"type":"tool","phase":"end","name":found.name,"label":found.label,"sources":sources})
                        answer = {"status":"ok","content":content}
                writer.write((json.dumps(answer, ensure_ascii=False)+"\n").encode()); await writer.drain()
            except Exception:
                writer.write(b'{"status":"error","text":"tool_unavailable"}\n')
                try: await writer.drain()
                except Exception: pass
            finally:
                writer.close()
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        # Endpoint and nonce live in a private 0600 file the bridge reads, never in argv or the CLI environment.
        bridge_config.write_text(json.dumps({"endpoint": f"tcp:127.0.0.1:{server.sockets[0].getsockname()[1]}", "nonce": nonce,
            "tools": [{"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in tools]}), encoding="utf-8")
        try: bridge_config.chmod(0o600)
        except OSError: pass
        argv = [binary, "-p", "--model", model, "--output-format", "stream-json", "--verbose", "--tools", "",
            "--strict-mcp-config", "--mcp-config", json.dumps({"mcpServers": {"dcm_tools": {
                "command": __import__("sys").executable, "args": [str(BRIDGE)],
                "env": {"DCM_ASSISTANT_BRIDGE_CONFIG": str(bridge_config)}}} if names else {}}, separators=(",", ":")),
            "--allowedTools", ",".join(f"mcp__dcm_tools__{n}" for n in names), "--effort", map_effort("claude_cli", effort),
            "--settings", '{"disableAllHooks":true,"disableBundledSkills":true,"autoMemoryEnabled":false}']
        resume = session_id and any((config / "projects").glob(f"*/{session_id}.jsonl"))
        active_id = session_id or str(uuid.uuid4())
        argv.extend(["--resume", active_id] if resume else ["--session-id", active_id])
        proc = await self.process_factory(*argv, cwd=str(root), env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=(os.name != "nt"), limit=get_limit("max_output_bytes"))
        async def stderr_log():
            data = await proc.stderr.read()
            if data:
                log.warning("Claude CLI stderr: %s", data.decode("utf-8", "replace")[-4000:])
        err_task = asyncio.create_task(stderr_log())
        saw_init = False
        saw_result = False
        assistant_usage = {}
        last_context = [0]
        total = 0
        try:
            proc.stdin.write(prompt.encode("utf-8"))
            await asyncio.wait_for(proc.stdin.drain(), get_limit("idle_timeout")); proc.stdin.close()
            await asyncio.wait_for(proc.stdin.wait_closed(), get_limit("idle_timeout"))
            async def read_events():
                nonlocal saw_init, saw_result, total, assistant_usage
                while True:
                    try: line = await asyncio.wait_for(proc.stdout.readline(), get_limit("idle_timeout"))
                    except asyncio.TimeoutError as exc: raise DriverError("timeout") from exc
                    if not line: break
                    total += len(line)
                    if total > get_limit("max_output_bytes"):
                        raise DriverError("invalid_output")
                    try: event = json.loads(line)
                    except ValueError as exc: raise DriverError("invalid_output") from exc
                    while tool_events: yield tool_events.pop(0)  # tool activity reaches the client while the turn runs
                    if event.get("type") == "system" and event.get("subtype") == "init":
                        listed = event.get("tools")
                        servers = event.get("mcp_servers")
                        expected = {f"mcp__dcm_tools__{n}" for n in names}
                        actual = sorted(listed) if isinstance(listed, list) else None
                        expected = sorted(expected)
                        valid_servers = (servers == [] if not names else isinstance(servers, list) and len(servers) == 1 and
                            isinstance(servers[0], dict) and servers[0].get("name") == "dcm_tools" and servers[0].get("status") == "connected")
                        if actual != expected or not valid_servers: raise DriverError("security_violation")
                        saw_init = True
                        yield {"type": "init"}
                    elif not saw_init:
                        if event.get("type") == "system":
                            continue
                        raise DriverError("security_violation")
                    elif event.get("type") == "rate_limit_event" and (event.get("rate_limit_info") or {}).get("status") == "rejected":
                        raise DriverError("quota_exceeded")
                    elif event.get("type") == "assistant" and "429" in str(event.get("error", "")):
                        raise DriverError("quota_exceeded")
                    elif event.get("type") == "assistant":
                        message = event.get("message") if isinstance(event.get("message"), dict) else {}
                        details = message.get("usage") or event.get("usage") or {}
                        if details:
                            last_context[0] = sum(int(details.get(k, 0) or 0) for k in
                                ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
                            for key, value in (("input_tokens", details.get("input_tokens", 0)),
                                               ("output_tokens", details.get("output_tokens", 0)),
                                               ("cache_read_tokens", details.get("cache_read_input_tokens", 0)),
                                               ("cache_write_tokens", details.get("cache_creation_input_tokens", 0))):
                                assistant_usage[key] = assistant_usage.get(key, 0) + int(value or 0)
                        blocks = event.get("content")
                        if blocks is None and message: blocks = message.get("content")
                        if isinstance(blocks, str): blocks = [{"type": "text", "text": blocks}]
                        for block in blocks if isinstance(blocks, list) else []:
                            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                                yield {"type": "delta", "text": block["text"]}
                    elif event.get("type") == "result":
                        if event.get("status") == 429 or event.get("api_error_status") == 429: raise DriverError("quota_exceeded")
                        usage = event.get("usage") or {}
                        mapped = assistant_usage or {"input_tokens": int(usage.get("input_tokens", 0) or 0),
                            "output_tokens": int(usage.get("output_tokens", 0) or 0),
                            "cache_read_tokens": int(usage.get("cache_read_input_tokens", 0) or 0),
                            "cache_write_tokens": int(usage.get("cache_creation_input_tokens", 0) or 0)}
                        mapped = dict(mapped)
                        if last_context[0]: mapped["context_tokens"] = last_context[0]
                        yield {"type": "usage", "usage": mapped}
                        saw_result = True
            async with asyncio.timeout(get_limit("provider_timeout")):
                async for frame in read_events(): yield frame
                await proc.wait()
            if proc.returncode or not saw_result: raise DriverError("provider_error" if proc.returncode else "invalid_output")
            for item in tool_events: yield item
            yield {"type": "session", "session_id": active_id}
        except asyncio.TimeoutError as exc:
            error = DriverError("timeout")
            error.before_init = not saw_init
            raise error from exc
        except asyncio.CancelledError as exc:
            exc.before_init = not saw_init  # lets the turn runner discard a resumed session that never reached init
            raise
        except DriverError as exc:
            if not saw_init:
                exc.before_init = True
            raise
        except Exception as exc:
            error = DriverError("provider_error")
            error.before_init = not saw_init
            raise error from exc
        finally:
            if proc.returncode is None:
                self.kill_tree(proc)
                try: await asyncio.wait_for(proc.wait(), 3)
                except Exception: pass
            err_task.cancel()
            try: await err_task
            except BaseException: pass
            server.close()
            await server.wait_closed()
