#!/usr/bin/env python3
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

# --- Configuration ---
SECRETS_YAML_PATH = "secrets.yaml"
PROJECT_CONFIG_PATH = "project.yaml"
LOCAL_ENV_FILE = ".env.local"
PROTON_CLI_CMD = "pass-cli"  # Der Befehl für Proton Pass
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
        print(f"❌ Error parsing YAML file {path}: {exc}")
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
        print("❌ Error: project secret_inputs must be a mapping when defined.")
        sys.exit(1)
    return policy


def normalize_secret_source(provider, fallback_provider=None):
    """Normalize provider configuration from CLI or project policy."""
    if provider is None:
        return None
    if provider not in SECRET_SOURCE_CHOICES:
        print(f"❌ Error: invalid secret provider '{provider}'.")
        sys.exit(1)

    if fallback_provider is None:
        return provider
    if fallback_provider != "proton":
        print(f"❌ Error: unsupported fallback_provider '{fallback_provider}'. Only 'proton' is supported.")
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


def resolve_source(definition, config, secret_target=None):
    """Resolve a Proton source path from a plain source or a target template."""
    source = definition.get("source")
    if source:
        return source

    source_template = definition.get("source_template")
    if not source_template:
        return None

    target = resolve_secret_target(config, secret_target)
    if not target:
        print("   ⚠️  Cannot resolve source_template without a secret target.")
        return None

    try:
        return source_template.format(target=target)
    except KeyError as exc:
        print(f"   ⚠️  Invalid source_template placeholder {exc} in secrets.yaml.")
        return None


def is_excluded_from_env(definition):
    """Return whether a secret must not flow into generated env files."""
    return bool(definition.get("exclude_from_env", False))


def is_excluded_from_github(definition):
    """Return whether a secret must not be synced to GitHub secrets."""
    return bool(definition.get("exclude_from_github", False))


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
        print(f"   ⚠️  Target '{target}' not found in inventory '{inventory_path}'.")
        return None

    if not isinstance(target_data, dict):
        print(f"   ⚠️  Target '{target}' in '{inventory_path}' is not a mapping.")
        return None

    return target_data


def resolve_github_environment(config, secret_target=None, github_environment=None):
    """Resolve the GitHub environment for secret sync."""
    if github_environment:
        return github_environment

    target_data = get_inventory_target_data(config, secret_target)
    if target_data:
        environment_name = target_data.get("github_environment")
        if environment_name:
            return environment_name

    environment_template = config.get("github_environment_template")
    if environment_template:
        target = resolve_secret_target(config, secret_target)
        if not target:
            print("   ⚠️  Cannot resolve github_environment_template without a secret target.")
            return None
        try:
            return environment_template.format(target=target)
        except KeyError as exc:
            print(f"   ⚠️  Invalid github_environment_template placeholder {exc} in secrets.yaml.")
            return None

    return config.get("github_environment")


def validate_target_secret_map(target_name, target_values, path_label):
    """Ensure one target in a values YAML file is a flat key/value mapping."""
    if not isinstance(target_values, dict):
        print(f"❌ Error: target '{target_name}' in {path_label} must be a mapping of secret names to values.")
        sys.exit(1)


def load_values_file(path):
    """Load a YAML values file that stores secrets per target."""
    values_path = Path(path)
    if not values_path.exists():
        print(f"❌ Error: values file not found: {values_path}")
        sys.exit(1)

    data = load_yaml_file(values_path)
    if not isinstance(data, dict):
        print(f"❌ Error: values file {values_path} must contain a YAML mapping.")
        sys.exit(1)

    targets = data.get("targets")
    if targets is None:
        print(f"❌ Error: values file {values_path} must contain a top-level 'targets' mapping.")
        sys.exit(1)
    if not isinstance(targets, dict):
        print(f"❌ Error: values file {values_path} has invalid 'targets'; expected a mapping.")
        sys.exit(1)

    for target_name, target_values in targets.items():
        validate_target_secret_map(target_name, target_values, str(values_path))

    return data


def check_dependencies(target, secret_source="proton"):
    """Prüft, ob nötige CLIs vorhanden sind."""
    if target == "github" and not shutil.which("gh"):
        print("❌ Error: 'gh' CLI is required for GitHub sync.")
        sys.exit(1)

    has_proton = shutil.which(PROTON_CLI_CMD) is not None
    if secret_source in ("proton", "auto") and not has_proton:
        print(f"⚠️  Warning: '{PROTON_CLI_CMD}' not found. You can only use defaults or manual input.")
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
        print(f"   ❌ Invalid path format: {clean_path} (Expected: Vault/Item/Field)")
        return None

    vault = parts[0]
    item = parts[1]
    field = parts[2]

    try:
        print(f"   🔄 Fetching [{vault}] -> [{item}] -> {field} ...", end="", flush=True)
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
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print(" [CLI ERROR]")
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

    print(f"   ❌ Invalid {source_label} value for {key}: expected scalar or string, got {type(value).__name__}.")
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
        print("❌ Error: --values-file can only be used with secret source yaml or auto.")
        sys.exit(1)

    if secret_source == "yaml" and not values_file:
        print("❌ Error: a values file is required when secret source yaml is used.")
        sys.exit(1)

    if values_file and secret_source in ("yaml", "auto") and not secret_target:
        print(
            f"❌ Error: no secret target could be resolved for {target} sync while YAML values are enabled. "
            "Use --secret-target, project.yaml secret_inputs.target, project.yaml deploy_target, "
            "or secrets.yaml config.default_target."
        )
        sys.exit(1)


def sync_local(
    config,
    secrets_def,
    has_proton,
    secret_target=None,
    secret_source="proton",
    values_data=None,
):
    target = resolve_secret_target(config, secret_target)
    if target:
        print(f"📂 Syncing to {LOCAL_ENV_FILE} for target '{target}' ...")
    else:
        print(f"📂 Syncing to {LOCAL_ENV_FILE} ...")

    output_lines = ["# Auto-generated local secrets from secrets.yaml"]

    for key, definition in secrets_def.items():
        if is_excluded_from_env(definition):
            print(f"   ⏭️  {key}: Skipping (exclude_from_env)")
            continue

        dev_default = definition.get("dev_default")
        value = None

        if dev_default is not None:
            value = str(dev_default)
            print(f"   ✅ {key}: Using dev_default")
        else:
            source = resolve_source(definition, config, secret_target=target)
            fetched, resolved_from = resolve_secret_value(
                key,
                source,
                has_proton,
                secret_source,
                secret_target=target,
                values_data=values_data,
            )
            if fetched is not None:
                value = fetched
                if resolved_from == "yaml":
                    print(f"   ✅ {key}: Using YAML values file")

            if value is None:
                print(f"   ⚠️  {key}: No default and configured secret lookup failed.")
                value = input(f"      Please enter value for {key}: ").strip()

        output_lines.append(f"{key}={value}")

    with open(LOCAL_ENV_FILE, "w", encoding="utf-8") as handle:
        handle.write("\n".join(output_lines))
        handle.write("\n")

    print(f"✅ Successfully wrote {LOCAL_ENV_FILE}")


def collect_github_secret_values(config, secrets_def, has_proton, secret_target, secret_source, values_data):
    """Resolve all GitHub secret values before any write when yaml input is active."""
    planned = []
    missing = []

    for key, definition in secrets_def.items():
        if is_excluded_from_github(definition):
            print(f"   ⏭️  Skipping {key}: exclude_from_github is set.")
            continue

        source = resolve_source(definition, config, secret_target=secret_target)
        if secret_source == "proton" and not source:
            print(f"   ⚠️  Skipping {key}: No resolvable source defined in YAML.")
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
                print(f"   ❌ Missing {key} in local YAML values for target '{secret_target}'.")
            else:
                print(f"   ❌ Failed to fetch {key} from configured secret sources.")
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
):
    target_repo = config.get("target_repo")
    if not target_repo:
        print("❌ Error: 'config.target_repo' missing in secrets.yaml")
        sys.exit(1)

    environment_name = resolve_github_environment(
        config,
        secret_target=secret_target,
        github_environment=github_environment,
    )

    if environment_name:
        print(f"☁️  Syncing to GitHub Environment: {target_repo}/{environment_name}")
    else:
        print(f"☁️  Syncing to GitHub Repo: {target_repo}")

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
        )
        if missing_keys:
            print("")
            print(
                "❌ Error: unable to resolve all GitHub secrets before writing: "
                + ", ".join(missing_keys)
            )
            sys.exit(1)
    else:
        planned_values = []
        for key, definition in secrets_def.items():
            if is_excluded_from_github(definition):
                print(f"   ⏭️  Skipping {key}: exclude_from_github is set.")
                continue

            source = resolve_source(definition, config, secret_target=secret_target)
            if secret_source == "proton" and not source:
                print(f"   ⚠️  Skipping {key}: No resolvable source defined in YAML.")
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
                print(f"   ❌ Failed to fetch {key} from configured secret sources.")
                continue
            planned_values.append((key, value, resolved_from))

    for key, value, resolved_from in planned_values:
        print(f"   🚀 Pushing {key} to GitHub...", end="", flush=True)
        cmd = ["gh", "secret", "set", key, "--repo", target_repo]
        if environment_name:
            cmd.extend(["--env", environment_name])
        proc = subprocess.run(cmd, input=value, text=True, capture_output=True)

        if proc.returncode == 0:
            source_suffix = f" via {resolved_from}" if resolved_from else ""
            print(f" [OK{source_suffix}]")
        else:
            print(f" [ERROR]\n     {proc.stderr.strip()}")


def main():
    parser = argparse.ArgumentParser(description="Sync secrets from Proton Pass to Local or GitHub.")
    parser.add_argument("--target", choices=["local", "github"], required=True, help="Destination for secrets")
    parser.add_argument("--secret-target", help="Target placeholder override for target-based secrets")
    parser.add_argument(
        "--secret-source",
        choices=SECRET_SOURCE_CHOICES,
        help="Override secret provider. When omitted, project.yaml secret_inputs or Proton defaults apply.",
    )
    parser.add_argument("--values-file", help="Override local YAML file with target-specific secret values")
    parser.add_argument("--github-environment", help="Optional GitHub environment override for GitHub sync")
    args = parser.parse_args()

    project_config, project_config_path = load_project_config()

    if not Path(SECRETS_YAML_PATH).exists():
        print(f"❌ Error: {SECRETS_YAML_PATH} not found.")
        sys.exit(1)

    try:
        with open(SECRETS_YAML_PATH, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        print(f"❌ Error parsing YAML: {exc}")
        sys.exit(1)

    config = data.get("config", {})
    secrets_def = data.get("secrets", {})
    if not secrets_def:
        print("❌ Error: No 'secrets' block found in YAML.")
        sys.exit(1)

    effective = resolve_effective_settings(
        config,
        cli_secret_source=args.secret_source,
        cli_values_file=args.values_file,
        cli_secret_target=args.secret_target,
        project_config=project_config,
        project_config_path=project_config_path,
    )
    validate_effective_settings(args.target, effective)

    has_proton = check_dependencies(args.target, secret_source=effective["secret_source"])
    values_data = None
    if effective["values_file"] and effective["secret_source"] in ("yaml", "auto"):
        values_data = load_values_file(effective["values_file"])

    if args.target == "local":
        sync_local(
            config,
            secrets_def,
            has_proton,
            secret_target=effective["secret_target"],
            secret_source=effective["secret_source"],
            values_data=values_data,
        )
    else:
        sync_github(
            config,
            secrets_def,
            has_proton,
            secret_target=effective["secret_target"],
            github_environment=args.github_environment,
            secret_source=effective["secret_source"],
            values_data=values_data,
        )


if __name__ == "__main__":
    main()
