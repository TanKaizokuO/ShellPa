import os
import shutil
import json
import hashlib
import difflib
from datetime import datetime
from typing import Optional, Dict, List, Tuple
import toml
from rich.console import Console
from rich.table import Table

console = Console()

class DynamicPath:
    def __init__(self, func):
        self.func = func
    def __fspath__(self) -> str:
        return self.func()
    def __str__(self) -> str:
        return self.func()
    def __repr__(self) -> str:
        return self.func()
    def __eq__(self, other) -> bool:
        return str(self) == str(other)
    def __hash__(self) -> int:
        return hash(str(self))

CONFIG_DIR = DynamicPath(lambda: os.path.expanduser("~/.shellpa"))
CONFIG_PATH = DynamicPath(lambda: os.path.join(str(CONFIG_DIR), "config.toml"))
META_PATH = DynamicPath(lambda: os.path.join(str(CONFIG_DIR), "meta.json"))
BACKUP_DIR = DynamicPath(lambda: os.path.join(str(CONFIG_DIR), "dotfiles"))

DEFAULT_FILES = ["~/.bashrc", "~/.zshrc", "~/.vimrc", "~/.tmux.conf"]

def load_config() -> dict:
    """Loads config.toml safely. Auto-creates if missing."""
    if not os.path.exists(CONFIG_PATH):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        default_config = {
            "dotfiles": {
                "files": DEFAULT_FILES
            },
            "ai": {
                "api_key": ""
            },
            "sync": {
                "repo": "shellpa-backup",
                "username": "your-github-username"
            }
        }
        try:
            with open(CONFIG_PATH, "w") as f:
                toml.dump(default_config, f)
        except Exception as e:
            console.print(f"[red]Error: Could not write default config to {CONFIG_PATH}: {e}[/red]")
        return default_config

    try:
        with open(CONFIG_PATH, "r") as f:
            return toml.load(f)
    except Exception as e:
        console.print(f"[yellow]Warning: Could not parse config file {CONFIG_PATH}: {e}. Using defaults.[/yellow]")
        return {"dotfiles": {"files": DEFAULT_FILES}}

def save_config(config: dict) -> None:
    """Writes configuration to config.toml."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            toml.dump(config, f)
    except Exception as e:
        console.print(f"[red]Error: Could not save config to {CONFIG_PATH}: {e}[/red]")

def load_metadata() -> dict:
    """Loads meta.json safely. Resets to empty dict if corrupted."""
    if not os.path.exists(META_PATH):
        return {}
    try:
        with open(META_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        console.print(f"[yellow]Warning: Metadata file {META_PATH} is corrupted: {e}. Resetting to empty.[/yellow]")
        # Reset to empty dict
        save_metadata({})
        return {}

def save_metadata(meta: dict) -> None:
    """Saves metadata to meta.json."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(META_PATH, "w") as f:
            json.dump(meta, f, indent=2)
    except Exception as e:
        console.print(f"[red]Error: Could not save metadata to {META_PATH}: {e}[/red]")

def calculate_hash(file_path: str) -> str:
    """Computes the SHA-256 hash of a file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

def get_backup_path(file_path: str) -> str:
    """Maps absolute file path to its backup location under BACKUP_DIR."""
    abs_path = os.path.abspath(os.path.expanduser(file_path))
    # Remove leading slash (on Linux)
    rel_path = abs_path.lstrip(os.path.sep)
    return os.path.join(BACKUP_DIR, rel_path)

def check_dir_writable(path: str) -> bool:
    """Helper to check if a directory path is writable (or can be created and written to)."""
    # If the directory doesn't exist, check parent
    temp_path = path
    while temp_path and not os.path.exists(temp_path):
        temp_path = os.path.dirname(temp_path)
    if not temp_path:
        return False
    return os.access(temp_path, os.W_OK)

def is_binary(file_path: str) -> bool:
    """Checks if a file is binary."""
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(1024)
            return b"\x00" in chunk
    except Exception:
        return False

def show_diff(original_path: str, backup_path: str) -> None:
    """Generates and displays a unified diff between original and backup."""
    if is_binary(original_path) or is_binary(backup_path):
        console.print("[yellow]Binary file differences detected (diff not displayed).[/yellow]")
        return
    
    try:
        with open(original_path, "r", encoding="utf-8", errors="replace") as f1:
            orig_lines = f1.readlines()
        with open(backup_path, "r", encoding="utf-8", errors="replace") as f2:
            backup_lines = f2.readlines()
        
        diff = list(difflib.unified_diff(
            orig_lines, backup_lines,
            fromfile=original_path, tofile=backup_path
        ))
        if diff:
            diff_text = "".join(diff)
            from rich.syntax import Syntax
            syntax = Syntax(diff_text, "diff", theme="monokai")
            console.print(syntax)
        else:
            console.print("[green]No differences found.[/green]")
    except Exception as e:
        console.print(f"[yellow]Could not generate diff: {e}[/yellow]")

def backup_files() -> None:
    """Performs the backup of all tracked files."""
    config = load_config()
    meta = load_metadata()

    # Validate that we can write to the backup directory
    if not check_dir_writable(CONFIG_DIR):
        console.print(f"[red]Error: Backup directory {CONFIG_DIR} is not writable. Aborting.[/red]")
        raise Exception("Backup directory not writable")

    files = config.get("dotfiles", {}).get("files", [])
    if not files:
        console.print("[yellow]No tracked files found in configuration.[/yellow]")
        return

    warnings_occurred = False
    
    for file_path_str in files:
        expanded_path = os.path.expanduser(file_path_str)
        abs_path = os.path.abspath(expanded_path)

        if not os.path.exists(abs_path):
            console.print(f"[yellow]Warning: Source file {file_path_str} does not exist. Skipping.[/yellow]")
            warnings_occurred = True
            continue

        if not os.path.isfile(abs_path):
            console.print(f"[yellow]Warning: Path {file_path_str} is not a file. Skipping.[/yellow]")
            warnings_occurred = True
            continue

        try:
            # Check read permission
            if not os.access(abs_path, os.R_OK):
                console.print(f"[yellow]Warning: Permission denied for {file_path_str}. Skipping.[/yellow]")
                warnings_occurred = True
                continue

            file_hash = calculate_hash(abs_path)
            backup_path = get_backup_path(abs_path)
            
            # Incremental check
            if os.path.exists(backup_path):
                backup_hash = calculate_hash(backup_path)
                if file_hash == backup_hash:
                    console.print(f"[blue]{file_path_str} is up to date.[/blue]")
                    continue

            # Ensure backup parent directory exists
            os.makedirs(os.path.dirname(backup_path), exist_ok=True)
            shutil.copy2(abs_path, backup_path)
            
            # Update metadata
            meta[abs_path] = {
                "backup_path": backup_path,
                "last_backup": datetime.now().isoformat(),
                "hash": f"sha256:{file_hash}",
                "size_bytes": os.path.getsize(abs_path)
            }
            console.print(f"[green]Backed up {file_path_str} successfully.[/green]")
        except Exception as e:
            console.print(f"[yellow]Warning: Failed to back up {file_path_str}: {e}[/yellow]")
            warnings_occurred = True

    save_metadata(meta)
    
    if warnings_occurred:
        console.print("[yellow]Backup completed with some warnings.[/yellow]")
    else:
        console.print("[green]Backup completed successfully![/green]")

def restore_files(target_path_str: Optional[str] = None) -> None:
    """Restores files from backup to their original locations."""
    config = load_config()
    meta = load_metadata()
    files = config.get("dotfiles", {}).get("files", [])

    if not files:
        console.print("[yellow]No tracked files found in configuration.[/yellow]")
        return

    if target_path_str:
        # Restore a SINGLE file
        expanded_path = os.path.expanduser(target_path_str)
        abs_path = os.path.abspath(expanded_path)
        
        # Verify if it's in the configuration
        tracked = False
        for f in files:
            if os.path.abspath(os.path.expanduser(f)) == abs_path:
                tracked = True
                break
        
        if not tracked:
            console.print(f"[red]Error: {target_path_str} is not in the tracked files list.[/red]")
            return

        backup_path = get_backup_path(abs_path)
        if not os.path.exists(backup_path):
            console.print(f"[yellow]Warning: No backup exists for {target_path_str}.[/yellow]")
            return

        if os.path.exists(abs_path):
            # Check if identical
            file_hash = calculate_hash(abs_path)
            backup_hash = calculate_hash(backup_path)
            if file_hash == backup_hash:
                console.print(f"[green]{target_path_str} is already identical to the backup.[/green]")
                return
            
            # Show diff
            console.print(f"[bold]Diff for {target_path_str} vs backup:[/bold]")
            show_diff(abs_path, backup_path)
            
            # Prompt user
            confirm = console.input(f"Overwrite {target_path_str}? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                console.print("[blue]Restore aborted.[/blue]")
                return

        # Restore
        try:
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            shutil.copy2(backup_path, abs_path)
            console.print(f"[green]Restored {target_path_str} successfully.[/green]")
        except Exception as e:
            console.print(f"[red]Error: Failed to restore {target_path_str}: {e}[/red]")
    else:
        # Restore ALL files
        # Check how many have backups
        backups_to_restore = []
        for file_path_str in files:
            expanded_path = os.path.expanduser(file_path_str)
            abs_path = os.path.abspath(expanded_path)
            backup_path = get_backup_path(abs_path)
            if os.path.exists(backup_path):
                backups_to_restore.append((file_path_str, abs_path, backup_path))
            else:
                console.print(f"[yellow]Warning: No backup exists for {file_path_str}. Skipping.[/yellow]")

        if not backups_to_restore:
            console.print("[yellow]No backups are available to restore.[/yellow]")
            return

        confirm = console.input(f"Restore all {len(backups_to_restore)} files? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            console.print("[blue]Restore aborted.[/blue]")
            return

        for file_path_str, abs_path, backup_path in backups_to_restore:
            try:
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                shutil.copy2(backup_path, abs_path)
                console.print(f"[green]Restored {file_path_str} successfully.[/green]")
            except Exception as e:
                console.print(f"[red]Error: Failed to restore {file_path_str}: {e}[/red]")

def show_status() -> None:
    """Displays a status table of all tracked files."""
    config = load_config()
    meta = load_metadata()
    files = config.get("dotfiles", {}).get("files", [])

    if not files:
        console.print("[yellow]No tracked files found in configuration.[/yellow]")
        return

    table = Table(title="Dotfiles Tracking Status")
    table.add_column("Configured Path", style="cyan")
    table.add_column("Disk Status", style="magenta")
    table.add_column("Sync Status", style="green")
    table.add_column("Last Backup", style="yellow")

    for file_path_str in files:
        expanded_path = os.path.expanduser(file_path_str)
        abs_path = os.path.abspath(expanded_path)
        backup_path = get_backup_path(abs_path)

        src_exists = os.path.exists(abs_path)
        bck_exists = os.path.exists(backup_path)
        
        # Determine Disk Status
        disk_status = "[green]exists[/green]" if src_exists else "[red]missing[/red]"
        
        # Determine Sync Status & Last Backup
        sync_status = "[red]no backup[/red]"
        last_backup_str = "Never"
        
        file_meta = meta.get(abs_path)
        if file_meta:
            last_backup_str = file_meta.get("last_backup", "Unknown")
            # Format iso timestamp if possible to a human-readable one
            try:
                dt = datetime.fromisoformat(last_backup_str)
                last_backup_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        if bck_exists:
            if src_exists:
                src_hash = calculate_hash(abs_path)
                bck_hash = calculate_hash(backup_path)
                if src_hash == bck_hash:
                    sync_status = "[green]in-sync[/green]"
                else:
                    sync_status = "[yellow]out-of-sync[/yellow]"
            else:
                sync_status = "[yellow]backup-only[/yellow]"
        else:
            if src_exists:
                sync_status = "[red]no backup[/red]"
            else:
                sync_status = "[red]missing[/red]"

        table.add_row(file_path_str, disk_status, sync_status, last_backup_str)

    console.print(table)

def list_files() -> None:
    """Prints a simple list of all tracked paths in config.toml."""
    config = load_config()
    files = config.get("dotfiles", {}).get("files", [])
    if not files:
        console.print("[yellow]No tracked files found in configuration.[/yellow]")
        return
    for f in files:
        console.print(f)

def add_file(path_str: str) -> None:
    """Adds a new file to the tracked list in config.toml."""
    expanded_path = os.path.expanduser(path_str)
    abs_path = os.path.abspath(expanded_path)

    if os.path.isdir(abs_path):
        console.print(f"[red]Error: Only files are supported, not directories: {path_str}[/red]")
        raise ValueError("Directories not supported")

    config = load_config()
    files = config.setdefault("dotfiles", {}).setdefault("files", [])

    # Check if already tracked (by absolute path comparison)
    already_tracked = False
    for f in files:
        if os.path.abspath(os.path.expanduser(f)) == abs_path:
            already_tracked = True
            break

    if already_tracked:
        console.print(f"[yellow]Already tracked: {path_str}[/yellow]")
        return

    if not os.path.exists(abs_path):
        console.print(f"[yellow]Warning: File not found, adding anyway (will be skipped during backup): {path_str}[/yellow]")

    files.append(path_str)
    save_config(config)
    console.print(f"[green]Added {path_str} to tracked files.[/green]")

def remove_file(path_str: str) -> None:
    """Removes a file from the tracked list in config.toml and optionally deletes backup."""
    config = load_config()
    files = config.get("dotfiles", {}).get("files", [])
    
    if not files:
        console.print("[yellow]No tracked files found in configuration.[/yellow]")
        return

    abs_input_path = os.path.abspath(os.path.expanduser(path_str))
    
    # Find matching configured path
    matching_config_path = None
    for f in files:
        if os.path.abspath(os.path.expanduser(f)) == abs_input_path:
            matching_config_path = f
            break

    if not matching_config_path:
        console.print(f"[red]Error: File not found in tracked list: {path_str}[/red]")
        raise ValueError("File not in tracked list")

    files.remove(matching_config_path)
    save_config(config)

    # Check for backups in metadata or on disk
    meta = load_metadata()
    backup_path = get_backup_path(abs_input_path)
    backup_exists = os.path.exists(backup_path)

    console.print(f"Removing {matching_config_path} from tracking.")

    if backup_exists:
        last_backup_str = "Unknown"
        file_meta = meta.get(abs_input_path)
        if file_meta:
            last_backup_str = file_meta.get("last_backup", "Unknown")
            try:
                dt = datetime.fromisoformat(last_backup_str)
                last_backup_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        console.print(f"Backup exists at: {backup_path} (last backed up: {last_backup_str})")
        confirm = console.input("Delete backup too? [y/N]: ").strip().lower()
        if confirm in ("y", "yes"):
            try:
                os.remove(backup_path)
                # Cleanup empty parent directories in backup_dir
                parent_dir = os.path.dirname(backup_path)
                while parent_dir and parent_dir != BACKUP_DIR:
                    if not os.listdir(parent_dir):
                        os.rmdir(parent_dir)
                        parent_dir = os.path.dirname(parent_dir)
                    else:
                        break
                console.print("[green]Deleted backup file and cleaned up empty directories.[/green]")
            except Exception as e:
                console.print(f"[yellow]Warning: Could not delete backup file {backup_path}: {e}[/yellow]")
            
            if abs_input_path in meta:
                del meta[abs_input_path]
                save_metadata(meta)
    else:
        console.print(f"[green]Removed {matching_config_path} from tracking.[/green]")
