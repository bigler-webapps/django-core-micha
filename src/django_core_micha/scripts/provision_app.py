"""
Usage for a new app
===================

Run this script from the app repository root, where `secrets.yaml` is located.

Minimum setup before running:
- `secrets.yaml` must contain `config.target_repo`
- `secrets.yaml` must contain `secrets.SSH_PRIVATE_KEY.source`
- `project.yaml` should contain `deploy_target`
- the Proton item referenced by `SSH_PRIVATE_KEY.source` must already exist
- `gh`, `ssh-keygen`, and `pass-cli` must be available locally
- you must be logged into Proton Pass CLI and GitHub CLI

Typical command for a new app:

    provision-app

Concrete example for `survey_app`:

    cd ..\\survey_app
    provision-app --key-name survey_app_ci --comment "survey_app_ci@deploy"

This assumes:
- `survey_app/secrets.yaml` contains `config.target_repo: "MichaBigler/survey_app"`
- `survey_app/secrets.yaml` contains
  `SSH_PRIVATE_KEY.source: "proton://Projekt Survey-App/Infrastructure-Access/ssh_private_key"`
- the Proton item `Projekt Survey-App / Infrastructure-Access` already exists
- `webapp-management` is located next to `survey_app`

Useful optional arguments:
- `--management-repo ..\\webapp-management`
  Use this if the infrastructure repo is not a sibling directory.
- `--target contact-prod`
  Override `deploy_target` from `project.yaml`.
- `--key-name my_app_ci`
  Override the generated public key filename.
- `--comment "my_app_ci@deploy"`
  Override the SSH key comment.
- `--force`
  Rotate an existing key and overwrite the public key file.

What the script does:
1. Generates a new ed25519 deploy key pair locally.
2. Updates the Proton item referenced by `SSH_PRIVATE_KEY.source`.
3. Writes the public key to `webapp-management/access/deploy/<key_name>.pub`.
4. Syncs GitHub secrets for the current app from Proton.
5. Triggers `sync-ssh-access.yml` in `webapp-management` and passes the public key directly.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from django_core_micha.scripts import sync_secrets

PROTON_CLI_CMD = "pass-cli"
DEFAULT_MANAGEMENT_REPO_NAME = "MichaBigler/webapp-management"
DEFAULT_SYNC_WORKFLOW = "sync-ssh-access.yml"


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


def read_deploy_target(project_dir):
    project_data = load_yaml(project_dir / "project.yaml")
    deploy_target = project_data.get("deploy_target")
    return str(deploy_target).strip() if deploy_target else None


def parse_proton_path(proton_path):
    if not proton_path or not proton_path.startswith("proton://"):
        fail("SSH_PRIVATE_KEY source must be a proton:// path in secrets.yaml")

    parts = proton_path.replace("proton://", "").split("/")
    if len(parts) < 3:
        fail("Invalid Proton path format. Expected proton://Vault/Item/Field")

    vault = parts[0]
    item = parts[1]
    field = parts[2]
    return vault, item, field


def get_management_repo_path(project_dir, override):
    if override:
        repo_path = Path(override).expanduser().resolve()
    elif project_dir.name == "webapp-management":
        repo_path = project_dir
    else:
        repo_path = (project_dir.parent / "webapp-management").resolve()

    if not repo_path.exists():
        fail(
            "Could not find webapp-management repo. "
            "Use --management-repo to point to it explicitly."
        )

    return repo_path


def ensure_dependencies():
    missing = [
        cmd
        for cmd in (PROTON_CLI_CMD, "gh", "ssh-keygen")
        if not shutil.which(cmd)
    ]
    if missing:
        fail(f"Missing required command(s): {', '.join(missing)}")


def normalize_key_name(target_repo, explicit_name=None):
    if explicit_name:
        return explicit_name

    repo_name = (target_repo or "").split("/")[-1] or Path.cwd().name
    return f"{repo_name.replace('-', '_')}_ci"


def generate_keypair(key_name, comment):
    with tempfile.TemporaryDirectory(prefix="provision-app-") as temp_dir:
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
    with tempfile.TemporaryDirectory(prefix="derive-app-public-key-") as temp_dir:
        private_key_path = Path(temp_dir) / key_name
        private_key_path.write_text(private_key, encoding="utf-8")
        harden_private_key_file(private_key_path)
        result = run_command(["ssh-keygen", "-y", "-f", str(private_key_path)])
        return result.stdout.strip()


def update_proton_item(vault, item, private_field, public_field, private_key, public_key):
    print(f"[INFO] Updating Proton item '{item}' in vault '{vault}'...")
    run_command(
        [
            PROTON_CLI_CMD,
            "item",
            "update",
            "--vault-name",
            vault,
            "--item-title",
            item,
            "--field",
            f"{private_field}={private_key}",
            "--field",
            f"{public_field}={public_key}",
        ]
    )


def sync_github_secrets(project_dir):
    secrets_path = project_dir / "secrets.yaml"
    data = load_yaml(secrets_path)
    config = data.get("config", {})
    secrets_def = data.get("secrets", {})
    if not secrets_def:
        fail("No 'secrets' block found in secrets.yaml")

    print("[INFO] Syncing GitHub secrets from Proton...")
    sync_secrets.check_dependencies("github")
    sync_secrets.sync_github(config, secrets_def)


def trigger_ssh_sync(workflow_name, management_repo_name, target, deploy_public_key):
    print(f"[INFO] Triggering workflow '{workflow_name}' in {management_repo_name}...")
    run_command(
        [
            "gh",
            "workflow",
            "run",
            workflow_name,
            "--repo",
            management_repo_name,
            "--field",
            f"target={target}",
            "--field",
            f"extra_deploy_public_key={deploy_public_key}",
        ]
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate a deploy SSH key for the current app, store it in Proton, "
            "publish the public key to webapp-management/access/deploy, sync GitHub "
            "secrets, and trigger the SSH access sync workflow."
        )
    )
    parser.add_argument(
        "--management-repo",
        help="Path to the local webapp-management repository (defaults to sibling repo).",
    )
    parser.add_argument(
        "--management-repo-name",
        default=DEFAULT_MANAGEMENT_REPO_NAME,
        help="GitHub repository name used to trigger the sync workflow.",
    )
    parser.add_argument(
        "--sync-workflow",
        default=DEFAULT_SYNC_WORKFLOW,
        help="Workflow filename to trigger after writing the public key.",
    )
    parser.add_argument(
        "--target",
        help="Inventory target in webapp-management. Defaults to deploy_target from project.yaml.",
    )
    parser.add_argument(
        "--key-name",
        help="Public key filename without extension. Defaults to <repo>_ci.",
    )
    parser.add_argument(
        "--public-field",
        default="ssh_public_key",
        help="Proton field name that should receive the public key.",
    )
    parser.add_argument(
        "--comment",
        help="SSH key comment. Defaults to <key-name>@deploy.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing public key file in webapp-management/access/deploy.",
    )
    args = parser.parse_args()

    project_dir = Path.cwd()
    secrets_path = project_dir / "secrets.yaml"
    secrets_data = load_yaml(secrets_path)
    config = secrets_data.get("config", {})
    secrets_def = secrets_data.get("secrets", {})
    deploy_target = args.target or read_deploy_target(project_dir)

    ssh_private_key_def = secrets_def.get("SSH_PRIVATE_KEY")
    if not isinstance(ssh_private_key_def, dict):
        fail("SSH_PRIVATE_KEY definition missing in secrets.yaml")

    target_repo = config.get("target_repo")
    if not target_repo:
        fail("config.target_repo missing in secrets.yaml")

    if not deploy_target:
        fail(
            "No deploy target configured. Add 'deploy_target' to project.yaml "
            "or pass --target explicitly."
        )

    management_repo_path = get_management_repo_path(project_dir, args.management_repo)
    access_dir = management_repo_path / "access" / "deploy"
    access_dir.mkdir(parents=True, exist_ok=True)

    ensure_dependencies()

    key_name = normalize_key_name(target_repo, args.key_name)
    comment = args.comment or f"{key_name}@deploy"
    public_key_path = access_dir / f"{key_name}.pub"

    proton_source = ssh_private_key_def.get("source")
    vault, item, private_field = parse_proton_path(proton_source)
    rotated = False

    if public_key_path.exists() and not args.force:
        print(f"[INFO] Public key already exists at {public_key_path}")
        print("[INFO] Reusing existing key material and continuing with secret sync...")
        public_key = sync_secrets.get_proton_secret(f"proton://{vault}/{item}/{args.public_field}")
        if not public_key:
            private_key = sync_secrets.get_proton_secret(proton_source)
            if not private_key:
                fail("Existing Proton SSH private key missing; rerun with --force to rotate it.")
            public_key = derive_public_key(private_key, key_name)
        public_key_path.write_text(f"{public_key}\n", encoding="utf-8")
    else:
        private_key, public_key = generate_keypair(key_name, comment)
        update_proton_item(
            vault=vault,
            item=item,
            private_field=private_field,
            public_field=args.public_field,
            private_key=private_key,
            public_key=public_key,
        )

        public_key_path.write_text(f"{public_key}\n", encoding="utf-8")
        print(f"[INFO] Wrote public key to {public_key_path}")
        rotated = True

    sync_github_secrets(project_dir)
    trigger_ssh_sync(args.sync_workflow, args.management_repo_name, deploy_target, public_key)

    print("")
    print("✅ App provisioning completed.")
    print(f"   Key name: {key_name}")
    print(f"   Deploy target: {deploy_target}")
    print(f"   Proton item: {vault}/{item}")
    print(f"   Public key file: {public_key_path}")
    if rotated:
        print("   Key action: generated new key pair and updated Proton")
    else:
        print("   Key action: reused existing key pair")
    print(
        "   Next step: monitor the SSH sync workflow in webapp-management "
        f"('{args.sync_workflow}')."
    )


if __name__ == "__main__":
    main()
