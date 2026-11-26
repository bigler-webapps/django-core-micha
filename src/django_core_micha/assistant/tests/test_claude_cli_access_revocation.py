import asyncio
import json
from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync
from django.test import override_settings

from django_core_micha.assistant.drivers import base, claude_cli


@pytest.mark.parametrize("failure", ["tool", "authorization", "ordinary"])
def test_bridge_revocation_stops_continuation(monkeypatch, tmp_path, failure):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _: "claude")
    marker = "withdrawn-secret"
    replies, killed, frames = [], [], []
    tool = SimpleNamespace(name="lookup", label="Lookup", description="", input_schema={})

    async def invoke(*args):
        if failure == "tool":
            raise base.DriverError("access_lost")
        if failure == "ordinary":
            raise base.DriverError("invalid_output")
        return marker, []

    monkeypatch.setattr(base, "invoke_tool", invoke)

    async def run():
        stopped = asyncio.Event()
        class Input:
            def write(self, data): pass
            async def drain(self): pass
            def close(self): pass
            async def wait_closed(self): pass

        class Output:
            def __init__(self): self.first = True
            async def readline(self):
                if self.first:
                    self.first = False
                    return (json.dumps({"type": "system", "subtype": "init",
                        "tools": ["mcp__dcm_tools__lookup"],
                        "mcp_servers": [{"name": "dcm_tools", "status": "connected"}]}) + "\n").encode()
                await stopped.wait()
                return b""

        class Process:
            pid = 123
            returncode = None
            stdin = Input()
            stdout = Output()
            stderr = SimpleNamespace(read=lambda: asyncio.sleep(0, result=b""))
            async def wait(self):
                await stopped.wait()
                return self.returncode

        proc = Process()
        def kill(process):
            killed.append(True)
            process.returncode = -1
            stopped.set()

        async def factory(*args, **kwargs):
            config = json.loads((tmp_path / "chat" / "bridge.json").read_text(encoding="utf-8"))
            async def call():
                _, host, port = config["endpoint"].split(":")
                reader, writer = await asyncio.open_connection(host, int(port))
                writer.write((json.dumps({"nonce": config["nonce"], "name": "lookup", "arguments": {}}) + "\n").encode())
                await writer.drain()
                replies.append(await reader.readline())
                writer.close()
                await writer.wait_closed()
                if failure == "ordinary":
                    proc.returncode = 0
                    stopped.set()
            asyncio.create_task(call())
            return proc

        checks = 0
        async def authorize():
            nonlocal checks
            checks += 1
            return failure != "authorization" or checks == 1

        driver = claude_cli.ClaudeCliDriver(process_factory=factory, kill_tree=kill)
        with pytest.raises(base.DriverError) as error:
            async for frame in driver.stream(prompt="p", model="m", tools=[tool], credential="x",
                                              chat=SimpleNamespace(pk="chat"), authorize=authorize):
                frames.append(frame)
        await asyncio.sleep(0)
        return error.value.code

    with override_settings(DCM_ASSISTANT_CLI_ROOT=tmp_path):
        code = async_to_sync(run)()
    if failure == "ordinary":
        assert code == "invalid_output"
        assert json.loads(replies[0])["text"] == "tool_unavailable"
        assert not killed
    else:
        assert code == "access_lost"
        assert killed
        assert all(marker.encode() not in reply for reply in replies)
        assert not any(frame.get("type") in {"session", "delta"} for frame in frames)
