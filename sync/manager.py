import os
import sys
import json
import base64
import hashlib
import platform
import keyring
from datetime import datetime
from typing import Optional, List, Dict, Tuple, Any

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.fernet import Fernet
from github import Github, GithubException
from github.InputGitTreeElement import InputGitTreeElement

from shellpa.dotfiles.manager import (
    DynamicPath,
    CONFIG_PATH,
    BACKUP_DIR,
    calculate_hash,
    load_config,
    save_config,
    load_metadata,
    save_metadata,
)
from shellpa.cheatsheet.manager import get_connection, get_all_snippets, add_snippet

# ─── Paths ────────────────────────────────────────────────────────────────────
TOKEN_FALLBACK_PATH = DynamicPath(lambda: os.path.join(os.path.expanduser("~/.shellpa"), ".github_token"))

# ─── Exceptions ────────────────────────────────────────────────────────────────
class SyncError(Exception):
    """User-facing sync error."""

# ─── Token Management ──────────────────────────────────────────────────────────
def ensure_token() -> str:
    """
    Attempts to fetch the GitHub token from the system keyring.
    Falls back to ~/.shellpa/.github_token (unencrypted file).
    Prints a warning when using fallback file.
    Raises SyncError if no token is found.
    """
    try:
        token = keyring.get_password("shellpa", "github")
        if token:
            return token
    except Exception:
        pass

    fallback_path = str(TOKEN_FALLBACK_PATH)
    if os.path.exists(fallback_path):
        from rich.console import Console
        Console().print(
            "[yellow]Warning: Using unencrypted GitHub token from fallback file. "
            "Start a keyring daemon (e.g. gnome-keyring) to store it securely.[/yellow]"
        )
        try:
            with open(fallback_path, "r") as f:
                token = f.read().strip()
                if token:
                    return token
        except Exception as e:
            raise SyncError(f"Failed to read fallback token file: {e}")

    raise SyncError(
        "GitHub token not found. Please run `sp sync setup` to configure sync."
    )


def store_token(token: str) -> None:
    """
    Attempts to store token in keyring. On failure, falls back to fallback file
    written atomically with chmod 600 permissions.
    """
    token = token.strip()
    try:
        keyring.set_password("shellpa", "github", token)
        # Verify it works
        val = keyring.get_password("shellpa", "github")
        if val == token:
            # If fallback file exists, remove it
            fallback_path = str(TOKEN_FALLBACK_PATH)
            if os.path.exists(fallback_path):
                try:
                    os.remove(fallback_path)
                except Exception:
                    pass
            return
    except Exception:
        pass

    # Keyring failed/not available, write fallback file with 600 permissions
    fallback_path = str(TOKEN_FALLBACK_PATH)
    try:
        os.makedirs(os.path.dirname(fallback_path), exist_ok=True)
        # O_CREAT | O_WRONLY | O_TRUNC with mode 0o600 for atomic restricted permissions
        fd = os.open(fallback_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token)
    except Exception as e:
        raise SyncError(f"Failed to save fallback token: {e}")


def get_github_client() -> Github:
    """Returns PyGithub client authenticated via ensure_token()."""
    token = ensure_token()
    return Github(token)


def ensure_repo(client: Github, repo_name: str) -> Any:
    """
    Ensures a repository exists on GitHub, creating it as private if missing.
    Aborts with SyncError if it exists but is PUBLIC.
    """
    try:
        user = client.get_user()
    except GithubException as e:
        raise SyncError(f"GitHub authentication failed: {e}")

    try:
        repo = user.get_repo(repo_name)
    except GithubException as e:
        if e.status == 404:
            # Create private repo
            try:
                repo = user.create_repo(repo_name, private=True)
            except Exception as create_err:
                raise SyncError(
                    f"Failed to create private repository '{repo_name}': {create_err}"
                )
        else:
            raise SyncError(f"GitHub API error: {e}")

    if not repo.private:
        raise SyncError(
            f"Repository '{repo_name}' is PUBLIC! Sync aborted for security."
        )
    return repo


# ─── Configuration Sanitation ─────────────────────────────────────────────────
def sanitize_config() -> str:
    """
    Loads config.toml and asserts no keys/secrets are stored inside.
    Returns the config content string if clean.
    """
    path = str(CONFIG_PATH)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r") as f:
            content = f.read()
    except Exception as e:
        raise SyncError(f"Failed to read config.toml: {e}")

    import toml
    try:
        data = toml.loads(content)
    except Exception as e:
        raise SyncError(f"config.toml is malformed: {e}")

    # Assert no secrets in [ai]
    ai_section = data.get("ai", {})
    if ai_section.get("api_key"):
        raise SyncError(
            "Security violation: config.toml contains ai.api_key! Must use environment variables instead."
        )

    # General pattern check for tokens
    import re
    if (
        re.search(r"nvapi-[A-Za-z0-9_\-]+", content)
        or re.search(r"ghp_[A-Za-z0-9]+", content)
        or re.search(r"github_pat_[A-Za-z0-9_\-]+", content)
    ):
        raise SyncError("Security violation: config.toml contains raw secret API tokens!")

    return content


# ─── Cryptography ─────────────────────────────────────────────────────────────
def _derive_key(passphrase: str) -> bytes:
    # PBKDF2HMAC with a constant salt for sync decryption consistency
    salt = b"shellpa-sync-salt-constant"
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))


def encrypt_blob(data: bytes, passphrase: str) -> bytes:
    """Encrypts bytes using Fernet derived key."""
    key = _derive_key(passphrase)
    f = Fernet(key)
    return f.encrypt(data)


def decrypt_blob(data: bytes, passphrase: str) -> bytes:
    """Decrypts bytes using Fernet derived key."""
    key = _derive_key(passphrase)
    f = Fernet(key)
    return f.decrypt(data)


# ─── Snippets Export ──────────────────────────────────────────────────────────
def export_snippets_json() -> List[Dict]:
    """Wraps get_all_snippets() and asserts/ensures UUID is present in all rows."""
    # Connecting automatically triggers migrations/backfills via init_db
    get_connection().close()
    snippets = get_all_snippets()
    for s in snippets:
        if not s.get("uuid"):
            raise SyncError(
                f"Snippet #{s['id']} has no UUID. Database migration failed or skipped."
            )
    return snippets


# ─── Payload Building ──────────────────────────────────────────────────────────
def build_payload(passphrase: Optional[str] = None) -> Dict[str, bytes]:
    """
    Walks backup files, exports config + snippets, encrypting snippets and
    dotfiles contents if passphrase is provided. Returns rel_path -> bytes map.
    """
    payload = {}

    # 1. Config (Never encrypted, but sanitized)
    config_content = sanitize_config()
    payload["config.toml"] = config_content.encode("utf-8")

    # 2. Snippets
    snippets_list = export_snippets_json()
    snippets_bytes = json.dumps(snippets_list, indent=2).encode("utf-8")
    if passphrase:
        snippets_bytes = encrypt_blob(snippets_bytes, passphrase)
    payload["snippets.json"] = snippets_bytes

    # 3. Dotfiles tree under BACKUP_DIR
    backup_dir = str(BACKUP_DIR)
    if os.path.exists(backup_dir):
        for root, _, files in os.walk(backup_dir):
            for file in files:
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, backup_dir)
                try:
                    with open(abs_path, "rb") as f:
                        file_bytes = f.read()
                except Exception as e:
                    raise SyncError(f"Failed to read backup file {abs_path}: {e}")

                if passphrase:
                    file_bytes = encrypt_blob(file_bytes, passphrase)

                # Store with prefix directory
                payload[f"dotfiles/{rel_path}"] = file_bytes

    return payload


# ─── Push ─────────────────────────────────────────────────────────────────────
def push(
    client: Github,
    repo: Any,
    payload: Dict[str, bytes],
    message: str,
) -> None:
    """
    Builds a Git commit recursively and pushes via Git Data API.
    Handles empty repositories by creating branch ref cleanly.
    """
    default_branch = repo.default_branch

    # Create tree elements from payload blobs
    tree_elements = []
    for path, data in payload.items():
        try:
            # base64 representation prevents binary encoding issues with API
            blob = repo.create_git_blob(
                base64.b64encode(data).decode("utf-8"), "base64"
            )
            tree_elements.append(
                InputGitTreeElement(path, "100644", "blob", sha=blob.sha)
            )
        except Exception as e:
            raise SyncError(f"Failed to create blob for '{path}': {e}")

    # Check if empty repo (no commits)
    is_empty = False
    try:
        # get_commits() raises or is empty if branch has no commits
        commits = repo.get_commits()
        if commits.totalCount == 0:
            is_empty = True
        else:
            # Attempt to touch first commit to confirm
            _ = commits[0]
    except Exception:
        is_empty = True

    if is_empty:
        # Empty repo: create tree, commit with no parents, create git ref
        try:
            tree = repo.create_git_tree(tree_elements)
            commit = repo.create_git_commit(message, tree.sha, parents=[])
            repo.create_git_ref(ref=f"refs/heads/{default_branch}", sha=commit.sha)
        except Exception as e:
            raise SyncError(f"Failed to initialize repository commit: {e}")
    else:
        # Non-empty: retrieve parent tree, create tree base, commit, update ref
        try:
            branch = repo.get_branch(default_branch)
            parent_commit = branch.commit.commit
            tree = repo.create_git_tree(tree_elements, base_tree=parent_commit.tree.sha)
            commit = repo.create_git_commit(
                message, tree.sha, parents=[parent_commit]
            )
            ref = repo.get_ref(f"heads/{default_branch}")
            ref.edit(sha=commit.sha)
        except Exception as e:
            raise SyncError(f"Failed to push commit: {e}")

    try:
        config = load_config()
        if "sync" not in config:
            config["sync"] = {}
        config["sync"]["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_config(config)
    except Exception:
        pass


# ─── Status dry run / Diff calculation ─────────────────────────────────────────
def diff_status(
    client: Github,
    repo: Any,
    passphrase: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Computes a read-only comparison report between remote repository files
    and local cheatsheet / backup files. Mutates nothing.
    """
    default_branch = repo.default_branch

    # Download all files from remote tree
    remote_payload = {}
    try:
        branch = repo.get_branch(default_branch)
        tree_sha = branch.commit.commit.tree.sha
        git_tree = repo.get_git_tree(tree_sha, recursive=True)
        for element in git_tree.tree:
            if element.type == "blob":
                blob = repo.get_git_blob(element.sha)
                if blob.encoding == "base64":
                    content = base64.b64decode(blob.content.strip())
                else:
                    content = blob.content.encode("utf-8")
                remote_payload[element.path] = content
    except Exception:
        # Empty repo or connection issue. Treat remote as empty.
        pass

    # Parsed Remote Data
    remote_snippets = []
    if "snippets.json" in remote_payload:
        snippets_bytes = remote_payload["snippets.json"]
        if passphrase:
            try:
                snippets_bytes = decrypt_blob(snippets_bytes, passphrase)
            except Exception:
                raise SyncError(
                    "Failed to decrypt remote snippets.json. Check passphrase."
                )
        try:
            remote_snippets = json.loads(snippets_bytes.decode("utf-8"))
        except Exception:
            raise SyncError("Remote snippets.json is not valid JSON.")

    # 1. Compare Snippets
    # Auto-migration triggered to ensure local UUIDs exist
    get_connection().close()
    local_snippets = get_all_snippets()

    local_snippets_by_uuid = {s["uuid"]: s for s in local_snippets if s.get("uuid")}
    remote_snippets_by_uuid = {s["uuid"]: s for s in remote_snippets if s.get("uuid")}

    snippets_report = {
        "remote_added": [],  # exists in remote, not locally
        "pending_push": [],  # exists locally, not in remote
        "conflicts": [],     # conflict mismatches
        "noop": [],          # identical
    }

    # Remote additions & conflicts
    for r_uuid, r_snippet in remote_snippets_by_uuid.items():
        if r_uuid not in local_snippets_by_uuid:
            snippets_report["remote_added"].append(r_snippet)
        else:
            l_snippet = local_snippets_by_uuid[r_uuid]
            if l_snippet["command"] == r_snippet["command"]:
                snippets_report["noop"].append(r_snippet)
            else:
                snippets_report["conflicts"].append((l_snippet, r_snippet))

    # Local pending push
    for l_uuid, l_snippet in local_snippets_by_uuid.items():
        if l_uuid not in remote_snippets_by_uuid:
            snippets_report["pending_push"].append(l_snippet)

    # 2. Compare Dotfiles
    backup_dir = str(BACKUP_DIR)
    local_dotfiles = {}
    if os.path.exists(backup_dir):
        for root, _, files in os.walk(backup_dir):
            for file in files:
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, backup_dir)
                local_dotfiles[rel_path] = abs_path

    dotfiles_report = {
        "remote_added": [],  # rel_path, remote_bytes
        "pending_push": [],  # rel_path, local_bytes
        "conflicts": [],     # rel_path, local_bytes, remote_bytes
        "noop": [],          # rel_path
    }

    # Remote dotfiles relative mapping: remote key "dotfiles/home/user/.bashrc" -> rel_path "home/user/.bashrc"
    remote_dotfiles = {}
    for path, data in remote_payload.items():
        if path.startswith("dotfiles/"):
            rel_path = path[len("dotfiles/") :]
            decrypted = data
            if passphrase:
                try:
                    decrypted = decrypt_blob(data, passphrase)
                except Exception:
                    raise SyncError(
                        f"Failed to decrypt remote dotfile: {path}. Check passphrase."
                    )
            remote_dotfiles[rel_path] = decrypted

    for rel_path, remote_bytes in remote_dotfiles.items():
        if rel_path not in local_dotfiles:
            dotfiles_report["remote_added"].append((rel_path, remote_bytes))
        else:
            # Compare hashes
            local_path = local_dotfiles[rel_path]
            l_hash = calculate_hash(local_path)
            # Remote bytes hash
            r_hash = hashlib.sha256(remote_bytes).hexdigest()

            if l_hash == r_hash:
                dotfiles_report["noop"].append(rel_path)
            else:
                try:
                    with open(local_path, "rb") as f:
                        local_bytes = f.read()
                except Exception:
                    local_bytes = b""
                dotfiles_report["conflicts"].append(
                    (rel_path, local_bytes, remote_bytes)
                )

    # Local pending push
    for rel_path, local_path in local_dotfiles.items():
        if rel_path not in remote_dotfiles:
            try:
                with open(local_path, "rb") as f:
                    local_bytes = f.read()
            except Exception:
                local_bytes = b""
            dotfiles_report["pending_push"].append((rel_path, local_bytes))

    return {
        "snippets": snippets_report,
        "dotfiles": dotfiles_report,
        "remote_payload": remote_payload,
    }


# ─── Apply Pull ───────────────────────────────────────────────────────────────
def apply_pull(
    diff_report: Dict[str, Any],
    resolved_snippets: List[Tuple[str, str, Dict[str, Any]]],
    resolved_dotfiles: List[Tuple[str, str, bytes]],
) -> None:
    """
    Applies the resolved diff updates to the local database and local backup
    copies. Syncs hashes to meta.json for dotfiles.
    """
    # 1. Apply Snippet Additions
    for s in diff_report["snippets"]["remote_added"]:
        add_snippet(
            s["command"],
            s["description"],
            s["tags"],
            s["source"],
            s["uuid"],
        )

    # 2. Apply Snippet Resolutions
    from shellpa.cheatsheet.manager import update_snippet

    for uuid_val, action, remote_snippet in resolved_snippets:
        if action == "remote":
            with get_connection() as conn:
                row = conn.execute(
                    "SELECT id FROM snippets WHERE uuid = ?", (uuid_val,)
                ).fetchone()
                if row:
                    sid = row["id"]
                    update_snippet(
                        sid,
                        remote_snippet["command"],
                        remote_snippet["description"],
                        remote_snippet["tags"],
                    )
        elif action == "both":
            # Keep local (noop), and insert remote as a fresh snippet with new UUID
            add_snippet(
                remote_snippet["command"],
                remote_snippet["description"],
                remote_snippet["tags"],
                remote_snippet["source"],
            )

    # 3. Apply Dotfile Additions
    backup_dir = str(BACKUP_DIR)
    meta = load_metadata()

    for rel_path, remote_bytes in diff_report["dotfiles"]["remote_added"]:
        dest_path = os.path.join(backup_dir, rel_path)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(remote_bytes)

        # Update meta.json
        orig_path = "/" + rel_path
        meta[orig_path] = {
            "timestamp": datetime.now().isoformat(),
            "sha256": hashlib.sha256(remote_bytes).hexdigest(),
        }

    # 4. Apply Dotfile Resolutions
    for rel_path, action, remote_bytes in resolved_dotfiles:
        if action == "remote":
            dest_path = os.path.join(backup_dir, rel_path)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, "wb") as f:
                f.write(remote_bytes)

            # Update meta.json
            orig_path = "/" + rel_path
            meta[orig_path] = {
                "timestamp": datetime.now().isoformat(),
                "sha256": hashlib.sha256(remote_bytes).hexdigest(),
            }

    save_metadata(meta)

    try:
        config = load_config()
        if "sync" not in config:
            config["sync"] = {}
        config["sync"]["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_config(config)
    except Exception:
        pass


# ─── Cron / macOS Daemon ───────────────────────────────────────────────────────
def setup_cron(interval_hours: int) -> None:
    """Linux crontab auto-sync scheduler configuration."""
    from crontab import CronTab

    cron = CronTab(user=True)
    cron.remove_all(comment="shellpa-sync")

    # Call CLI entry point preserving python environment
    cmd = f"{sys.executable} -m shellpa.main sync push"
    job = cron.new(command=cmd, comment="shellpa-sync")
    job.hour.every(interval_hours)
    cron.write()


def remove_cron() -> None:
    """Linux crontab entry disablement."""
    from crontab import CronTab

    cron = CronTab(user=True)
    cron.remove_all(comment="shellpa-sync")
    cron.write()


def setup_launchd(interval_hours: int) -> None:
    """Best effort Stub for macOS launchd daemon configuration."""
    if platform.system() != "Darwin":
        raise SyncError(
            "macOS launchd auto-sync scheduling can only be configured on macOS."
        )
    # macOS Stub implementation (if needed, otherwise stub with exception)
    raise SyncError("macOS launchd config is not implemented on this host.")


def remove_launchd() -> None:
    """macOS launchd plist disablement."""
    if platform.system() != "Darwin":
        raise SyncError(
            "macOS launchd auto-sync scheduling can only be configured on macOS."
        )
    raise SyncError("macOS launchd config is not implemented on this host.")
