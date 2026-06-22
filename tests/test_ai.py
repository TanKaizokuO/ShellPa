import os
import json
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

from shellpa.ai import manager as ai_manager
from shellpa.ai.manager import AIError


# ─── call_nim tests ───────────────────────────────────────────────────────────

def test_call_nim_wraps_auth_error(mock_shellpa_home, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "fake-key")
    from openai import AuthenticationError
    with patch("shellpa.ai.manager.get_client") as mock_client_fn:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client
        mock_client.chat.completions.create.side_effect = AuthenticationError(
            message="auth failed", response=MagicMock(status_code=401, headers={}), body={}
        )
        with pytest.raises(AIError, match="Authentication failed"):
            ai_manager.call_nim("system", "user")


def test_call_nim_wraps_connection_error(mock_shellpa_home, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "fake-key")
    from openai import APIConnectionError
    with patch("shellpa.ai.manager.get_client") as mock_client_fn:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client
        mock_client.chat.completions.create.side_effect = APIConnectionError(request=MagicMock())
        with pytest.raises(AIError, match="Could not reach NVIDIA NIM"):
            ai_manager.call_nim("system", "user")


def test_call_nim_raises_on_empty_response(mock_shellpa_home, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "fake-key")
    with patch("shellpa.ai.manager.get_client") as mock_client_fn:
        mock_client = MagicMock()
        mock_client_fn.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "   "  # whitespace only
        mock_client.chat.completions.create.return_value = mock_response
        with pytest.raises(AIError, match="Empty response"):
            ai_manager.call_nim("system", "user")


# ─── ask tests ────────────────────────────────────────────────────────────────

def test_ask_returns_command(mock_shellpa_home, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "fake-key")
    with patch("shellpa.ai.manager.call_nim", return_value="ls -la") as mock_nim:
        cmd, from_cache = ai_manager.ask("list files")
    assert cmd == "ls -la"
    assert from_cache is False
    mock_nim.assert_called_once()


def test_ask_uses_cache_on_repeat_query(mock_shellpa_home, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "fake-key")
    with patch("shellpa.ai.manager.call_nim", return_value="ls -la") as mock_nim:
        # First call — populates cache
        ai_manager.ask("list files")
        # Second call — should hit cache, not call NIM again
        cmd, from_cache = ai_manager.ask("list files")

    assert cmd == "ls -la"
    assert from_cache is True
    assert mock_nim.call_count == 1  # Only called once


def test_ask_cache_capped_at_ten(mock_shellpa_home, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "fake-key")
    call_count = [0]

    def fake_nim(system_prompt, user_content, **kwargs):
        call_count[0] += 1
        return f"cmd_{call_count[0]}"

    with patch("shellpa.ai.manager.call_nim", side_effect=fake_nim):
        # Ask 12 different queries
        for i in range(12):
            ai_manager.ask(f"query number {i}")

    cache = ai_manager.load_cache()
    assert len(cache) <= 10


# ─── explain tests ────────────────────────────────────────────────────────────

def test_explain_returns_text(mock_shellpa_home, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "fake-key")
    expected = "## `ls -la`\n- `-l`: long format\n- `-a`: show hidden"
    with patch("shellpa.ai.manager.call_nim", return_value=expected):
        result = ai_manager.explain("ls -la")
    assert result == expected


# ─── suggest_fix tests ────────────────────────────────────────────────────────

def test_suggest_fix_returns_none_on_no_fix_available(mock_shellpa_home, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "fake-key")
    with patch("shellpa.ai.manager.call_nim", return_value="NO_FIX_AVAILABLE"):
        result = ai_manager.suggest_fix("bad cmd", 1, "some error")
    assert result is None


# ─── is_dangerous tests ───────────────────────────────────────────────────────

def test_is_dangerous_detects_rm_rf(mock_shellpa_home):
    assert ai_manager.is_dangerous("rm -rf /tmp/test") is True
    assert ai_manager.is_dangerous("sudo rm -rf /") is True
    assert ai_manager.is_dangerous("mkfs.ext4 /dev/sda") is True
    assert ai_manager.is_dangerous("DROP TABLE users") is True   # case-insensitive
    assert ai_manager.is_dangerous("drop table users") is True


def test_is_dangerous_false_on_safe_command(mock_shellpa_home):
    assert ai_manager.is_dangerous("ls -la") is False
    assert ai_manager.is_dangerous("echo hello") is False
    assert ai_manager.is_dangerous("git status") is False


# ─── history tests ────────────────────────────────────────────────────────────

def test_get_last_history_command_bash(mock_shellpa_home, tmp_path, monkeypatch):
    hist_file = tmp_path / ".bash_history"
    hist_file.write_text("echo hello\nls -la\ngit status\n")
    monkeypatch.setenv("HISTFILE", str(hist_file))
    monkeypatch.setenv("SHELL", "/bin/bash")

    result = ai_manager.get_last_history_command()
    assert result == "git status"


def test_get_last_history_command_zsh_strips_timestamp(mock_shellpa_home, tmp_path, monkeypatch):
    hist_file = tmp_path / ".zsh_history"
    # zsh extended history format: ': timestamp:0;command'
    hist_file.write_text(": 1718700000:0;ls -la\n: 1718700060:0;git push\n")
    monkeypatch.setenv("HISTFILE", str(hist_file))
    monkeypatch.setenv("SHELL", "/bin/zsh")

    result = ai_manager.get_last_history_command()
    assert result == "git push"


def test_get_last_history_command_missing_file_returns_none(mock_shellpa_home, tmp_path, monkeypatch):
    monkeypatch.setenv("HISTFILE", str(tmp_path / "nonexistent_history"))
    monkeypatch.delenv("SHELL", raising=False)

    result = ai_manager.get_last_history_command()
    assert result is None


def test_handle_ask_result_menu_dispatch(mock_shellpa_home):
    # Test that choices inside handle_ask_result work
    # We will test Run ('r'), Save ('s'), and Cancel ('c')
    
    # Choice 'r': mock input to return 'r', mock subprocess.run
    with patch("builtins.input", return_value="r"), \
         patch("subprocess.run") as mock_run:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result
        
        ai_manager.handle_ask_result("echo test", "test query")
        mock_run.assert_called_once_with("echo test", shell=True)

    # Choice 's': mock input to return 's', mock add_snippet
    with patch("builtins.input", return_value="s"), \
         patch("shellpa.cheatsheet.manager.add_snippet", return_value=42) as mock_add:
        ai_manager.handle_ask_result("echo test", "test query")
        mock_add.assert_called_once_with("echo test", "test query", tags="ai", source="ai")

    # Choice 'c': mock input to return 'c'
    with patch("builtins.input", return_value="c"):
        ai_manager.handle_ask_result("echo test", "test query")
