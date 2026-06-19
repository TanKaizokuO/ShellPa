import os
import json
import sqlite3
import pytest
from unittest.mock import patch, MagicMock

from shellpa.cheatsheet import manager as cs_manager
from shellpa.sync import manager as sync_manager
from shellpa.sync.manager import SyncError


# ─── Schema Migration Tests ──────────────────────────────────────────────────

def test_migrate_snippets_uuid_backfills_existing_rows(mock_shellpa_home):
    # Initialize DB table using the old schema without uuid first
    db_path = str(cs_manager.DB_PATH)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE snippets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            command     TEXT NOT NULL,
            description TEXT NOT NULL,
            tags        TEXT NOT NULL DEFAULT '',
            source      TEXT NOT NULL DEFAULT 'manual',
            created_at  TEXT NOT NULL,
            last_used   TEXT,
            use_count   INTEGER NOT NULL DEFAULT 0
        );
    """)
    conn.execute(
        "INSERT INTO snippets (command, description, created_at) VALUES (?, ?, ?)",
        ("ls -la", "list directory", "2026-06-19T00:00:00")
    )
    conn.commit()
    conn.close()

    # Triggering cs_manager.get_connection() runs init_db which includes schema migration & backfill
    conn2 = cs_manager.get_connection()
    cursor = conn2.execute("SELECT id, command, uuid FROM snippets")
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["command"] == "ls -la"
    assert rows[0]["uuid"] is not None
    assert len(rows[0]["uuid"]) > 0
    conn2.close()


def test_migrate_snippets_uuid_idempotent_on_rerun(mock_shellpa_home):
    conn = cs_manager.get_connection()
    conn.close()

    # Rerun - should be a complete no-op and not raise errors
    conn2 = cs_manager.get_connection()
    conn2.close()


# ─── Snippets Export Tests ───────────────────────────────────────────────────

def test_export_snippets_json_includes_uuid(mock_shellpa_home):
    cs_manager.add_snippet("echo hello", "print hello")
    snippets = sync_manager.export_snippets_json()
    assert len(snippets) == 1
    assert "uuid" in snippets[0]
    assert snippets[0]["command"] == "echo hello"


# ─── Config Sanitation Tests ──────────────────────────────────────────────────

def test_sanitize_config_passes_when_clean(mock_shellpa_home):
    from shellpa.dotfiles.manager import CONFIG_PATH
    import toml
    config_data = {
        "dotfiles": {"files": []},
        "ai": {"api_key": ""},
        "sync": {"repo": "shellpa-backup"}
    }
    with open(CONFIG_PATH, "w") as f:
        toml.dump(config_data, f)

    content = sync_manager.sanitize_config()
    assert len(content) > 0


def test_sanitize_config_raises_on_populated_secret_field(mock_shellpa_home):
    from shellpa.dotfiles.manager import CONFIG_PATH
    import toml
    # Containing ai.api_key
    config_data = {
        "dotfiles": {"files": []},
        "ai": {"api_key": "nvapi-somekeyhere"},
        "sync": {"repo": "shellpa-backup"}
    }
    with open(CONFIG_PATH, "w") as f:
        toml.dump(config_data, f)

    with pytest.raises(SyncError, match="config.toml contains ai.api_key"):
        sync_manager.sanitize_config()

    # Containing raw github token regex scan
    config_data2 = {
        "dotfiles": {"files": []},
        "ai": {"api_key": ""},
        "sync": {"repo": "shellpa-backup", "secret": "github_pat_1234abcd"}
    }
    with open(CONFIG_PATH, "w") as f:
        toml.dump(config_data2, f)

    with pytest.raises(SyncError, match="contains raw secret API tokens"):
        sync_manager.sanitize_config()


# ─── Visibility and Token Storage Tests ───────────────────────────────────────

def test_ensure_repo_raises_on_existing_public_repo(mock_shellpa_home):
    mock_client = MagicMock()
    mock_user = MagicMock()
    mock_repo = MagicMock()
    mock_repo.private = False  # Public repo!
    mock_user.get_repo.return_value = mock_repo
    mock_client.get_user.return_value = mock_user

    with pytest.raises(SyncError, match="is PUBLIC! Sync aborted for security"):
        sync_manager.ensure_repo(mock_client, "some-repo")


def test_store_and_ensure_token_roundtrip_keyring(mock_shellpa_home):
    stored_passwords = {}

    def mock_set(service, username, password):
        stored_passwords[(service, username)] = password

    def mock_get(service, username):
        return stored_passwords.get((service, username))

    with patch("keyring.set_password", side_effect=mock_set), \
         patch("keyring.get_password", side_effect=mock_get):
        sync_manager.store_token("my-fake-token")
        token = sync_manager.ensure_token()
        assert token == "my-fake-token"


def test_ensure_token_falls_back_to_file_when_no_keyring_backend(mock_shellpa_home):
    # keyring raises exception (no daemon running)
    with patch("keyring.get_password", side_effect=RuntimeError("no backend")), \
         patch("keyring.set_password", side_effect=RuntimeError("no backend")):
        sync_manager.store_token("fallback-token")
        assert os.path.exists(sync_manager.TOKEN_FALLBACK_PATH)

        # Check permissions: 600 (octal)
        stat = os.stat(sync_manager.TOKEN_FALLBACK_PATH)
        assert (stat.st_mode & 0o777) == 0o600

        token = sync_manager.ensure_token()
        assert token == "fallback-token"


# ─── Payload Building & Cryptography Tests ─────────────────────────────────────

def test_build_payload_excludes_ai_cache_and_meta_json(mock_shellpa_home):
    # Set up config, cheatsheet DB and dotfiles backup dir
    from shellpa.dotfiles.manager import BACKUP_DIR, CONFIG_PATH, META_PATH
    import toml
    config_data = {
        "dotfiles": {"files": []},
        "ai": {"api_key": ""},
        "sync": {"repo": "shellpa-backup"}
    }
    with open(CONFIG_PATH, "w") as f:
        toml.dump(config_data, f)

    # Create dummy backup file
    backup_file = os.path.join(str(BACKUP_DIR), "dummy.txt")
    os.makedirs(os.path.dirname(backup_file), exist_ok=True)
    with open(backup_file, "w") as f:
        f.write("hello")

    # Create meta.json and ai_cache.json which must be EXCLUDED
    with open(META_PATH, "w") as f:
        f.write("{}")
    
    ai_cache_path = os.path.join(os.path.dirname(str(CONFIG_PATH)), "ai_cache.json")
    with open(ai_cache_path, "w") as f:
        f.write("[]")

    payload = sync_manager.build_payload()
    assert "config.toml" in payload
    assert "snippets.json" in payload
    assert "dotfiles/dummy.txt" in payload
    assert "meta.json" not in payload
    assert "ai_cache.json" not in payload


def test_encrypt_decrypt_blob_roundtrip(mock_shellpa_home):
    original_data = b"secret credentials text"
    passphrase = "super_secure_passphrase"
    encrypted = sync_manager.encrypt_blob(original_data, passphrase)
    assert encrypted != original_data

    decrypted = sync_manager.decrypt_blob(encrypted, passphrase)
    assert decrypted == original_data


# ─── Push Commits Tests ───────────────────────────────────────────────────────

def test_first_push_to_empty_repo(mock_shellpa_home):
    mock_client = MagicMock()
    mock_repo = MagicMock()
    mock_repo.default_branch = "main"

    # Set up get_commits() to simulate empty repository (Count is 0)
    mock_commits = MagicMock()
    mock_commits.totalCount = 0
    mock_repo.get_commits.return_value = mock_commits

    # Mock git blob & tree creation
    mock_blob = MagicMock()
    mock_blob.sha = "blob_sha_123"
    mock_repo.create_git_blob.return_value = mock_blob

    mock_tree = MagicMock()
    mock_tree.sha = "tree_sha_456"
    mock_repo.create_git_tree.return_value = mock_tree

    mock_commit = MagicMock()
    mock_commit.sha = "commit_sha_789"
    mock_repo.create_git_commit.return_value = mock_commit

    payload = {"snippets.json": b"content"}
    sync_manager.push(mock_client, mock_repo, payload, "Initial Backup")

    # Verify we did create_git_ref on first push instead of edit ref
    mock_repo.create_git_tree.assert_called_once()
    mock_repo.create_git_commit.assert_called_with("Initial Backup", "tree_sha_456", parents=[])
    mock_repo.create_git_ref.assert_called_with(ref="refs/heads/main", sha="commit_sha_789")
    mock_repo.get_ref.assert_not_called()


# ─── Read-only status & Diff tests ────────────────────────────────────────────

def test_status_does_not_mutate_db_or_files(mock_shellpa_home):
    # Set up local snippets
    cs_manager.add_snippet("ls", "list")
    local_count = len(cs_manager.get_all_snippets())

    mock_client = MagicMock()
    mock_repo = MagicMock()
    mock_repo.default_branch = "main"

    # Mock remote returning an added snippet
    mock_branch = MagicMock()
    mock_branch.commit.commit.tree.sha = "tree_sha"
    mock_repo.get_branch.return_value = mock_branch

    mock_element = MagicMock()
    mock_element.type = "blob"
    mock_element.path = "snippets.json"
    mock_element.sha = "blob_sha"

    mock_tree = MagicMock()
    mock_tree.tree = [mock_element]
    mock_repo.get_git_tree.return_value = mock_tree

    remote_snippets = [
        {"uuid": "new-remote-uuid", "command": "echo", "description": "desc", "tags": "t", "source": "s"}
    ]
    mock_blob = MagicMock()
    mock_blob.encoding = "utf-8"
    mock_blob.content = json.dumps(remote_snippets)
    mock_repo.get_git_blob.return_value = mock_blob

    # Run status check
    report = sync_manager.diff_status(mock_client, mock_repo)
    assert len(report["snippets"]["remote_added"]) == 1

    # Verify no local modifications were made
    assert len(cs_manager.get_all_snippets()) == local_count


# ─── Merge Snippets Logic Tests ───────────────────────────────────────────────

def test_merge_snippets_new_remote_uuid(mock_shellpa_home):
    report = {
        "snippets": {
            "remote_added": [
                {"uuid": "some-new-uuid", "command": "pwd", "description": "print working dir", "tags": "nav", "source": "remote"}
            ]
        },
        "dotfiles": {"remote_added": []}
    }
    sync_manager.apply_pull(report, resolved_snippets=[], resolved_dotfiles=[])

    local = cs_manager.get_all_snippets()
    assert len(local) == 1
    assert local[0]["uuid"] == "some-new-uuid"
    assert local[0]["command"] == "pwd"


def test_merge_snippets_same_uuid_same_command(mock_shellpa_home):
    # Setup identical uuid and command
    cs_manager.add_snippet("ls", "desc", snippet_uuid="uuid-123")
    report = {
        "snippets": {"remote_added": []},
        "dotfiles": {"remote_added": []}
    }
    # Pull should be clean no-op
    sync_manager.apply_pull(report, resolved_snippets=[], resolved_dotfiles=[])

    local = cs_manager.get_all_snippets()
    assert len(local) == 1
    assert local[0]["command"] == "ls"


def test_merge_snippets_same_uuid_diff_command_conflict(mock_shellpa_home):
    cs_manager.add_snippet("ls -la", "local desc", snippet_uuid="uuid-conflict")

    # Mismatch payload
    remote_snippet = {
        "uuid": "uuid-conflict",
        "command": "ls -lh",
        "description": "remote desc",
        "tags": "tags",
        "source": "remote"
    }

    # Verify pull resolution works when selecting Remote
    report = {
        "snippets": {"remote_added": []},
        "dotfiles": {"remote_added": []}
    }
    resolved = [("uuid-conflict", "remote", remote_snippet)]
    sync_manager.apply_pull(report, resolved_snippets=resolved, resolved_dotfiles=[])

    local = cs_manager.get_all_snippets()
    assert len(local) == 1
    assert local[0]["command"] == "ls -lh"  # remote overwrote local

    # Verify pull resolution works when selecting Both
    resolved_both = [("uuid-conflict", "both", remote_snippet)]
    sync_manager.apply_pull(report, resolved_snippets=resolved_both, resolved_dotfiles=[])

    local2 = cs_manager.get_all_snippets()
    assert len(local2) == 2  # local kept, remote inserted as a new row (fresh uuid)


# ─── Merge Dotfiles Logic Tests ───────────────────────────────────────────────

def test_merge_dotfiles_matching_hash(mock_shellpa_home):
    from shellpa.dotfiles.manager import BACKUP_DIR
    rel_path = "home/user/.vimrc"
    dest_path = os.path.join(str(BACKUP_DIR), rel_path)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "w") as f:
        f.write("syntax on")

    # If hashes match, pull should make no changes
    report = {
        "snippets": {"remote_added": []},
        "dotfiles": {"remote_added": []}
    }
    sync_manager.apply_pull(report, resolved_snippets=[], resolved_dotfiles=[])
    with open(dest_path, "r") as f:
        assert f.read() == "syntax on"


def test_merge_dotfiles_mismatched_hash_conflict(mock_shellpa_home):
    from shellpa.dotfiles.manager import BACKUP_DIR
    rel_path = "home/user/.vimrc"
    dest_path = os.path.join(str(BACKUP_DIR), rel_path)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "w") as f:
        f.write("syntax on")

    remote_bytes = b"syntax off"
    resolved = [(rel_path, "remote", remote_bytes)]
    report = {
        "snippets": {"remote_added": []},
        "dotfiles": {"remote_added": []}
    }

    # Pull overwrite selection
    sync_manager.apply_pull(report, resolved_snippets=[], resolved_dotfiles=resolved)
    with open(dest_path, "r") as f:
        assert f.read() == "syntax off"
