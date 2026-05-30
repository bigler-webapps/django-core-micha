import argparse
import json
import os
import re
import sys

import yaml

from django_core_micha.scripts import sync_secrets


def parse_env_file(path):
    """Liest eine .env Datei in ein Dictionary ein."""
    if not os.path.exists(path):
        return {}
    data = {}
    env_regex = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            match = env_regex.search(line)
            if match:
                key, val = match.groups()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                data[key] = val
    return data


def write_env_file(path, lines):
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
            handle.write("\n")
        print(f"Successfully wrote {path}")
    except Exception as exc:
        print(f"Error writing file: {exc}")
        sys.exit(1)


def load_secrets_metadata(path="secrets.yaml"):
    """Load secrets.yaml metadata and config."""
    if not os.path.exists(path):
        return {}, {}

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        print(f"   [WARN] Error parsing {path}: {exc}")
        return {}, {}

    return data.get("config", {}) or {}, data.get("secrets", {}) or {}


def load_inputs_from_secrets_yaml(
    path="secrets.yaml",
    project_config=None,
    project_config_path=None,
    secret_target=None,
    secret_source=None,
    values_file=None,
):
    """Load local secret inputs directly from secrets.yaml using the same provider rules as sync-secrets."""
    if not os.path.exists(path):
        print(f"   [WARN] {path} not found (no secrets loaded)")
        return {}

    secrets_config, secrets_def = load_secrets_metadata(path)
    if not isinstance(secrets_def, dict) or not secrets_def:
        print(f"   [WARN] No 'secrets' block found in {path}")
        return {}

    effective = sync_secrets.resolve_effective_settings(
        secrets_config,
        cli_secret_source=secret_source,
        cli_values_file=values_file,
        cli_secret_target=secret_target,
        project_config=project_config,
        project_config_path=project_config_path,
    )
    sync_secrets.validate_effective_settings("local", effective)

    has_proton = sync_secrets.check_dependencies("local", effective["secret_source"])
    values_data = None
    if effective["values_file"] and effective["secret_source"] in ("yaml", "auto"):
        values_data = sync_secrets.load_values_file(effective["values_file"])

    resolved = {}
    target = effective["secret_target"]

    print(f"   [INFO] Loading {len(secrets_def)} secrets from {path}")
    for key, definition in secrets_def.items():
        if not isinstance(definition, dict):
            continue

        if sync_secrets.is_excluded_from_env(definition):
            print(f"   [INFO] Skipping {key} from {path} (exclude_from_env)")
            continue

        dev_default = definition.get("dev_default")
        value = None

        if dev_default is not None:
            value = dev_default
        else:
            source = sync_secrets.resolve_source(definition, secrets_config, secret_target=target)
            value, _ = sync_secrets.resolve_secret_value(
                key,
                source,
                has_proton,
                effective["secret_source"],
                secret_target=target,
                values_data=values_data,
            )

        if value is None:
            print(f"   [WARN] Missing value for {key} (using empty value)")
            value = ""

        resolved[key] = str(value)

    return resolved


def generate_env(
    env_name,
    config_path="project.yaml",
    output_path=".env",
    secret_target=None,
    secret_source=None,
    values_file=None,
):
    print(f" Generating .env for environment: {env_name}")

    resolved_config_path = sync_secrets.resolve_yaml_path(config_path)
    if not resolved_config_path:
        print(f"Error: Config file '{config_path}' not found.")
        sys.exit(1)

    with open(resolved_config_path, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    secrets_config, secrets_meta = load_secrets_metadata("secrets.yaml")

    inputs = {}
    inputs.update(
        load_inputs_from_secrets_yaml(
            "secrets.yaml",
            project_config=config,
            project_config_path=resolved_config_path,
            secret_target=secret_target,
            secret_source=secret_source,
            values_file=values_file,
        )
    )

    secrets_json = os.environ.get("SECRETS_CONTEXT")
    if secrets_json:
        try:
            print("   Loading SECRETS_CONTEXT from GitHub")
            github_secrets = json.loads(secrets_json)
            allowed_github_secrets = {}
            for key, value in github_secrets.items():
                definition = secrets_meta.get(key)
                # Treat secrets.yaml as the source of truth: only declared,
                # non-excluded secrets reach the runtime .env. Secrets present
                # in SECRETS_CONTEXT but absent from secrets.yaml are dropped
                # by default — this prevents CI-only secrets (e.g. SSH keys
                # used for cross-server automation) from leaking into the
                # runtime environment of every deployed app.
                if not isinstance(definition, dict):
                    print(
                        f"   [INFO] Skipping {key} from SECRETS_CONTEXT (not declared in secrets.yaml)"
                    )
                    continue
                if sync_secrets.is_excluded_from_env(definition):
                    print(f"   [INFO] Skipping {key} from SECRETS_CONTEXT (exclude_from_env)")
                    continue
                allowed_github_secrets[key] = value
            inputs.update(allowed_github_secrets)
        except json.JSONDecodeError:
            print("   Error decoding SECRETS_CONTEXT")

    if env_name == "edge" and os.path.exists(".env.edge"):
        print("   Loading .env.edge (Edge Overrides)")
        inputs.update(parse_env_file(".env.edge"))

    if env_name not in config.get("environments", {}):
        print(f"Error: Environment '{env_name}' not defined in {resolved_config_path}")
        sys.exit(1)

    env_config = config["environments"][env_name]
    domains = env_config.get("domains", [])
    use_traefik = env_config.get("use_traefik", False)

    if env_name == "local":
        web_port = env_config.get("web_port", 8000)
        frontend_port = env_config.get("frontend_port", 5173)
        db_port = env_config.get("db_port", 5432)
        redis_port = env_config.get("redis_port", 6379)
        java_port = env_config.get("java_port", 8080)
    else:
        web_port = None
        frontend_port = None
        db_port = None
        redis_port = None
        java_port = None

    # Global, environment-independent app config (non-secret runtime env vars).
    # Backward-compatible: absent `app_env` block is a no-op, so existing
    # project.yaml files without it keep their previous behaviour.
    #
    # Precedence among raw inputs: secrets < app_env < env_overrides (per-env wins).
    # NOTE: platform-computed keys emitted via add() below (PROJECT_NAME,
    # DJANGO_ALLOWED_HOSTS, CSRF_TRUSTED_URLS, TRAEFIK_ROUTER_RULE, PUBLIC_ORIGIN,
    # MASTER_BASE_URL, volume names, …) are authoritative and CANNOT be overridden
    # via app_env/env_overrides (DEBUG is the sole guarded exception). Keys starting
    # with SSH_/GITHUB_ are filtered out of the runtime .env (see emit loop below).
    app_env_block = config.get("app_env", {})
    if app_env_block:
        print(f"   Applying {len(app_env_block)} app_env vars from project.yaml")
        inputs.update(app_env_block)

    overrides = env_config.get("env_overrides", {})
    if overrides:
        print(f"   Applying {len(overrides)} overrides from project.yaml")
        inputs.update(overrides)

    base_prefix = config.get("container_prefix", "app")
    if env_name == "staging":
        ctr_prefix = f"{base_prefix}_stage"
    elif env_name == "production":
        ctr_prefix = f"{base_prefix}_prod"
    elif env_name == "edge":
        ctr_prefix = f"{base_prefix}_edge"
    else:
        ctr_prefix = f"{base_prefix}_{env_name}"

    lines = []
    lines.append(f"# Auto-generated for environment: {env_name}")
    lines.append(f"# Config Source: {resolved_config_path}")

    written_keys = set()

    def add(key, value):
        if key not in written_keys:
            lines.append(f"{key}={value}")
            written_keys.add(key)

    if env_name != "local":
        add("ENV_TYPE", env_name)
    add("PROJECT_NAME", config.get("project_name", "Project"))
    add("COMPOSE_PROJECT_NAME", f"{config.get('project_name')}_{env_name}")
    add("CONTAINER_NAME_PREFIX", ctr_prefix)
    add("IMAGE_TAG", "latest")

    if env_name == "local":
        add("WEB_PORT", str(web_port))
        add("FRONTEND_PORT", str(frontend_port))
        add("DB_HOST_PORT", str(db_port))
        add("REDIS_HOST_PORT", str(redis_port))
        add("JAVA_PORT", str(java_port))

    add("ROUTER_NAME", f"{config.get('project_name')}-{env_name}")
    add("MFA_WEBAUTHN_RP_NAME", config.get("project_name"))

    add("BACKUP_ENABLE", "true" if env_name == "production" else "false")
    add("TRAEFIK_ENABLE", "true" if use_traefik else "false")
    add("USE_EXTERNAL_PROXY", "true" if (use_traefik and env_name in ("staging", "production")) else "false")

    vol_config = env_config.get("volumes", {})

    def get_vol_name(key, default_suffix):
        val = vol_config.get(key)
        if isinstance(val, dict):
            return val.get("name", f"{ctr_prefix}_{default_suffix}")
        return val if val else f"{ctr_prefix}_{default_suffix}"

    add("DB_VOLUME_NAME", get_vol_name("postgres_data", "postgres_data"))
    add("MEDIA_VOLUME_NAME", get_vol_name("media_volume", "media_volume"))
    add("EXCEL_VOLUME_NAME", get_vol_name("excel_volume", "excel_volume"))

    if domains:
        add("MASTER_BASE_URL", f"https://{domains[0]}")

    if "master_public_ip" in env_config:
        add("MASTER_PUBLIC_IP", env_config["master_public_ip"])

    host_list = list(domains)
    host_list.extend(["localhost", "127.0.0.1", "backend", f"{ctr_prefix}_backend"])
    add("DJANGO_ALLOWED_HOSTS", ",".join(host_list))

    protocol = "https" if use_traefik else "http"
    csrf_urls = [f"{protocol}://{domain}" for domain in domains]

    if env_name == "local":
        csrf_urls.append(f"http://localhost:{frontend_port}")
        csrf_urls.append(f"http://127.0.0.1:{frontend_port}")
        csrf_urls.append(f"http://localhost:{web_port}")
        csrf_urls.append(f"http://127.0.0.1:{web_port}")
        add("PUBLIC_ORIGIN", f"http://localhost:{frontend_port}")
        add("DEBUG", "False")
    else:
        main_domain = domains[0] if domains else "localhost"
        add("PUBLIC_ORIGIN", f"{protocol}://{main_domain}")
        if "DEBUG" not in inputs:
            add("DEBUG", "False")

    add("CSRF_TRUSTED_URLS", ",".join(csrf_urls))

    if use_traefik and domains:
        rules = [f"Host(`{domain}`)" for domain in domains]
        add("TRAEFIK_ROUTER_RULE", " || ".join(rules))
    else:
        add("TRAEFIK_ROUTER_RULE", "Host(`localhost`)")

    print(f"   Injecting {len(inputs)} variables from inputs...")
    local_only_port_keys = {"WEB_PORT", "FRONTEND_PORT", "DB_HOST_PORT", "REDIS_HOST_PORT", "JAVA_PORT"}

    for key in sorted(inputs.keys()):
        if not key or key.startswith("GITHUB_") or key.startswith("SSH_"):
            continue
        if env_name != "local" and key in local_only_port_keys:
            continue

        val = inputs[key]
        if isinstance(val, str) and "\n" in val:
            clean_val = val.replace(chr(10), "\\n").replace(chr(13), "")
            val = f'"{clean_val}"'

        add(key, val)

    write_env_file(output_path, lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", required=True, help="Environment (production, staging, local, edge)")
    parser.add_argument("--output", default=".env", help="Output file path")
    parser.add_argument("--config", default="project.yaml", help="Path to project.yaml")
    parser.add_argument("--secret-target", help="Target placeholder override for target-based secrets")
    parser.add_argument(
        "--secret-source",
        choices=sync_secrets.SECRET_SOURCE_CHOICES,
        help="Override secret provider. When omitted, project.yaml secret_inputs or Proton defaults apply.",
    )
    parser.add_argument("--values-file", help="Override local YAML file with target-specific secret values")
    args = parser.parse_args()

    generate_env(
        args.env,
        config_path=args.config,
        output_path=args.output,
        secret_target=args.secret_target,
        secret_source=args.secret_source,
        values_file=args.values_file,
    )


if __name__ == "__main__":
    main()
