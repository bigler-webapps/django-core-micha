#!/usr/bin/env python3
import argparse
import json
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

# --- Configuration ---
SECRETS_YAML_PATH = "secrets.yaml"
PROJECT_CONFIG_PATH = "project.yaml"
PROTON_CLI_CMD = "pass-cli"  # Der Befehl für Proton Pass

_FETCH_MAX_RETRIES = 3
_FETCH_BACKOFF_BASE = 1.0  # seconds, exponential: 1 / 2 / 4 + jitter
SECRET_SOURCE_CHOICES = ("proton", "yaml", "auto")


def load_yaml_file(path):
    """Load a YAML file and return a dictionary."""
    file_path = Path(path)
    if not file_path.exists():
        return {}

    try:
        with file_path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        print(f"Error parsing YAML file {path}: {exc}")
        sys.exit(1)


def resolve_yaml_path(path):
    """Resolve .yml/.yaml fallback variants for one config path."""
    candidate = Path(path)
    if candidate.exists():
        return candidate

    if candidate.suffix == ".yml":
        alternate = candidate.with_suffix(".yaml")
        if alternate.exists():
            return alternate
    elif candidate.suffix == ".yaml":
        alternate = candidate.with_suffix(".yml")
        if alternate.exists():
            return alternate

    return None


def load_project_config(path=PROJECT_CONFIG_PATH):
    """Load project config when present and return (data, resolved_path)."""
    resolved_path = resolve_yaml_path(path)
    if not resolved_path:
        return {}, None
    return load_yaml_file(resolved_path), resolved_path


def get_secret_inputs_policy(project_config):
    """Return optional secret policy from project.yaml."""
    policy = project_config.get("secret_inputs", {})
    if policy is None:
        return {}
    if not isinstance(policy, dict):
        print("Error: project secret_inputs must be a mapping when defined.")
        sys.exit(1)
    return policy


def normalize_secret_source(provider, fallback_provider=None):
    """Normalize provider configuration from CLI or project policy."""
    if provider is None:
        return None
    if provider not in SECRET_SOURCE_CHOICES:
        print(f"Error: invalid secret provider '{provider}'.")
        sys.exit(1)

    if fallback_provider is None:
        return provider
    if fallback_provider != "proton":
        print(f"Error: unsupported fallback_provider '{fallback_provider}'. Only 'proton' is supported.")
        sys.exit(1)

    if provider == "yaml":
        return "auto"
    return provider


def resolve_values_file_path(values_file, project_config_path=None):
    """Resolve a local values file path relative to project.yaml when needed."""
    if not values_file:
        return None

    values_path = Path(values_file)
    if values_path.is_absolute() or not project_config_path:
        return values_path
    return project_config_path.parent / values_path


def resolve_effective_settings(
    secrets_config,
    cli_secret_source=None,
    cli_values_file=None,
    cli_secret_target=None,
    project_config=None,
    project_config_path=None,
):
    """Resolve provider, values file, and target with CLI > project.yaml > existing defaults."""
    project_config = project_config or {}
    policy = get_secret_inputs_policy(project_config)

    policy_source = normalize_secret_source(
        policy.get("provider"),
        fallback_provider=policy.get("fallback_provider"),
    )
    secret_source = normalize_secret_source(cli_secret_source) or policy_source or "proton"

    raw_values_file = cli_values_file or policy.get("values_file")
    values_file = resolve_values_file_path(raw_values_file, project_config_path=project_config_path)

    secret_target = (
        cli_secret_target
        or policy.get("target")
        or project_config.get("deploy_target")
        or secrets_config.get("default_target")
    )
    if secret_target is not None:
        secret_target = str(secret_target).strip() or None

    return {
        "secret_source": secret_source,
        "values_file": values_file,
        "secret_target": secret_target,
    }


def resolve_secret_target(config, secret_target=None):
    """Resolve the active target used in source templates."""
    return secret_target or config.get("default_target")


def resolve_server_from_project(project_config, secret_target):
    """Look up environments[<target>].server from project.yaml; return None if absent."""
    if not project_config or not secret_target:
        return None
    environments = project_config.get("environments", {})
    if not isinstance(environments, dict):
        return None
    env_data = environments.get(secret_target)
    if not isinstance(env_data, dict):
        return None
    server = env_data.get("server")
    if server is None:
        return None
    return str(server).strip() or None


def resolve_source(definition, config, secret_target=None, project_config=None):
    """Resolve a Proton source path from a plain source or a target template.

    Supports two placeholders in source_template:
      - {target}: the literal secret_target value (e.g. 'production', 'staging')
      - {server}: server name resolved via project.yaml environments[<target>].server
                  Used when secrets should reference a centrally-managed server
                  entry instead of duplicating per-app/per-env.
    """
    source = definition.get("source")
    if source:
        return source

    source_template = definition.get("source_template")
    if not source_template:
        return None

    target = resolve_secret_target(config, secret_target)
    if not target:
        print("    Cannot resolve source_template without a secret target.")
        return None

    substitutions = {"target": target}
    if "{server}" in source_template:
        server = resolve_server_from_project(project_config, target)
        if not server:
            print(
                f"    source_template uses {{server}} but project.yaml has no "
                f"environments['{target}'].server entry."
            )
            return None
        substitutions["server"] = server

    try:
        return source_template.format(**substitutions)
    except KeyError as exc:
        print(f"    Invalid source_template placeholder {exc} in secrets.yaml.")
        return None


def is_excluded_from_env(definition):
    """Return whether a secret must not flow into generated env files."""
    return bool(definition.get("exclude_from_env", False))


def is_excluded_from_github(definition):
    """Return whether a secret must not be synced to GitHub secrets."""
    return bool(definition.get("exclude_from_github", False))


VALID_TARGET_SCOPES = ("env", "repo")


def get_target_scope(definition, key=None):
    """Return per-secret push scope: 'env' (default) or 'repo'.

    When 'repo', the secret is pushed at repository level regardless of any
    github_environment / inventory / project.yaml env-resolution. Useful for
    cross-cutting secrets that share a single value across all environments
    (e.g. shared encryption keys, API tokens used by every workflow).
    """
    scope = definition.get("target_scope", "env")
    if scope not in VALID_TARGET_SCOPES:
        label = f" for secret '{key}'" if key else ""
        print(f"Error: invalid target_scope '{scope}'{label} (allowed: env, repo).")
        sys.exit(1)
    return scope


def get_inventory_target_data(config, secret_target=None):
    """Load target metadata from the configured inventory file."""
    inventory_path = config.get("inventory_path")
    target = resolve_secret_target(config, secret_target)

    if not inventory_path or not target:
        return None

    inventory = load_yaml_file(inventory_path)
    targets = inventory.get("targets", {})
    target_data = targets.get(target)

    if target_data is None:
        print(f"    Target '{target}' not found in inventory '{inventory_path}'.")
        return None

    if not isinstance(target_data, dict):
        print(f"    Target '{target}' in '{inventory_path}' is not a mapping.")
        return None

    return target_data


def resolve_github_environment(config, secret_target=None, github_environment=None, project_config=None):
    """Resolve the GitHub environment for secret sync.

    Resolution precedence:
      1. explicit github_environment arg (CLI --github-environment)
      2. inventory target's github_environment field (when inventory_path is set)
      3. project.yaml environments[<secret_target>] (when use_project_yaml is opted-in)
      4. github_environment_template with {target} substitution
      5. static config.github_environment

    Returns None when no source produces an env name caller falls back to repo-level sync.
    """
    if github_environment:
        return github_environment

    target_data = get_inventory_target_data(config, secret_target)
    if target_data:
        environment_name = target_data.get("github_environment")
        if environment_name:
            return environment_name

    # Opt-in: when secrets.yaml config has use_project_yaml=true AND project.yaml
    # declares the secret_target as an environment, use the secret_target value
    # itself as the GH environment name (semantic naming).
    if config.get("use_project_yaml") and project_config and secret_target:
        environments = project_config.get("environments", {})
        if isinstance(environments, dict) and secret_target in environments:
            return secret_target

    environment_template = config.get("github_environment_template")
    if environment_template:
        target = resolve_secret_target(config, secret_target)
        if not target:
            print("    Cannot resolve github_environment_template without a secret target.")
            return None
        try:
            return environment_template.format(target=target)
        except KeyError as exc:
            print(f"    Invalid github_environment_template placeholder {exc} in secrets.yaml.")
            return None

    return config.get("github_environment")


def validate_target_secret_map(target_name, target_values, path_label):
    """Ensure one target in a values YAML file is a flat key/value mapping."""
    if not isinstance(target_values, dict):
        print(f"Error: target '{target_name}' in {path_label} must be a mapping of secret names to values.")
        sys.exit(1)


def load_values_file(path):
    """Load a YAML values file that stores secrets per target."""
    values_path = Path(path)
    if not values_path.exists():
        print(f"Error: values file not found: {values_path}")
        sys.exit(1)

    data = load_yaml_file(values_path)
    if not isinstance(data, dict):
        print(f"Error: values file {values_path} must contain a YAML mapping.")
        sys.exit(1)

    targets = data.get("targets")
    if targets is None:
        print(f"Error: values file {values_path} must contain a top-level 'targets' mapping.")
        sys.exit(1)
    if not isinstance(targets, dict):
        print(f"Error: values file {values_path} has invalid 'targets'; expected a mapping.")
        sys.exit(1)

    for target_name, target_values in targets.items():
        validate_target_secret_map(target_name, target_values, str(values_path))

    return data


def check_dependencies(target, secret_source="proton"):
    """Prüft, ob nötige CLIs vorhanden sind."""
    if target == "github" and not shutil.which("gh"):
        print("Error: 'gh' CLI is required for GitHub sync.")
        sys.exit(1)

    has_proton = shutil.which(PROTON_CLI_CMD) is not None
    if secret_source in ("proton", "auto") and not has_proton:
        print(f" Warning: '{PROTON_CLI_CMD}' not found. You can only use defaults or manual input.")
    return has_proton


def get_proton_secret(proton_path):
    """
    Holt ein Secret via Proton Pass CLI.
    Format: proton://Vault Name/Item Name/Field
    Robust gegen "Hidden" vs "Text" Felder.
    """
    if not proton_path or not proton_path.startswith("proton://"):
        return None

    clean_path = proton_path.replace("proton://", "")
    parts = clean_path.split("/")

    if len(parts) < 3:
        print(f"   Invalid path format: {clean_path} (Expected: Vault/Item/Field)")
        return None

    vault = parts[0]
    item = parts[1]
    field = parts[2]

    try:
        print(f"   Fetching [{vault}] -> [{item}] -> {field} ...", end="", flush=True)
        cmd = [
            PROTON_CLI_CMD,
            "item",
            "view",
            "--vault-name",
            vault,
            "--item-title",
            item,
            "--output",
            "json",
        ]

        result = None
        for attempt in range(1, _FETCH_MAX_RETRIES + 1):
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                break
            if attempt < _FETCH_MAX_RETRIES:
                delay = _FETCH_BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                print(f" [retry {attempt}/{_FETCH_MAX_RETRIES}]", end="", flush=True)
                time.sleep(delay)

        if result is None or result.returncode != 0:
            stderr_hint = (result.stderr or "").strip() if result is not None else ""
            print(f" [CLI ERROR]{(': ' + stderr_hint) if stderr_hint else ''}")
            return None

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            print(" [JSON ERROR]")
            return None

        val = None
        item_root = data.get("item", data)
        content_root = item_root.get("content", {})

        if "extra_fields" not in content_root and "extra_fields" in item_root:
            content_root = item_root

        if field == "password":
            val = content_root.get("password")
        elif field == "username":
            val = content_root.get("username")
        elif field == "note":
            val = content_root.get("note")
        elif field == "url":
            urls = content_root.get("urls", [])
            val = urls[0] if urls else None

        if val is None:
            extra_fields = content_root.get("extra_fields", [])
            for extra_field in extra_fields:
                if extra_field.get("name", "").lower() != field.lower():
                    continue

                field_content = extra_field.get("content", {})
                if isinstance(field_content, dict):
                    if "Hidden" in field_content:
                        val = field_content["Hidden"]
                    elif "Text" in field_content:
                        val = field_content["Text"]
                    elif "value" in field_content:
                        val = field_content["value"]
                    elif "hidden" in field_content:
                        val = field_content["hidden"]
                    elif "text" in field_content:
                        val = field_content["text"]
                else:
                    val = str(field_content)
                break

        if val is not None:
            print(" [OK]")
            return val

        print(f" [FIELD '{field}' NOT FOUND]")
        return None

    except Exception as exc:
        print(f" [EXCEPTION: {exc}]")
        return None


def normalize_secret_value(key, value, source_label):
    """Return a secret as a string while rejecting nested YAML structures."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)

    print(f"   Invalid {source_label} value for {key}: expected scalar or string, got {type(value).__name__}.")
    return None


def get_yaml_secret(key, values_data, secret_target):
    """Fetch one secret from a local values YAML file."""
    if not values_data or not secret_target:
        return None

    targets = values_data.get("targets", {})
    target_values = targets.get(secret_target)
    if target_values is None:
        return None

    validate_target_secret_map(secret_target, target_values, "values file")
    return normalize_secret_value(key, target_values.get(key), "YAML")


def resolve_secret_value(key, source, has_proton, secret_source, secret_target=None, values_data=None):
    """Resolve one secret value from the selected provider."""
    if secret_source == "yaml":
        return get_yaml_secret(key, values_data, secret_target), "yaml"

    if secret_source == "auto":
        yaml_value = get_yaml_secret(key, values_data, secret_target)
        if yaml_value is not None:
            return yaml_value, "yaml"

    if has_proton and source:
        proton_value = get_proton_secret(source)
        if proton_value is not None:
            return proton_value, "proton"

    return None, None


def validate_effective_settings(target, settings):
    """Validate effective settings after project.yaml and defaults are applied."""
    secret_source = settings["secret_source"]
    values_file = settings["values_file"]
    secret_target = settings["secret_target"]

    if values_file and secret_source == "proton":
        print("Error: --values-file can only be used with secret source yaml or auto.")
        sys.exit(1)

    if secret_source == "yaml" and not values_file:
        print("Error: a values file is required when secret source yaml is used.")
        sys.exit(1)

    if values_file and secret_source in ("yaml", "auto") and not secret_target:
        print(
            f"Error: no secret target could be resolved for {target} sync while YAML values are enabled. "
            "Use --secret-target, project.yaml secret_inputs.target, project.yaml deploy_target, "
            "or secrets.yaml config.default_target."
        )
        sys.exit(1)


def collect_github_secret_values(config, secrets_def, has_proton, secret_target, secret_source, values_data, project_config=None):
    """Resolve all GitHub secret values before any write when yaml input is active."""
    planned = []
    missing = []

    for key, definition in secrets_def.items():
        if is_excluded_from_github(definition):
            print(f"    Skipping {key}: exclude_from_github is set.")
            continue

        source = resolve_source(definition, config, secret_target=secret_target, project_config=project_config)
        if secret_source == "proton" and not source:
            print(f"    Skipping {key}: No resolvable source defined in YAML.")
            continue

        value, resolved_from = resolve_secret_value(
            key,
            source,
            has_proton,
            secret_source,
            secret_target=secret_target,
            values_data=values_data,
        )

        if value is None:
            if secret_source == "yaml":
                print(f"   Missing {key} in local YAML values for target '{secret_target}'.")
            else:
                print(f"   Failed to fetch {key} from configured secret sources.")
            missing.append(key)
            continue

        planned.append((key, value, resolved_from))

    return planned, missing


def sync_github(
    config,
    secrets_def,
    has_proton,
    secret_target=None,
    github_environment=None,
    secret_source="proton",
    values_data=None,
    project_config=None,
):
    target_repo = config.get("target_repo")
    if not target_repo:
        print("Error: 'config.target_repo' missing in secrets.yaml")
        sys.exit(1)

    environment_name = resolve_github_environment(
        config,
        secret_target=secret_target,
        github_environment=github_environment,
        project_config=project_config,
    )

    if environment_name:
        print(f" Syncing to GitHub Environment: {target_repo}/{environment_name}")
    else:
        print(f" Syncing to GitHub Repo: {target_repo}")

    if secret_source == "proton":
        print("   (Fetching REAL secrets from Proton - ignoring defaults)")
    elif secret_source == "yaml":
        print("   (Fetching REAL secrets from local YAML values - ignoring defaults)")
    else:
        print("   (Fetching REAL secrets from local YAML values first, then Proton - ignoring defaults)")

    preflight_required = secret_source == "yaml" or (secret_source == "auto" and values_data is not None)

    if preflight_required:
        planned_values, missing_keys = collect_github_secret_values(
            config,
            secrets_def,
            has_proton,
            secret_target,
            secret_source,
            values_data,
            project_config=project_config,
        )
        if missing_keys:
            print("")
            print(
                "Error: unable to resolve all GitHub secrets before writing: "
                + ", ".join(missing_keys)
            )
            sys.exit(1)
    else:
        planned_values = []
        for key, definition in secrets_def.items():
            if is_excluded_from_github(definition):
                print(f"    Skipping {key}: exclude_from_github is set.")
                continue

            source = resolve_source(definition, config, secret_target=secret_target, project_config=project_config)
            if secret_source == "proton" and not source:
                print(f"    Skipping {key}: No resolvable source defined in YAML.")
                continue

            value, resolved_from = resolve_secret_value(
                key,
                source,
                has_proton,
                secret_source,
                secret_target=secret_target,
                values_data=values_data,
            )
            if value is None:
                print(f"   Failed to fetch {key} from configured secret sources.")
                continue
            planned_values.append((key, value, resolved_from))

    failed_keys = []
    for key, value, resolved_from in planned_values:
        definition = secrets_def.get(key, {})
        scope = get_target_scope(definition, key=key)
        use_env = bool(environment_name) and scope == "env"

        scope_label = f"env {environment_name}" if use_env else "repo (forced)" if scope == "repo" else "repo"
        print(f"   Pushing {key} to GitHub [{scope_label}]...", end="", flush=True)

        cmd = ["gh", "secret", "set", key, "--repo", target_repo]
        if use_env:
            cmd.extend(["--env", environment_name])
        proc = None
        for attempt in range(1, _FETCH_MAX_RETRIES + 1):
            proc = subprocess.run(cmd, input=value, text=True, capture_output=True)
            if proc.returncode == 0:
                break
            if attempt < _FETCH_MAX_RETRIES:
                delay = _FETCH_BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                print(f" [retry {attempt}/{_FETCH_MAX_RETRIES}]", end="", flush=True)
                time.sleep(delay)

        if proc is not None and proc.returncode == 0:
            source_suffix = f" via {resolved_from}" if resolved_from else ""
            print(f" [OK{source_suffix}]")
        else:
            failed_keys.append(key)
            stderr_hint = (proc.stderr or "").strip() if proc is not None else ""
            print(f" [ERROR]\n     {stderr_hint}")

    if failed_keys:
        print(
            f"Error: failed to push {len(failed_keys)} secret(s) after "
            f"{_FETCH_MAX_RETRIES} attempts: " + ", ".join(failed_keys)
        )
        sys.exit(1)


_BARE_SERVER_TARGETS = ("staging", "production")


def _resolve_bare_targets(parser, config, project_config):
    """Resolve the bare-mode target list, explicit precedence (SEC-3).

    Returns (bare_targets, derived_from_infra_servers) — the second element tells
    the caller whether the list came from project.yaml infra.servers (derivation,
    branch 2) as opposed to an explicit override or the built-in default (branches
    1 and 3). That distinction matters downstream: verification is fatal only for a
    derived list — infra.servers can itself be incomplete (it omitted `runners`),
    which is exactly the class of bug this WO fixes. An override or the built-in
    default reflects an existing consumer's current, already-working setup; making
    a stale environment there suddenly fatal would break repos SEC-3 never touched
    (confirmed: hram has a third GitHub Environment, `hram-webapp`, not in its
    staging/production default — a real, pre-existing gap, but not this WO's to
    turn into a hard failure without that consumer's own review).

      1. config.bare_server_targets, if set — someone's explicit escape hatch, wins outright.
      2. else, project.yaml infra.servers keys, in declaration order — derived, no hand
         maintenance; this is what closed the OPS-2 gap (a server added to infra.servers
         is now a bare-mode target without a second edit).
      3. else, the built-in _BARE_SERVER_TARGETS default — app repos have no infra.servers
         and must keep working exactly as they do today.
    """
    bare_targets = config.get("bare_server_targets")
    if bare_targets is not None:
        if not (
            isinstance(bare_targets, (list, tuple))
            and bare_targets
            and all(isinstance(t, str) and t.strip() for t in bare_targets)
        ):
            parser.error(
                "config.bare_server_targets must be a non-empty list of non-empty"
                " strings (server target names)."
            )
        return list(bare_targets), False

    infra = (project_config or {}).get("infra")
    if isinstance(infra, dict) and "servers" in infra:
        servers = infra["servers"]
        if not (isinstance(servers, dict) and servers):
            parser.error(
                "project.yaml infra.servers must be a non-empty mapping of server name ->"
                " config, to derive bare-mode targets from."
            )
        return list(servers.keys()), True

    return list(_BARE_SERVER_TARGETS), False


def _fetch_github_environment_names(target_repo):
    """Return the set of GitHub Environment names configured on *target_repo*.

    Returns None (not an empty set) when the names could not be determined at all
    (gh missing, not authenticated, API error) — callers must treat that as "cannot
    verify" and warn, not as "repo has zero environments" and fail.
    """
    try:
        proc = subprocess.run(
            ["gh", "api", f"repos/{target_repo}/environments", "--jq", ".environments[].name"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def _verify_bare_targets_against_environments(parser, bare_targets, target_repo, *, fatal):
    """Check the resolved bare targets against the repo's actual GitHub Environments,
    in both directions (SEC-3 — this is the half that actually prevents recurrence: a
    registry we derive from can itself be wrong, as `infra.servers` already proved by
    omitting `runners`).

    *fatal* (True only for a project.yaml infra.servers-derived list, see
    _resolve_bare_targets) decides the consequence of a mismatch: a derived list can
    itself be incomplete, so a gap there is exactly the bug class this WO fixes and
    must hard-stop the run. An explicit override or the built-in default reflects an
    existing consumer's current, already-working configuration — a mismatch there is
    surfaced as a loud, unmissable warning instead, so newly-added verification alone
    cannot break another repo's routine sync outright; that repo's own owner decides
    when to act on it.

    Cannot-verify (gh missing/unauthenticated/API error) is always a warning, never a
    failure, regardless of *fatal* — a transient tooling gap must not block every sync.
    """
    actual = _fetch_github_environment_names(target_repo)
    if actual is None:
        print(
            f"    Warning: could not list GitHub Environments for {target_repo} to verify"
            " bare-mode targets against (gh api failed) — proceeding without verification."
        )
        return

    resolved = set(bare_targets)
    envs_with_no_target = sorted(actual - resolved)
    targets_with_no_env = sorted(resolved - actual)
    if not envs_with_no_target and not targets_with_no_env:
        return

    lines = [
        f"bare-mode target list does not match GitHub Environments for {target_repo}."
    ]
    if envs_with_no_target:
        lines.append(
            "  Environment(s) with no bare-mode target (would silently never receive"
            f" secrets): {', '.join(envs_with_no_target)}"
        )
    if targets_with_no_env:
        lines.append(
            "  Bare-mode target(s) with no matching environment (typo, or a decommissioned"
            f" server): {', '.join(targets_with_no_env)}"
        )
    message = "\n".join(lines)

    if fatal:
        parser.error(f"Error: {message}")
    else:
        print(f"\n{'!' * 60}\n    WARNING: {message}\n{'!' * 60}\n")


def _do_server_sync(
    secret_target_name,
    config,
    secrets_def,
    project_config,
    project_config_path,
    *,
    cli_secret_source=None,
    cli_values_file=None,
    github_environment=None,
):
    """Run one GitHub-secrets sync pass for *secret_target_name*.

    Raises SystemExit on validation or write errors — bare-mode callers catch it
    to abort the sequence and propagate the exit code.
    """
    effective = resolve_effective_settings(
        config,
        cli_secret_source=cli_secret_source,
        cli_values_file=cli_values_file,
        cli_secret_target=secret_target_name,
        project_config=project_config,
        project_config_path=project_config_path,
    )
    validate_effective_settings("github", effective)
    has_proton = check_dependencies("github", secret_source=effective["secret_source"])
    values_data = None
    if effective["values_file"] and effective["secret_source"] in ("yaml", "auto"):
        values_data = load_values_file(effective["values_file"])
    sync_github(
        config,
        secrets_def,
        has_proton,
        secret_target=effective["secret_target"],
        github_environment=github_environment,
        secret_source=effective["secret_source"],
        values_data=values_data,
        project_config=project_config,
    )


def _write_local_env(secret_target, secret_source, values_file, project_config):
    """Write the full local ``.env`` via the generate-env composition.

    Returns False when project.yaml has no local environment, so callers can
    decide whether that is fatal or a graceful no-op.
    """
    if "local" not in (project_config or {}).get("environments", {}):
        return False

    # Keep .env a pure, Proton-regenerable derivative: generate-env overwrites
    # it from project.yaml plus secret inputs instead of preserving hand edits,
    # so no secret can live only in an uncommitted local .env file.
    # This lazy import avoids generate_env.py's module-level sync_secrets import.
    from django_core_micha.scripts import generate_env as generate_env_module

    generate_env_module.generate_env(
        "local",
        config_path=PROJECT_CONFIG_PATH,
        output_path=".env",
        secret_target=secret_target,
        secret_source=secret_source,
        values_file=values_file,
    )
    return True


def _sync_all_github_targets(parser, config, secrets_def, project_config, project_config_path, args):
    """Sync every configured bare-mode GitHub target in sequence."""
    bare_targets, derived_from_infra_servers = _resolve_bare_targets(parser, config, project_config)

    target_repo = config.get("target_repo")
    if target_repo:
        _verify_bare_targets_against_environments(
            parser, bare_targets, target_repo, fatal=derived_from_infra_servers
        )

    for target_name in bare_targets:
        print(f"\n{'-' * 60}")
        print(f"  sync-secrets — target: {target_name}")
        print(f"{'-' * 60}\n")
        try:
            _do_server_sync(
                target_name,
                config,
                secrets_def,
                project_config,
                project_config_path,
                cli_secret_source=args.secret_source,
                cli_values_file=args.values_file,
                github_environment=args.github_environment,
            )
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            print(f"\nError: {target_name} sync failed (exit {code}). Aborting remaining targets.")
            sys.exit(code)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Sync secrets to a local .env file or to GitHub Environment Secrets."
        " With no arguments, writes local .env when configured and syncs GitHub secrets"
        " for each bare-mode target in sequence (default staging then production;"
        " override with config.bare_server_targets in secrets.yaml)."
    )
    destination_group = parser.add_mutually_exclusive_group(required=False)
    destination_group.add_argument(
        "--local",
        action="store_true",
        help="Write resolved secrets to a local .env file (replaces the old `--target local`).",
    )
    destination_group.add_argument(
        "--server",
        action="store_true",
        help="Push resolved secrets only to the GitHub target named by --secret-target.",
    )
    destination_group.add_argument(
        "--github",
        "--remote",
        action="store_true",
        dest="github_all",
        help=(
            "Sync GitHub secrets for every config.bare_server_targets environment (no local write). "
            "Same GitHub-only behaviour as a bare invocation, minus the local .env write."
        ),
    )
    destination_group.add_argument(
        "--staging",
        action="store_true",
        help="Shorthand for --server --secret-target staging.",
    )
    destination_group.add_argument(
        "--production",
        action="store_true",
        help="Shorthand for --server --secret-target production.",
    )
    parser.add_argument("--secret-target", help="Target placeholder override for target-based secrets")
    parser.add_argument(
        "--secret-source",
        choices=SECRET_SOURCE_CHOICES,
        help="Override secret provider. When omitted, project.yaml secret_inputs or Proton defaults apply.",
    )
    parser.add_argument("--values-file", help="Override local YAML file with target-specific secret values")
    parser.add_argument("--github-environment", help="Optional GitHub environment override for GitHub sync")
    args = parser.parse_args(argv)

    project_config, project_config_path = load_project_config()

    if not Path(SECRETS_YAML_PATH).exists():
        print(f"Error: {SECRETS_YAML_PATH} not found.")
        sys.exit(1)

    try:
        with open(SECRETS_YAML_PATH, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        print(f"Error parsing YAML: {exc}")
        sys.exit(1)

    config = data.get("config", {})
    secrets_def = data.get("secrets", {})
    if not secrets_def:
        print("Error: No 'secrets' block found in YAML.")
        sys.exit(1)

    # Resolve destination and effective CLI secret target from flags.
    if args.staging:
        if args.secret_target:
            parser.error("--staging already implies --secret-target staging; do not combine with --secret-target.")
        destination = "github"
        effective_cli_target = "staging"
    elif args.production:
        if args.secret_target:
            parser.error("--production already implies --secret-target production; do not combine with --secret-target.")
        destination = "github"
        effective_cli_target = "production"
    elif args.server:
        destination = "github"
        effective_cli_target = args.secret_target
    elif args.github_all:
        if args.secret_target:
            parser.error("--github/--remote already syncs all bare_server_targets; do not combine with --secret-target.")
        destination = "github_all"
        effective_cli_target = None
    elif args.local:
        destination = "local"
        effective_cli_target = args.secret_target
    else:
        if args.secret_target:
            parser.error("--secret-target requires a destination flag (--server, --staging, or --production).")
        destination = None  # bare mode: both targets in sequence

    if destination is None:
        wrote = _write_local_env(None, args.secret_source, args.values_file, project_config)
        if not wrote:
            print(f"   (No 'local' environment in {PROJECT_CONFIG_PATH} — skipping local .env write.)")
        _sync_all_github_targets(parser, config, secrets_def, project_config, project_config_path, args)
        return

    if destination == "github_all":
        _sync_all_github_targets(parser, config, secrets_def, project_config, project_config_path, args)
        return

    if destination == "github":
        _do_server_sync(
            effective_cli_target,
            config,
            secrets_def,
            project_config,
            project_config_path,
            cli_secret_source=args.secret_source,
            cli_values_file=args.values_file,
            github_environment=args.github_environment,
        )
        return

    wrote = _write_local_env(effective_cli_target, args.secret_source, args.values_file, project_config)
    if not wrote:
        print(f"Error: no 'local' environment defined in {PROJECT_CONFIG_PATH} — cannot write .env.")
        sys.exit(1)


if __name__ == "__main__":
    main()
