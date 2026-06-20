# Shellpa (sp) Project Description

Shellpa is an advanced command-line interface (CLI) tool designed to streamline developer workflows by acting as a comprehensive dotfiles manager, a persistent snippet cheatsheet, and an AI-powered shell assistant. It integrates system administration tasks with natural language processing to enhance terminal productivity.

## Core Capabilities

1. **Dotfiles Management**
   - Seamlessly back up and restore configuration files (e.g., `~/.bashrc`, `~/.zshrc`) to a centralized local directory (`~/.shellpa/dotfiles/`).
   - Monitor the status of live dotfiles against backup copies using incremental SHA-256 hashing.
   - Built-in safety mechanisms with confirmation prompts to prevent accidental overwrites.

2. **Snippet Cheatsheet**
   - Save, tag, and manage frequently used shell commands in a local SQLite database (`snippets.db`).
   - Highly interactive fuzzy search interface powered by `fzf` for instantly finding and executing saved commands.
   - Keybindings for quick actions: run, copy to clipboard, edit in `$EDITOR` before execution, or delete snippets.
   - Usage statistics tracking to automatically sort popular commands.

3. **AI Assistant (Powered by NVIDIA NIM)**
   - **Translate**: Convert natural language task descriptions into fully qualified shell commands with safety backstops for destructive operations.
   - **Explain**: Provide detailed, flag-by-flag breakdowns of complex shell commands in human-readable Markdown.
   - **Debug/Fix**: Read local shell history, re-run failing commands, and propose corrections intelligently using the LLM.

4. **Repository Syncing**
   - Direct integration with GitHub's REST/Git Data API, eliminating the need for a local Git dependency.
   - Synchronize configuration files and snippet databases directly to a private GitHub repository.
   - Passphrase-based, opt-in encryption-at-rest to securely store sensitive snippets and backups.
   - Interactive merging with visual diffs for resolving conflict mismatches between local and remote states.

## Architecture Highlights
- Built in **Python 3.10+**.
- Modular architecture comprising `ai`, `cheatsheet`, `dotfiles`, and `sync` subsystems.
- All local configurations and data are safely sandboxed in `~/.shellpa/`.
- Secret sanitation to prevent accidental uploads of API keys (e.g., NVIDIA NIM, GitHub tokens) during remote synchronization.
