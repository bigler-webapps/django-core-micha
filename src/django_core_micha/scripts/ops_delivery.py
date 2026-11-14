"""sync-secrets <-> the ops delivery of webapp-management (WM-SEC-30).

An app environment that ops deploys (a strict prebuilt target) gets its runtime environment from the operations controller
(`webapp-management/ansible/deliver_secrets.py`, which reads Proton, runs `generate-env` with the real values and writes the
file on the server through Ansible, started inside WSL). `sync-secrets` asks that CLI which environments of the app in the
current directory are ops-managed (`list`), pushes only CI-only secrets to their GitHub Environment, and starts the delivery
(`deliver`) once per call for all of them. Nothing here reads a secret value: the CLI prints fixed words only, and this module
prints nothing but the lines of a strict shape.

The CLI is looked up as `../webapp-management/ansible/deliver_secrets.py` next to the app checkout (`WEBAPP_MANAGEMENT_DIR`
overrides the directory; an override that points nowhere is an error, never silently "no managed environment"). An app
outside that layout simply has no ops-managed environment and behaves as before.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

CLI_RELATIVE = Path("ansible") / "deliver_secrets.py"
ENVIRONMENTS = ("staging", "production")
LIST_TIMEOUT_SECONDS = 60
# The CLI limits one delivery run to 1500 s (deliver_secrets.RUN_SECONDS); the outer limit must exceed all of them, so the
# CLI's own timeout always fires first and the outer one only guards a CLI that hangs.
PER_TARGET_SECONDS = 1500
DELIVER_MARGIN_SECONDS = 120
_APP = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}\Z")
_MANAGED_LINE = re.compile(r"^managed (staging|production) ([A-Za-z0-9][A-Za-z0-9._-]{0,62})$")
_ERROR_LINE = re.compile(r"^error: ([a-z_]{1,40})$", re.M)
# The characters of a failed task name that the CLI lets through (deliver_secrets._TASK_NAME) plus its fixed reason codes.
_RESULT_LINE = re.compile(
    r"^deliver [A-Za-z0-9][A-Za-z0-9._-]{0,62}/(?:staging|production) -> [A-Za-z0-9][A-Za-z0-9._-]{0,62}: "
    r"(?:delivered \(changed=\d{1,4}\)|unchanged|FAILED \([A-Za-z0-9 ,.:()/_-]{1,120}\))$")
_ERROR_OUT = re.compile(r"^error: [a-z_]{1,40}$")


class OpsDeliveryError(Exception):
    """A fixed, value-free failure code."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def app_name(cwd=None):
    return Path(cwd or Path.cwd()).resolve().name


def cli_path(cwd=None):
    """The delivery CLI of the webapp-management checkout beside the app, or None when the layout is not there.

    A `WEBAPP_MANAGEMENT_DIR` that does not hold the CLI is a configuration error: the operator asked for it explicitly."""
    override = os.environ.get("WEBAPP_MANAGEMENT_DIR")
    base = Path(override) if override else Path(cwd or Path.cwd()).resolve().parent / "webapp-management"
    cli = base / CLI_RELATIVE
    if cli.is_file():
        return cli
    if override:
        raise OpsDeliveryError("cli_missing")
    return None


def cli_environment(cwd=None):
    """The environment of a CLI call: the app's parent directory is the workspace root the CLI reads project.yaml from, unless
    WEBAPPS_ROOT already names one (so a WEBAPP_MANAGEMENT_DIR elsewhere does not move the app lookup)."""
    env = dict(os.environ)
    env.setdefault("WEBAPPS_ROOT", str(Path(cwd or Path.cwd()).resolve().parent))
    return env


def managed_environments(cwd=None, run=None):
    """{environment: server} of the ops-managed environments of the app in `cwd`; {} when it has none.

    A CLI that fails or prints anything unexpected raises OpsDeliveryError: the caller must not push anything then, because
    it cannot tell which environment the server secrets belong to."""
    run = run or subprocess.run
    app = app_name(cwd)
    if not _APP.fullmatch(app):
        return {}  # no credentials.yml entry can name such a directory
    cli = cli_path(cwd)
    if cli is None:
        return {}
    try:
        result = run([sys.executable, str(cli), "list", "--app", app], capture_output=True, text=True,
                     timeout=LIST_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL, check=False, env=cli_environment(cwd))
    except (OSError, subprocess.SubprocessError):
        raise OpsDeliveryError("list_not_run") from None
    if result.returncode != 0:
        found = _ERROR_LINE.search(result.stdout or "")
        raise OpsDeliveryError(found.group(1) if found else "list_failed")
    managed = {}
    for line in (result.stdout or "").splitlines():
        match = _MANAGED_LINE.match(line.strip())
        if not match:
            raise OpsDeliveryError("list_unreadable")
        managed[match.group(1)] = match.group(2)
    return managed


def deliver(environments, cwd=None, run=None, out=None):
    """Deliver the runtime environment of the app in `cwd` to the named ops-managed environments; returns the exit status.

    Only lines of the CLI's fixed shapes are printed (a result line per target, or `error: <code>`); everything else it
    writes is dropped. A failure without any such line still prints a fixed line, so it is never silent."""
    run = run or subprocess.run
    out = out or sys.stdout
    cli = cli_path(cwd)
    wanted = [env for env in ENVIRONMENTS if env in set(environments)]
    if cli is None or not wanted:
        raise OpsDeliveryError("cli_missing" if cli is None else "no_environment")
    argv = [sys.executable, str(cli), "deliver", "--app", app_name(cwd)]
    for env in wanted:
        argv += ["--env", env]
    try:
        result = run(argv, capture_output=True, text=True, timeout=PER_TARGET_SECONDS * len(wanted) + DELIVER_MARGIN_SECONDS,
                     stdin=subprocess.DEVNULL, check=False, env=cli_environment(cwd))
    except subprocess.TimeoutExpired:
        raise OpsDeliveryError("deliver_timeout") from None
    except (OSError, subprocess.SubprocessError):
        raise OpsDeliveryError("deliver_not_run") from None
    printed = False
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if _RESULT_LINE.match(line) or _ERROR_OUT.match(line):
            print(line, file=out)
            printed = True
    if result.returncode != 0 and not printed:
        print("error: delivery_exit_%d" % int(result.returncode), file=out)
    return result.returncode
