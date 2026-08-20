"""DX-4: warn at `run-dev` start when a generated artefact has outlived its source.

Two incidents (hram, 2026-08-19, same shape): `.env` kept a stale value after
`project.yaml` changed, and `backend/.venv` sat 24 minor versions behind
`requirements.txt`. Both are generated artefacts on the developer machine that
survive a change to their source with nothing announcing the gap -- every
deployed environment re-applies its source at each deploy; the developer
machine is the only place with no re-application step.

Both checks below are read-only, parse-only (no network, no resolver, no
subprocess), warn-never-block, and silent on a clean start. Every entry point
degrades to an empty warning list on any parse failure -- a malformed
project.yaml or an exotic venv layout must not stop `run-dev` from starting.
"""
import re
from pathlib import Path

import yaml

# Keys generate_env.py computes itself via add() and refuses to let
# app_env/env_overrides override (DEBUG is generate_env's own guarded
# exception for non-local environments). Comparing these against project.yaml's
# raw app_env/env_overrides would report a by-design difference, not real
# drift -- see generate_env.py's own comment block above its add() calls.
_PLATFORM_COMPUTED_ENV_KEYS = {
    "ENV_TYPE", "PROJECT_NAME", "COMPOSE_PROJECT_NAME", "CONTAINER_NAME_PREFIX",
    "IMAGE_TAG", "IMAGE_NAME", "WEB_PORT", "FRONTEND_PORT", "DB_HOST_PORT",
    "REDIS_HOST_PORT", "JAVA_PORT", "ROUTER_NAME", "MFA_WEBAUTHN_RP_NAME",
    "BACKUP_ENABLE", "TRAEFIK_ENABLE", "USE_EXTERNAL_PROXY", "DB_VOLUME_NAME",
    "MEDIA_VOLUME_NAME", "EXCEL_VOLUME_NAME", "MASTER_BASE_URL", "MASTER_PUBLIC_IP",
    "DJANGO_ALLOWED_HOSTS", "PUBLIC_ORIGIN", "DEBUG", "CSRF_TRUSTED_URLS",
    "TRAEFIK_ROUTER_RULE",
}

_ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")


def _parse_env_file(env_path):
    """Key->value dict of an existing .env file, mirroring generate_env.parse_env_file."""
    data = {}
    if not env_path.exists():
        return data
    for line in env_path.read_text(encoding="utf-8").splitlines():
        match = _ENV_LINE_RE.match(line)
        if not match:
            continue
        key, val = match.groups()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        data[key] = val
    return data


def check_env_drift(base_dir, env_name="local"):
    """Compare `.env` against the raw app_env/env_overrides/secrets keys
    `project.yaml`/`secrets.yaml` declare for `env_name`. Returns one-line
    warnings naming keys only -- values (some sit next to secrets in the same
    file) are never included, by construction: only key names are compared
    for the app_env/env_overrides path, and only key presence for secrets.
    """
    base_dir = Path(base_dir)
    project_yaml = base_dir / "project.yaml"
    env_file = base_dir / ".env"
    if not project_yaml.exists() or not env_file.exists():
        return []

    try:
        config = yaml.safe_load(project_yaml.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return []
    if not isinstance(config, dict):
        return []

    expected = dict(config.get("app_env", {}) or {})
    env_config = (config.get("environments", {}) or {}).get(env_name, {}) or {}
    expected.update(env_config.get("env_overrides", {}) or {})
    for key in _PLATFORM_COMPUTED_ENV_KEYS:
        expected.pop(key, None)

    secret_keys = set()
    secrets_path = base_dir / "secrets.yaml"
    if secrets_path.exists():
        try:
            secrets_data = yaml.safe_load(secrets_path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError, UnicodeDecodeError):
            secrets_data = {}
        secrets_def = secrets_data.get("secrets", {}) if isinstance(secrets_data, dict) else {}
        if isinstance(secrets_def, dict):
            for key, definition in secrets_def.items():
                if isinstance(definition, dict) and not definition.get("exclude_from_env", False):
                    secret_keys.add(key)

    actual = _parse_env_file(env_file)

    missing = sorted((set(expected) | secret_keys) - set(actual))
    changed = sorted(
        key for key, value in expected.items()
        if key in actual and str(actual[key]) != str(value)
    )

    warnings = []
    if missing:
        warnings.append(
            ".env is missing key(s) project.yaml/secrets.yaml now declare "
            "(values not shown): " + ", ".join(missing)
        )
    if changed:
        warnings.append(
            ".env disagrees with project.yaml for key(s) (values not shown): "
            + ", ".join(changed)
        )
    if warnings:
        warnings.append(f"fix with `generate-env --env {env_name}`")
    return warnings


_REQUIREMENT_LINE_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)"   # package name
    r"(?:\[[^\]]*\])?"                   # optional extras, ignored
    r"\s*(==|>=)\s*"
    r"([0-9][A-Za-z0-9_.\-]*)\s*$"        # version
)


def _normalize_distribution_name(name):
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _parse_requirements(requirements_path):
    """[(normalized name, operator, version), ...] for every plain `==`/`>=`
    entry. VCS/URL requirements (e.g. hram-engine's git+tag pin) have no
    installable "pinned version" to compare against dist-info and are skipped.
    """
    requirements = []
    try:
        text = requirements_path.read_text(encoding="utf-8")
    except OSError:
        return requirements
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "@" in line:
            continue
        match = _REQUIREMENT_LINE_RE.match(line)
        if not match:
            continue
        name, operator, version = match.groups()
        requirements.append((_normalize_distribution_name(name), operator, version))
    return requirements


def _find_venv_site_packages(venv_dir):
    windows_site = venv_dir / "Lib" / "site-packages"
    if windows_site.is_dir():
        return windows_site
    lib_dir = venv_dir / "lib"
    if lib_dir.is_dir():
        for entry in lib_dir.iterdir():
            candidate = entry / "site-packages"
            if candidate.is_dir():
                return candidate
    return None


def _installed_versions(site_packages):
    """{normalized name: version} read directly from *.dist-info/METADATA --
    metadata-only, no import, no subprocess (scope item 5: cheap enough to run
    unconditionally)."""
    versions = {}
    try:
        entries = list(site_packages.iterdir())
    except OSError:
        return versions
    for entry in entries:
        if not entry.is_dir() or not entry.name.endswith(".dist-info"):
            continue
        metadata_path = entry / "METADATA"
        if not metadata_path.exists():
            continue
        name = version = None
        try:
            for line in metadata_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if name is None and line.startswith("Name:"):
                    name = line.split(":", 1)[1].strip()
                elif version is None and line.startswith("Version:"):
                    version = line.split(":", 1)[1].strip()
                if name and version:
                    break
        except OSError:
            continue
        if name and version:
            versions[_normalize_distribution_name(name)] = version
    return versions


def _version_tuple(value):
    parts = []
    for segment in re.split(r"[.\-+]", value):
        digits = re.match(r"\d+", segment)
        if not digits:
            break
        parts.append(int(digits.group()))
    return tuple(parts)


def _satisfies(installed_version, operator, required_version):
    installed_t = _version_tuple(installed_version)
    required_t = _version_tuple(required_version)
    if not installed_t or not required_t:
        return True  # can't confidently compare -- never false-positive
    if operator == "==":
        return installed_t == required_t
    return installed_t >= required_t


def check_venv_drift(base_dir):
    """Compare `backend/.venv`'s installed distributions against
    `backend/requirements.txt`'s pins. Returns one-line warnings naming the
    package and both versions (package versions are not secrets). Silently
    returns [] when there is no local venv at all -- most apps on this estate
    run backend code only in Docker and have none; that is not drift."""
    base_dir = Path(base_dir)
    requirements_path = base_dir / "backend" / "requirements.txt"
    venv_dir = base_dir / "backend" / ".venv"
    if not requirements_path.exists() or not venv_dir.is_dir():
        return []

    site_packages = _find_venv_site_packages(venv_dir)
    if site_packages is None:
        return []

    requirements = _parse_requirements(requirements_path)
    if not requirements:
        return []
    installed = _installed_versions(site_packages)

    missing = []
    outdated = []
    for name, operator, required in requirements:
        installed_version = installed.get(name)
        if installed_version is None:
            missing.append(name)
        elif not _satisfies(installed_version, operator, required):
            outdated.append(f"{name} (installed {installed_version}, pinned {operator}{required})")

    warnings = []
    if missing:
        warnings.append(
            "backend/.venv is missing pinned package(s): " + ", ".join(sorted(missing))
        )
    if outdated:
        warnings.append(
            "backend/.venv has package(s) below their pin: " + ", ".join(sorted(outdated))
        )
    if warnings:
        warnings.append("fix with `pip install -r backend/requirements.txt` in that venv")
    return warnings


def collect_drift_warnings(base_dir, env_name="local"):
    """Both checks, each independently degraded to no warnings on failure --
    a check that breaks the thing it checks would be worse than no check."""
    warnings = []
    try:
        warnings.extend(check_env_drift(base_dir, env_name))
    except Exception:
        pass
    try:
        warnings.extend(check_venv_drift(base_dir))
    except Exception:
        pass
    return warnings
