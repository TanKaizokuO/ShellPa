# Shellpa (sp)

![Python Version](https://img.shields.io/badge/python-3.10%2B-blue)

A single CLI that manages your dotfiles, remembers your shell commands, and asks an LLM when you forget the syntax.

---

## Features

### Dotfiles Manager
* **Backup & Restore**: Track and copy files like `~/.bashrc` to `~/.shellpa/dotfiles/` and restore them with diff confirmations.
* **Status Monitoring**: Compare your live dotfiles against backup copies using incremental SHA-256 hashing.
* **Safety Guards**: Prompts for confirmation before overwriting existing files and ensures directory writability.

### Snippet Cheatsheet
* **Fuzzy Search**: Instantly filter and execute your saved commands in a highly interactive `fzf` terminal UI.
* **Keybound Actions**: Run, copy, edit-and-run, or delete snippets directly from the search screen.
* **Usage Statistics**: Tracks snippet usage count and timestamps to sort commands by popularity.

### AI Assistant (NVIDIA NIM)
* **Natural Language Translate**: Translate plain English tasks into fully qualified shell commands with safety backstops for destructive commands.
* **Command Explanation**: Break down complex shell commands flag by flag in human-readable Markdown format.
* **Failure Debugger**: Read your terminal's history, re-run failing commands, and propose corrections using the LLM.

### Repository Sync
* **Zero Local Git Requirement**: Backs up configuration data directly using GitHub's REST/Git Data API.
* **Passphrase-based Encryption**: Opt-in to encrypting your snippet DB and dotfiles backups at rest before uploading.
* **Interactive Merging**: Visual diff conflict resolution for mismatched snippet database UUIDs and dotfiles.

---

## Installation

### Prerequisites
- **Python 3.10+**
- **fzf** (Required for fuzzy snippet search)
- **libsecret / Keyring daemon** (Optional, falls back to unencrypted file if not found)

### Steps
1. Clone this repository:
   ```bash
   git clone https://github.com/your-username/Shellpa.git
   cd Shellpa
   ```
2. Create and synchronize a virtual environment using `uv`:
   ```bash
   uv venv
   uv sync
   ```
3. Install the project in editable mode to expose the `sp` script:
   ```bash
   uv pip install -e .
   ```
4. Verify the installation:
   ```bash
   uv run sp --help
   ```

---

## Configuration

All local data and configurations are managed in `~/.shellpa/`.

### Config File (`~/.shellpa/config.toml`)
```toml
[dotfiles]
files = [
  "~/.bashrc",
  "~/.zshrc",
  "~/.vimrc",
  "~/.tmux.conf"
]

[ai]
# The model used for translation and explanation
model = "meta/llama-3.1-70b-instruct"

[sync]
repo = "shellpa-backup"
username = "github-username"
encryption = false # Set to true if encryption-at-rest is enabled
```
*Note: The `[ai]` section should never contain API keys. Always supply secret credentials through environment variables.*

### Environment Variables

| Variable | Required for | Notes |
|---|---|---|
| `NVIDIA_API_KEY` | `sp ask/explain/fix` | Mandated API key for NIM connection. Crucial security guard: never stored in `config.toml`. |
| `EDITOR` | cheatsheet & AI edit | Text editor to launch for editing commands. Defaults to `nano` or `vi`. |

### Directory Layout
```
~/.shellpa/
├── config.toml      # Configures dotfiles, AI models, and repository mappings.
├── dotfiles/        # Directory containing local backup copies of tracked files.
├── snippets.db      # SQLite database storing your snippets with auto-generated UUIDs.
├── ai_cache.json    # Local cache mapping natural language queries to commands.
├── meta.json        # Tracks dotfile path hashes and timestamps to support incremental backups.
└── .github_token    # Plaintext token fallback file (permissions 600). Only created if no OS keyring backend exists.
```

---

## Command Reference

### Dotfiles

- **Add a dotfile for tracking**:
  ```bash
  uv run sp dotfiles add ~/.bashrc
  ```
- **Backup tracked files to local repository**:
  ```bash
  uv run sp dotfiles backup
  ```
- **Restore backup copy to system location**:
  ```bash
  uv run sp dotfiles restore
  ```
  *(Or restore a single file: `uv run sp dotfiles restore ~/.bashrc`)*
- **Compare live file hashes against backups**:
  ```bash
  uv run sp dotfiles status
  ```
- **Remove a file from tracking list**:
  ```bash
  uv run sp dotfiles remove ~/.bashrc
  ```

### Cheatsheet

- **Save a command interactively**:
  ```bash
  uv run sp cheatsheet add
  # Prompts:
  # Command: docker compose up -d
  # Description: Start container services detached
  # Tags: docker,compose
  ```
- **List all saved snippets**:
  ```bash
  uv run sp cheatsheet list
  ```
- **Modify a saved snippet by ID**:
  ```bash
  uv run sp cheatsheet edit 3
  ```
- **Append a tag to a snippet**:
  ```bash
  uv run sp cheatsheet tag 3 devops
  ```
- **Delete a snippet**:
  ```bash
  uv run sp cheatsheet delete 3
  ```
- **Fuzzy Search Interface (Top-level command)**:
  ```bash
  uv run sp search
  ```

#### Search Keybindings (`sp search`)
When inside the fuzzy finder:
- `Enter` — Executes the command in your current shell.
- `Ctrl+Y` — Copies the command to your clipboard.
- `Ctrl+E` — Opens command in your `$EDITOR` to modify before executing (updates back to DB).
- `Ctrl+D` — Prompts to delete the selected snippet from the database.

### AI Assistant

- **Convert natural language into a shell command**:
  ```bash
  uv run sp ask "extract a tar.gz file"
  ```
- **Explain a complex command flag-by-flag**:
  ```bash
  uv run sp explain "find . -type f -name '*.py' -exec grep -l 'import os' {} +"
  ```
- **Debug and fix the last failing command**:
  ```bash
  uv run sp fix
  ```

#### Interactive AI Menu (`sp ask` & `sp fix`)
After generating a command or a fix, you will be prompted with:
- `Run` — Executes the suggested command in your shell.
- `Save` — Adds the suggested command directly into your cheatsheet DB.
- `Explain` — Returns a markdown breakdown of what the command does (`sp ask` only).
- `Edit` — Opens `$EDITOR` to tweak the command before choosing another action.
- `Cancel` — Exits without running or saving.

### Sync

- **Configure setup wizard**:
  ```bash
  uv run sp sync setup
  ```
- **Upload local backups**:
  ```bash
  uv run sp sync push
  ```
- **Pull remote commits and resolve conflicts**:
  ```bash
  uv run sp sync pull
  ```
- **Dry-run comparison against remote branch**:
  ```bash
  uv run sp sync status
  ```
- **Configure automatic background sync**:
  ```bash
  uv run sp sync auto --interval-hours 12
  ```
  *(Disable with: `uv run sp sync auto --disable`)*

---

## How the Modules Connect

All modules are designed to integrate seamlessly. AI recommendations can be direct pathways into your cheatsheet, and both your cheatsheet and dotfile backups are handled collectively by the Sync subsystem:

```
                  ┌───────────────────────┐
                  │    sp ask / sp fix    │
                  └───────────┬───────────┘
                              │ (Save choice)
                              ▼
┌──────────────┐  ┌───────────────────────┐
│ Live System  ├─►│     Cheatsheet DB     │
│  (.bashrc)   │  │     (snippets.db)     │
└──────┬───────┘  └───────────┬───────────┘
       │                      │
       │ (sp dotfiles backup) │
       ▼                      ▼
┌──────────────┐  ┌───────────────────────┐
│ Backup Tree  │  │ Exported JSON payload │
│ (dotfiles/)  │  │    (snippets.json)    │
└──────┬───────┘  └───────────┬───────────┘
       │                      │
       └──────────┬───────────┘
                  │ (sp sync push)
                  ▼
         ┌──────────────────┐
         │ Private GitHub   │
         │ Backup Repository│
         └──────────────────┘
```

---

## Project Structure

```
shellpa/
├── __init__.py
├── main.py          # Root Typer CLI application entry point.
├── ai/              # AI translator, debugger, and explainers (NVIDIA NIM).
│   ├── __init__.py
│   ├── cli.py
│   └── manager.py
├── cheatsheet/      # Snippet CRUD operations and fzf shell integration.
│   ├── __init__.py
│   ├── cli.py
│   └── manager.py
├── dotfiles/        # Incremental backup copy logic and paths helper.
│   ├── __init__.py
│   ├── cli.py
│   └── manager.py
└── sync/            # Remote repository sync, conflict diffs, and cron.
    ├── __init__.py
    ├── cli.py
    └── manager.py
```

---

## Development & Testing

All test suites are written with `pytest` and use mocked endpoints to avoid reaching out to live network backends or altering your real local environment.

### Installation for Development
Install all dependencies (including development groups):
```bash
uv sync
```

### Running Tests
- Run all tests:
  ```bash
  uv run pytest -v
  ```
- Run a specific test module (e.g., Sync):
  ```bash
  uv run pytest tests/test_sync.py -v
  ```

#### Test Isolation via `mock_shellpa_home`
To prevent test runs from modifying your actual `~/.shellpa` configurations or database, all test files make use of the `mock_shellpa_home` fixture defined in `tests/conftest.py`. This fixture dynamically mocks the `HOME` environment variable to point to a temporary test directory for the duration of the test. All managers use the `DynamicPath` class to resolve directory paths on-demand, ensuring tests are fully sandboxed.

---

## Security Notes

- **Secrets Sanitation**: `sp sync` will run a sanitation check before uploading any configurations. If a raw NVIDIA NIM API Key or GitHub PAT is found in your `config.toml`, the operation is aborted instantly.
- **Environment Key Storage**: `NVIDIA_API_KEY` is never written to disk by the application. It must always be passed as an environment variable.
- **Repository Visibility Safety**: During configuration or before running `push`/`pull` operations, the visibility of your backup repository is queried. If the repository is set to public, the operation is blocked to prevent exposing your private commands or configs.
- **Plaintext Key Fallback**: The GitHub authentication token is stored inside your OS Keyring. If no keyring daemon is active, it falls back to writing a plaintext `.github_token` file inside `~/.shellpa/` with restricted `0o600` permissions. The CLI prints a persistent warning on execution to ensure visibility.

---

## Known Limitations

- **macOS Launchd Support**: Auto-sync recurring scheduling via `launchd` is currently stubbed and not implemented. Running `sp sync auto` on a non-Linux system will raise a platform error.
- **Git Data API Constraints**: Because all operations run directly against the GitHub Git Data API without local Git command dependencies, sync speed is determined by HTTP payload round-trips. Syncing huge trees or large numbers of files may hit GitHub API rate limits.
- **Subprocess Shell History**: Reading failed commands for `sp fix` relies on parsing local shell history files (such as `~/.bash_history` or `~/.zsh_history`). Commands that are run inside subshells or that are not immediately written to the filesystem by the host shell may not be captured.

---

## License

Check the `LICENSE` file in the root of the repository for license details.
*(Placeholder: If no LICENSE file is present, default constraints apply).*
