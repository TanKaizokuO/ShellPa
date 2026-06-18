import os
import json
import pytest
import toml
from typer.testing import CliRunner
from shellpa.main import app
from shellpa.dotfiles import manager

runner = CliRunner()

def test_default_config_creation(mock_shellpa_home):
    # Verify config.toml is created automatically on first load_config call
    config = manager.load_config()
    assert "dotfiles" in config
    assert "files" in config["dotfiles"]
    assert os.path.exists(os.path.join(mock_shellpa_home, "config.toml"))

def test_add_file_validation(mock_shellpa_home, tmp_path):
    # Test adding a directory should fail
    dir_path = tmp_path / "mydir"
    dir_path.mkdir()
    result = runner.invoke(app, ["dotfiles", "add", str(dir_path)])
    assert result.exit_code != 0
    assert "Error" in result.output

    # Test adding a file that doesn't exist (warn but add anyway)
    non_existent = tmp_path / "missing.txt"
    result = runner.invoke(app, ["dotfiles", "add", str(non_existent)])
    assert result.exit_code == 0
    assert "Warning" in result.output

    # Verify it was added to config
    config = manager.load_config()
    assert str(non_existent) in config["dotfiles"]["files"]

def test_add_duplicate_file(mock_shellpa_home, tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("hello")

    # Add first time
    result1 = runner.invoke(app, ["dotfiles", "add", str(file_path)])
    assert result1.exit_code == 0

    # Add second time
    result2 = runner.invoke(app, ["dotfiles", "add", str(file_path)])
    assert result2.exit_code == 0
    assert "Already tracked" in result2.output

    # Verify it is in config only once
    config = manager.load_config()
    matches = [f for f in config["dotfiles"]["files"] if os.path.abspath(os.path.expanduser(f)) == os.path.abspath(file_path)]
    assert len(matches) == 1

def test_remove_nonexistent_file(mock_shellpa_home):
    result = runner.invoke(app, ["dotfiles", "remove", "~/nottracked.txt"])
    assert result.exit_code != 0
    assert "Error" in result.output

def test_backup_skips_missing_file(mock_shellpa_home, tmp_path):
    # Setup config with one existing file and one missing file
    existing_file = tmp_path / "existing.txt"
    existing_file.write_text("content")
    missing_file = tmp_path / "missing.txt"

    config = {
        "dotfiles": {
            "files": [str(existing_file), str(missing_file)]
        }
    }
    manager.save_config(config)

    result = runner.invoke(app, ["dotfiles", "backup"])
    assert result.exit_code == 0
    assert "Warning: Source file" in result.output
    assert "Backed up" in result.output

    # Verify existing file was backed up, missing was skipped
    backup_existing = manager.get_backup_path(str(existing_file))
    backup_missing = manager.get_backup_path(str(missing_file))
    assert os.path.exists(backup_existing)
    assert not os.path.exists(backup_missing)

def test_backup_incremental_same_hash(mock_shellpa_home, tmp_path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("original content")

    config = {"dotfiles": {"files": [str(test_file)]}}
    manager.save_config(config)

    # First backup
    res1 = runner.invoke(app, ["dotfiles", "backup"])
    assert "Backed up" in res1.output

    # Second backup with no changes
    res2 = runner.invoke(app, ["dotfiles", "backup"])
    assert "up to date" in res2.output

    # Update file and third backup
    test_file.write_text("updated content")
    res3 = runner.invoke(app, ["dotfiles", "backup"])
    assert "Backed up" in res3.output

def test_restore_no_backup_exists(mock_shellpa_home, tmp_path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("some content")

    config = {"dotfiles": {"files": [str(test_file)]}}
    manager.save_config(config)

    # Try restoring when no backup exists
    res = runner.invoke(app, ["dotfiles", "restore", str(test_file)])
    assert "Warning: No backup exists" in res.output

def test_restore_success(mock_shellpa_home, tmp_path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("original content")

    config = {"dotfiles": {"files": [str(test_file)]}}
    manager.save_config(config)

    # Backup
    runner.invoke(app, ["dotfiles", "backup"])

    # Modify original file
    test_file.write_text("modified content")

    # Restore single file (with prompt confirmation 'y')
    res = runner.invoke(app, ["dotfiles", "restore", str(test_file)], input="y\n")
    assert res.exit_code == 0
    assert "Restored" in res.output
    assert test_file.read_text() == "original content"

def test_restore_all_success(mock_shellpa_home, tmp_path):
    f1 = tmp_path / "f1.txt"
    f1.write_text("f1 content")
    f2 = tmp_path / "f2.txt"
    f2.write_text("f2 content")

    config = {"dotfiles": {"files": [str(f1), str(f2)]}}
    manager.save_config(config)

    # Backup
    runner.invoke(app, ["dotfiles", "backup"])

    # Modify both
    f1.write_text("f1 modified")
    f2.write_text("f2 modified")

    # Restore all (with global confirmation 'y')
    res = runner.invoke(app, ["dotfiles", "restore"], input="y\n")
    assert res.exit_code == 0
    assert "Restored" in res.output
    assert f1.read_text() == "f1 content"
    assert f2.read_text() == "f2 content"

def test_meta_json_corrupted(mock_shellpa_home):
    meta_file = os.path.join(mock_shellpa_home, "meta.json")
    with open(meta_file, "w") as f:
        f.write("invalid json string {")

    # This should warn and load empty metadata without crashing
    meta = manager.load_metadata()
    assert meta == {}

def test_status_and_list_commands(mock_shellpa_home, tmp_path):
    f1 = tmp_path / "f1.txt"
    f1.write_text("f1 content")

    config = {"dotfiles": {"files": [str(f1)]}}
    manager.save_config(config)

    # List command
    res_list = runner.invoke(app, ["dotfiles", "list"])
    assert res_list.exit_code == 0
    assert str(f1) in res_list.output

    # Status command (no backup state)
    res_status = runner.invoke(app, ["dotfiles", "status"])
    assert res_status.exit_code == 0
    assert "no backup" in res_status.output

    # Backup and check status again
    runner.invoke(app, ["dotfiles", "backup"])
    res_status2 = runner.invoke(app, ["dotfiles", "status"])
    assert "in-sync" in res_status2.output or "exists" in res_status2.output

def test_remove_file_with_backup_deletion(mock_shellpa_home, tmp_path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("some content")

    config = {"dotfiles": {"files": [str(test_file)]}}
    manager.save_config(config)

    # Backup
    runner.invoke(app, ["dotfiles", "backup"])

    backup_path = manager.get_backup_path(str(test_file))
    assert os.path.exists(backup_path)

    # Remove file and confirm backup deletion
    res = runner.invoke(app, ["dotfiles", "remove", str(test_file)], input="y\n")
    assert res.exit_code == 0
    assert "Removing" in res.output
    assert "Delete backup too?" in res.output

    # Verify backup is deleted and not in config
    assert not os.path.exists(backup_path)
    config = manager.load_config()
    assert str(test_file) not in config["dotfiles"]["files"]
