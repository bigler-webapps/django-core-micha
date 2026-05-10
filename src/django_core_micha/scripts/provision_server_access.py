"""
Usage for a new server target
=============================

Run this script from the `webapp-management` repository root.

Minimum setup before running:
- `secrets.yaml` must contain `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY`, and `SSH_PRIVATE_KEY_ROOT`
- the Proton item referenced by `SSH_PRIVATE_KEY_ROOT` must already exist and contain a valid root key
- the target must already exist in `inventory/inventory.yaml`
- `gh`, `ssh-keygen`, and `pass-cli` must be available locally

Typical command:

    provision-server-access --target contact-prod --server-ip 203.0.113.10

What the script does:
1. Generates a new infrastructure SSH key pair locally.
2. Stores host, deploy user, and the new infrastructure private key in Proton.
3. Derives the root public key from Proton and stores it in `access/root/<target>.pub`.
4. Writes the infrastructure public key to `access/infrastructure/<target>.pub`.
5. Syncs the target-specific `webapp-management` secrets to the matching GitHub Environment.
6. Triggers `provision-server.yml` and passes the infrastructure public key directly.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import yaml

from django_core_micha.scripts import sync_secrets

PROTON_CLI_CMD = "pass-cli"
DEFAULT_MANAGEMENT_REPO_NAME = "MichaBigler/webapp-management"
DEFAULT_PROVISION_WORKFLOW = "provision-server.yml"


def fail(message):
    print(f"❌ {message}")
    sys.exit(1)


def run_command(command, cwd=None, input_text=None):
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            input=input_text,
            text=True,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        details = stderr or stdout or str(exc)
        fail(details)


def load_yaml(path):
    if not path.exists():
        fail(f"Required file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        fail(f"Failed to parse {path}: {exc}")


def ensure_dependencies():
    missing = [
        cmd
        for cmd in (PROTON_CLI_CMD, "gh", "ssh-keygen")
        if not shutil.which(cmd)
    ]
    if missing:
        fail(f"Missing required command(s): {', '.join(missing)}")


def parse_proton_path(proton_path):
    if not proton_path or not proton_path.startswith("proton://"):
        fail("Expected a proton:// path in secrets.yaml")

    parts = proton_path.replace("proton://", "").split("/")
    if len(parts) < 3:
        fail("Invalid Proton path format. Expected proton://Vault/Item/Field")

    return parts[0], parts[1], parts[2]


def normalize_key_name(target, explicit_name=None):
    if explicit_name:
        return explicit_name
    return f"{target.replace('-', '_')}_infra"


def generate_keypair(key_name, comment):
    with tempfile.TemporaryDirectory(prefix="provision-server-access-") as temp_dir:
        temp_path = Path(temp_dir)
        private_key_path = temp_path / key_name

        run_command(
            [
                "ssh-keygen",
                "-t",
                "ed25519",
                "-C",
                comment,
                "-N",
                "",
                "-f",
                str(private_key_path),
            ]
        )

        private_key = private_key_path.read_text(encoding="utf-8")
        public_key = private_key_path.with_suffix(".pub").read_text(encoding="utf-8").strip()
        return private_key, public_key


def harden_private_key_file(private_key_path):
    """Restrict private key file permissions so ssh-keygen accepts it on Windows."""
    os.chmod(private_key_path, 0o600)

    if os.name != "nt":
        return

    # OpenSSH on Windows rejects inherited ACLs on private key files.
    for command in (
        ["icacls", str(private_key_path), "/inheritance:r"],
        ["icacls", str(private_key_path), "/grant:r", f"{os.environ.get('USERNAME', '')}:R"],
        ["icacls", str(private_key_path), "/remove:g", "OWNER RIGHTS"],
    ):
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            details = stderr or stdout or "icacls failed"
            fail(f"Failed to secure temporary private key file: {details}")


def derive_public_key(private_key, key_name):
    with tempfile.TemporaryDirectory(prefix="derive-public-key-") as temp_dir:
        private_key_path = Path(temp_dir) / key_name
        private_key_path.write_text(private_key, encoding="utf-8")
        harden_private_key_file(private_key_path)
        result = run_command(["ssh-keygen", "-y", "-f", str(private_key_path)])
        return result.stdout.strip()


def get_secret_definition(secrets_def, key):
    definition = secrets_def.get(key)
    if not isinstance(definition, dict):
        fail(f"{key} definition missing in secrets.yaml")
    return definition


def resolve_secret_source(config, secrets_def, key, target):
    definition = get_secret_definition(secrets_def, key)
    source = sync_secrets.resolve_source(definition, config, secret_target=target)
    if not source:
        fail(f"Could not resolve Proton source for {key}")
    return source


def build_item_updates(updates_by_item, proton_source, value):
    vault, item, field = parse_proton_path(proton_source)
    updates_by_item[(vault, item)].append((field, value))
    return vault, item, field


def update_proton_items(updates_by_item):
    for (vault, item), fields in updates_by_item.items():
        print(f"[INFO] Updating Proton item '{item}' in vault '{vault}'...")
        command = [
            PROTON_CLI_CMD,
            "item",
            "update",
            "--vault-name",
            vault,
            "--item-title",
            item,
        ]

        for field_name, value in fields:
            command.extend(["--field", f"{field_name}={value}"])

        run_command(command)


def sync_management_secrets(config, secrets_def, target):
    print(f"[INFO] Syncing webapp-management secrets for target '{target}' to GitHub...")
    sync_secrets.check_dependencies("github")
    sync_secrets.sync_github(config, secrets_def, secret_target=target)


def trigger_provision_workflow(workflow_name, repo_name, target, server_ip, deploy_user, infra_public_key):
    print(f"[INFO] Triggering workflow '{workflow_name}' in {repo_name}...")
    command = [
        "gh",
        "workflow",
        "run",
        workflow_name,
        "--repo",
        repo_name,
        "--field",
        f"target={target}",
        "--field",
        f"server_ip={server_ip}",
        "--field",
        f"infra_public_key={infra_public_key}",
    ]
    if deploy_user:
        command.extend(["--field", f"deploy_user={deploy_user}"])

    run_command(command)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate a new infrastructure SSH key for a server target, store it in Proton, "
            "sync GitHub Environment secrets, and trigger provision-server.yml."
        )
    )
    parser.add_argument("--target", required=True, help="Inventory target / GitHub environment")
    parser.add_argument("--server-ip", required=True, help="Server IP or host name")
    parser.add_argument(
        "--deploy-user",
        help="Deploy user that should be stored for the target and used by the workflow.",
    )
    parser.add_argument(
        "--key-name",
        help="Infrastructure public key filename without extension. Defaults to <target>_infra.",
    )
    parser.add_argument(
        "--comment",
        help="SSH key comment. Defaults to <key-name>@infra.",
    )
    parser.add_argument(
        "--public-field",
        default="ssh_public_key",
        help="Proton field name that should receive the infrastructure public key.",
    )
    parser.add_argument(
        "--management-repo-name",
        default=DEFAULT_MANAGEMENT_REPO_NAME,
        help="GitHub repository name used to trigger the provision workflow.",
    )
    parser.add_argument(
        "--provision-workflow",
        default=DEFAULT_PROVISION_WORKFLOW,
        help="Workflow filename to trigger after syncing secrets.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rotate the infrastructure key and overwrite an existing public key file.",
    )
    args = parser.parse_args()

    project_dir = Path.cwd()
    secrets_path = project_dir / "secrets.yaml"
    secrets_data = load_yaml(secrets_path)
    config = secrets_data.get("config", {})
    secrets_def = secrets_data.get("secrets", {})

    if not secrets_def:
        fail("No 'secrets' block found in secrets.yaml")

    inventory_target = sync_secrets.get_inventory_target_data(config, args.target)
    if not inventory_target:
        fail(f"Target '{args.target}' is not defined in inventory")

    ensure_dependencies()

    access_root_dir = project_dir / "access" / "root"
    access_infra_dir = project_dir / "access" / "infrastructure"
    access_root_dir.mkdir(parents=True, exist_ok=True)
    access_infra_dir.mkdir(parents=True, exist_ok=True)

    root_source = resolve_secret_source(config, secrets_def, "SSH_PRIVATE_KEY_ROOT", args.target)
    host_source = resolve_secret_source(config, secrets_def, "SSH_HOST", args.target)
    user_source = resolve_secret_source(config, secrets_def, "SSH_USER", args.target)
    infra_private_source = resolve_secret_source(config, secrets_def, "SSH_PRIVATE_KEY", args.target)
    infra_vault, infra_item, _ = parse_proton_path(infra_private_source)

    root_private_key = sync_secrets.get_proton_secret(root_source)
    if not root_private_key:
        fail(f"Could not fetch root private key from Proton for target '{args.target}'")

    root_public_key = derive_public_key(root_private_key, f"{args.target}_root")
    root_public_key_path = access_root_dir / f"{args.target}.pub"
    root_public_key_path.write_text(f"{root_public_key}\n", encoding="utf-8")
    print(f"[INFO] Wrote root public key to {root_public_key_path}")

    key_name = normalize_key_name(args.target, args.key_name)
    comment = args.comment or f"{key_name}@infra"
    infra_public_key_path = access_infra_dir / f"{args.target}.pub"
    rotated = False
    deploy_user = args.deploy_user or inventory_target.get("deploy_user") or "deploy"

    updates_by_item = defaultdict(list)
    current_host = sync_secrets.get_proton_secret(host_source)
    current_user = sync_secrets.get_proton_secret(user_source)

    if current_host != args.server_ip:
        build_item_updates(updates_by_item, host_source, args.server_ip)

    if current_user != deploy_user:
        build_item_updates(updates_by_item, user_source, deploy_user)

    infra_private_key = sync_secrets.get_proton_secret(infra_private_source)

    if args.force or not infra_private_key:
        infra_private_key, infra_public_key = generate_keypair(key_name, comment)
        build_item_updates(updates_by_item, infra_private_source, infra_private_key)
        rotated = True
    else:
        if infra_public_key_path.exists():
            print(f"[INFO] Infrastructure public key already exists at {infra_public_key_path}")
        print("[INFO] Reusing existing infrastructure key material from Proton...")

        try:
            infra_public_key = derive_public_key(infra_private_key, key_name)
        except SystemExit:
            fail(
                "Existing infrastructure private key in Proton is invalid. "
                "Fix the Proton field or rerun with --force to rotate it."
            )

    current_public_field_value = sync_secrets.get_proton_secret(
        f"proton://{infra_vault}/{infra_item}/{args.public_field}"
    )
    if current_public_field_value != infra_public_key:
        updates_by_item[(infra_vault, infra_item)].append((args.public_field, infra_public_key))

    infra_public_key_path.write_text(f"{infra_public_key}\n", encoding="utf-8")
    print(f"[INFO] Wrote infrastructure public key to {infra_public_key_path}")

    if updates_by_item:
        update_proton_items(updates_by_item)
    else:
        print("[INFO] Proton item already up to date. No Proton write needed.")
    sync_management_secrets(config, secrets_def, args.target)
    trigger_provision_workflow(
        args.provision_workflow,
        args.management_repo_name,
        args.target,
        args.server_ip,
        deploy_user,
        infra_public_key,
    )

    print("")
    print("✅ Server access provisioning completed.")
    print(f"   Target: {args.target}")
    print(f"   Server IP: {args.server_ip}")
    print(f"   Deploy user: {deploy_user}")
    print(f"   Root public key file: {root_public_key_path}")
    print(f"   Infrastructure public key file: {infra_public_key_path}")
    if rotated:
        print("   Key action: generated new infrastructure key pair")
    else:
        print("   Key action: reused existing infrastructure key pair")
    print(
        "   Next step: monitor the provision workflow in webapp-management "
        f"('{args.provision_workflow}')."
    )


if __name__ == "__main__":
    main()
