import os
import pytest
from unittest.mock import patch, MagicMock
from shellpa.dashboard import manager

def test_resolve_login_shell_returns_passwd_entry():
    with patch("os.getuid", return_value=1000), \
         patch("pwd.getpwuid") as mock_pw:
        mock_entry = MagicMock()
        mock_entry.pw_shell = "/bin/zsh"
        mock_pw.return_value = mock_entry
        
        shell = manager.resolve_login_shell()
        assert shell == "/bin/zsh"

def test_resolve_login_shell_recursion_guard_falls_back_to_bash():
    # Test with basename "sp"
    with patch("os.getuid", return_value=1000), \
         patch("pwd.getpwuid") as mock_pw:
        mock_entry = MagicMock()
        mock_entry.pw_shell = "/home/user/.local/bin/sp"
        mock_pw.return_value = mock_entry
        
        shell = manager.resolve_login_shell()
        assert shell == "/bin/bash"

    # Test with basename "shellpa"
    with patch("os.getuid", return_value=1000), \
         patch("pwd.getpwuid") as mock_pw:
        mock_entry = MagicMock()
        mock_entry.pw_shell = "/usr/bin/shellpa"
        mock_pw.return_value = mock_entry
        
        shell = manager.resolve_login_shell()
        assert shell == "/bin/bash"

def test_gather_stats_handles_missing_snippets_db(mock_shellpa_home):
    with patch("shellpa.dashboard.manager.get_all_snippets", side_effect=Exception("DB Error")), \
         patch("shellpa.dashboard.manager.get_status_data", return_value=[]), \
         patch("shellpa.dashboard.manager.load_config", return_value={}), \
         patch("shellpa.dashboard.manager.load_cache", return_value=[]):
        
        stats = manager.gather_stats()
        assert stats["cheatsheet"] == "—"
        assert stats["dotfiles"] == "0 tracked (0 out-of-sync, 0 missing)"
        assert stats["last_sync"] == "never"
        assert stats["ai_cache"] == "0 cached responses"

def test_gather_stats_handles_missing_dotfiles_config(mock_shellpa_home):
    with patch("shellpa.dashboard.manager.get_all_snippets", return_value=[]), \
         patch("shellpa.dashboard.manager.get_status_data", side_effect=Exception("Config missing")), \
         patch("shellpa.dashboard.manager.load_config", return_value={}), \
         patch("shellpa.dashboard.manager.load_cache", return_value=[]):
        
        stats = manager.gather_stats()
        assert stats["cheatsheet"] == "0 snippets, 0 used in last 7 days"
        assert stats["dotfiles"] == "—"

def test_get_status_data_returns_structured_list(mock_shellpa_home):
    from shellpa.dotfiles.manager import get_status_data
    data = get_status_data()
    assert isinstance(data, list)
    assert len(data) > 0
    for item in data:
        assert "configured_path" in item
        assert "disk_status" in item
        assert "sync_status" in item
        assert "last_backup" in item

def test_dashboard_enabled_false_skips_menu(mock_shellpa_home):
    with patch("shellpa.dashboard.manager.load_config") as mock_load, \
         patch("shellpa.dashboard.manager.enter_shell", side_effect=SystemExit) as mock_enter:
        mock_load.return_value = {"dashboard": {"enabled": False}}
        
        with pytest.raises(SystemExit):
            manager.run_dashboard()
        mock_enter.assert_called_once()
