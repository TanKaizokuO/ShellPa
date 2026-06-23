# ShellPa

## Overview
* **Vision/Goal:** A unified CLI tool to manage dotfiles, search and execute saved shell snippets, and use an LLM to translate natural language to shell commands or debug terminal errors.
* **Current Status:** MVP / Active Development (v0.1.0)

## Tech Stack
* **Language/Runtime:** Python 3.10+
* **Frameworks/Libraries:** Typer (CLI routing), Rich (terminal formatting), fzf (fuzzy finding UI), SQLite (built-in, for snippets DB).
* **Key Dependencies:** OpenAI SDK (for NVIDIA NIM API), PyGithub (for Git Data API sync), Keyring (secret management), Cryptography (encryption at rest), Python-crontab (background sync scheduling).

## Directory Structure
```text
shellpa/
├── main.py          # Root Typer CLI application entry point
├── ai/              # AI translator, debugger, and explainers (NVIDIA NIM)
│   ├── cli.py
│   └── manager.py
├── cheatsheet/      # Snippet CRUD operations and fzf shell integration
│   ├── cli.py
│   └── manager.py
├── dashboard/       # Interactive terminal dashboard and shell handoff
│   ├── cli.py
│   └── manager.py
├── dotfiles/        # Incremental backup copy logic and paths helper
│   ├── cli.py
│   └── manager.py
└── sync/            # Remote repository sync, conflict diffs, and cron
    ├── cli.py
    └── manager.py
```

## Core Logic & Data Flow
* **AI-Assisted Cheatsheet Ingestion:** Users execute `sp ask` or `sp fix` which calls the NVIDIA NIM LLM. Suggested commands can be interactively executed or directly saved into the Cheatsheet SQLite database (`~/.shellpa/snippets.db`).
* **Dotfiles Backup & Status Monitoring:** Tracked files defined in `~/.shellpa/config.toml` are hashed (SHA-256) and copied to `~/.shellpa/dotfiles/`. The system monitors `meta.json` to detect drift between the live system and backup copies.
* **Agentless Remote Sync:** Both the local dotfiles backups and an exported JSON representation of the cheatsheet DB are synchronized to a private GitHub repository via `sp sync push`/`pull`. This uses the GitHub Git Data API to avoid requiring a local `git` installation, and supports opt-in passphrase encryption before upload.

## Environment & Setup
* **Prerequisites:** Python 3.10+, `fzf` (required for snippet search), `uv` (recommended for package management), and `libsecret`/Keyring daemon (optional).
* **Environment Variables:** 
  * `NVIDIA_API_KEY`: Required for all AI commands. Never stored in the config file.
  * `EDITOR`: Used for interactive command editing.
* **Essential Commands:**
  * Install dependencies: `uv sync`
  * Install in editable mode: `uv pip install -e .`
  * Run test suite (fully sandboxed via fixtures): `uv run pytest -v`

## Development Conventions
* **CLI Architecture:** The CLI is built with `Typer`. Subsystems are cleanly separated into packages (`ai`, `cheatsheet`, `sync`, etc.), each containing a `cli.py` for command routing and a `manager.py` for the core business logic.
* **State Management:** All local data is strictly confined to `~/.shellpa/`, including `config.toml`, `snippets.db`, and `ai_cache.json`.
* **Testing Standards:** Tests must use the `mock_shellpa_home` fixture to redirect `HOME` to a temporary directory, ensuring developers' actual configurations and databases are never mutated during test runs.

## Known Issues / Debt
* **macOS Sync Limitations:** Auto-sync via `launchd` is currently stubbed and unimplemented; `sp sync auto` will raise a platform error on non-Linux systems.
* **Sync API Rate Limits:** Because the sync engine uses the GitHub Git Data API (HTTP payload round-trips) instead of a local Git binary, syncing very large file trees may hit rate limits or perform slowly.
* **Shell History Parsing:** The `sp fix` command parses local shell history files (e.g., `~/.bash_history`). It may miss commands executed inside subshells or commands that haven't yet been flushed to disk by the host shell.
* **Terminal Multiplexer UX:** If configured as a Kitty login shell, new panes or windows created inside `tmux` will bypass the dashboard and drop straight into the host shell.
