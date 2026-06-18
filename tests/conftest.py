import pytest
import os
from pathlib import Path

@pytest.fixture
def mock_shellpa_home(tmp_path, monkeypatch):
    shellpa_dir = tmp_path / ".shellpa"
    shellpa_dir.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    # We should also ensure the dotfiles directory exists inside it for tests
    (shellpa_dir / "dotfiles").mkdir()
    return shellpa_dir
