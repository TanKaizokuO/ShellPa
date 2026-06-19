import os
import sys
import uuid
import shutil
import shlex
import sqlite3
import tempfile
import subprocess
from datetime import datetime
from typing import Optional, List, Dict, Tuple
import pyperclip
from rich.console import Console
from shellpa.dotfiles.manager import DynamicPath

console = Console()

DB_PATH = DynamicPath(lambda: os.path.join(os.path.expanduser("~/.shellpa"), "snippets.db"))

def init_db(conn: sqlite3.Connection) -> None:
    """Creates ~/.shellpa/snippets.db and the snippets table if they don't exist."""
    schema = """
    CREATE TABLE IF NOT EXISTS snippets (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        command     TEXT NOT NULL,
        description TEXT NOT NULL,
        tags        TEXT NOT NULL DEFAULT '',
        source      TEXT NOT NULL DEFAULT 'manual',
        created_at  TEXT NOT NULL,
        last_used   TEXT,
        use_count   INTEGER NOT NULL DEFAULT 0,
        uuid        TEXT UNIQUE
    );
    """
    conn.execute(schema)
    conn.commit()

    # Schema migration to add uuid column if not present
    cursor = conn.execute("PRAGMA table_info(snippets)")
    columns = [row[1] for row in cursor.fetchall()]
    if "uuid" not in columns:
        conn.execute("ALTER TABLE snippets ADD COLUMN uuid TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_snippets_uuid ON snippets(uuid)")
        conn.commit()

        # Backfill existing rows with UUIDs
        cursor2 = conn.execute("SELECT id FROM snippets WHERE uuid IS NULL OR uuid = ''")
        rows = cursor2.fetchall()
        for r in rows:
            sid = r[0]
            conn.execute("UPDATE snippets SET uuid = ? WHERE id = ?", (str(uuid.uuid4()), sid))
        conn.commit()

def get_connection() -> sqlite3.Connection:
    """Connects to the snippets database and ensures tables are initialized."""
    db_dir = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn

def get_all_snippets() -> List[Dict]:
    """Returns all rows in the snippets table as dicts."""
    try:
        with get_connection() as conn:
            cursor = conn.execute("SELECT * FROM snippets")
            return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        console.print(f"[red]Database Error: {e}[/red]")
        sys.exit(1)

def get_snippet(sid: int) -> Optional[Dict]:
    """Returns one snippet row or None."""
    try:
        with get_connection() as conn:
            cursor = conn.execute("SELECT * FROM snippets WHERE id = ?", (sid,))
            row = cursor.fetchone()
            return dict(row) if row else None
    except sqlite3.Error as e:
        console.print(f"[red]Database Error: {e}[/red]")
        sys.exit(1)

def normalize_tags(tags_str: str) -> str:
    """Splits by comma, strips whitespace, removes duplicates case-insensitively, and joins with commas."""
    if not tags_str:
        return ""
    parts = [p.strip() for p in tags_str.split(",") if p.strip()]
    seen = set()
    unique_parts = []
    for p in parts:
        plow = p.lower()
        if plow not in seen:
            seen.add(plow)
            unique_parts.append(p)
    return ",".join(unique_parts)

def add_snippet(
    command: str,
    description: str,
    tags: str = "",
    source: str = "manual",
    snippet_uuid: Optional[str] = None,
) -> int:
    """Inserts a snippet and returns its database ID."""
    normalized_tags = normalize_tags(tags)
    created_at = datetime.now().isoformat()
    if not snippet_uuid:
        snippet_uuid = str(uuid.uuid4())
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO snippets (command, description, tags, source, created_at, uuid) VALUES (?, ?, ?, ?, ?, ?)",
                (command, description, normalized_tags, source, created_at, snippet_uuid)
            )
            conn.commit()
            return cursor.lastrowid
    except sqlite3.Error as e:
        console.print(f"[red]Database Error: {e}[/red]")
        sys.exit(1)

def delete_snippet(sid: int) -> bool:
    """Deletes snippet from DB. Returns True if row was deleted, False otherwise."""
    try:
        with get_connection() as conn:
            cursor = conn.execute("DELETE FROM snippets WHERE id = ?", (sid,))
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        console.print(f"[red]Database Error: {e}[/red]")
        sys.exit(1)

def update_snippet(sid: int, command: str, description: str, tags: str) -> bool:
    """Updates fields of an existing snippet, applying tag normalization."""
    normalized_tags = normalize_tags(tags)
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                "UPDATE snippets SET command = ?, description = ?, tags = ? WHERE id = ?",
                (command, description, normalized_tags, sid)
            )
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        console.print(f"[red]Database Error: {e}[/red]")
        sys.exit(1)

def tag_snippet(sid: int, tag: str) -> bool:
    """Normalizes and appends tag if not already present on that snippet."""
    snippet = get_snippet(sid)
    if not snippet:
        return False

    clean_tag = tag.strip()
    if not clean_tag:
        return True

    existing_tags_str = snippet.get("tags", "")
    existing_parts = [p.strip() for p in existing_tags_str.split(",") if p.strip()]

    if clean_tag.lower() in [p.lower() for p in existing_parts]:
        console.print("Tag already exists.")
        return True

    existing_parts.append(clean_tag)
    new_tags = ",".join(existing_parts)
    return update_snippet(sid, snippet["command"], snippet["description"], new_tags)

def record_usage(sid: int) -> None:
    """Increments the snippet's use count and updates its last_used timestamp."""
    now = datetime.now().isoformat()
    try:
        with get_connection() as conn:
            conn.execute(
                "UPDATE snippets SET use_count = use_count + 1, last_used = ? WHERE id = ?",
                (now, sid)
            )
            conn.commit()
    except sqlite3.Error as e:
        console.print(f"[red]Database Error: {e}[/red]")
        sys.exit(1)

def check_fzf_installed() -> bool:
    """Returns True if fzf executable is found in PATH."""
    return shutil.which("fzf") is not None

def open_fzf(snippets: List[Dict]) -> Optional[Dict]:
    """Launches fzf subprocess, returns selected snippet dict (with '_key' key added) or None."""
    if not check_fzf_installed():
        console.print("[red]Error: fzf is not installed.[/red]")
        console.print("Please install fzf to use interactive search:")
        console.print("  Linux : [bold]sudo apt install fzf[/bold]")
        console.print("  macOS : [bold]brew install fzf[/bold]")
        return None

    lines = []
    snippet_by_id = {}
    for s in snippets:
        sid = s["id"]
        # Sanitize tabs and newlines to prevent delimiter breaking
        cmd_display = s["command"].replace("\t", "    ").replace("\n", " ; ")
        desc_display = s["description"].replace("\t", "    ").replace("\n", " ; ")
        tags_display = f"[{s['tags']}]" if s["tags"] else ""
        lines.append(f"{sid}\t[{sid}] {cmd_display}  —  {desc_display}  {tags_display}")
        snippet_by_id[sid] = s

    preview_cmd = f"{shlex.quote(sys.executable)} -m shellpa.main cheatsheet preview {{1}}"
    
    cmd = [
        "fzf",
        "--delimiter=\t",
        "--with-nth=2",
        "--expect=ctrl-y,ctrl-e,ctrl-d",
        f"--preview={preview_cmd}",
        "--preview-window=bottom:40%",
        "--ansi",
        "--header=Enter: Run | Ctrl+Y: Copy | Ctrl+E: Edit & Run | Ctrl+D: Delete",
    ]

    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        stdout, _ = proc.communicate(input="\n".join(lines))
    except Exception as e:
        console.print(f"[red]Error: Failed to launch fzf: {e}[/red]")
        return None

    # Handle cancellation (Esc or status 130)
    if not stdout or proc.returncode == 130:
        return None

    out_lines = stdout.splitlines()
    if len(out_lines) < 2:
        return None

    key_pressed = out_lines[0].strip()
    selected_line = out_lines[1]

    parts = selected_line.split("\t")
    if not parts or not parts[0]:
        return None

    try:
        sid = int(parts[0])
    except ValueError:
        return None

    snippet = snippet_by_id.get(sid)
    if snippet:
        # Clone snippet dict and store key_pressed under custom key
        snippet_copy = dict(snippet)
        snippet_copy["_key"] = key_pressed
        return snippet_copy

    return None

def open_fzf_session() -> None:
    """Delegates to open_fzf and runs the corresponding keybinding action."""
    snippets = get_all_snippets()
    if not snippets:
        console.print("[yellow]No snippets saved yet. Use `sp cheatsheet add` to save your first one.[/yellow]")
        return

    selected = open_fzf(snippets)
    if not selected:
        return

    sid = selected["id"]
    command = selected["command"]
    key = selected["_key"]

    if key == "":
        # Enter: Run command
        console.print(f"[bold blue]Running Command:[/bold blue] {command}")
        record_usage(sid)
        subprocess.run(command, shell=True)

    elif key == "ctrl-y":
        # Ctrl+Y: Copy command to clipboard
        try:
            pyperclip.copy(command)
            console.print(f"[green]Copied command for snippet #{sid} to clipboard.[/green]")
        except Exception as e:
            console.print(f"[yellow]Warning: Clipboard not available: {e}[/yellow]")
        record_usage(sid)

    elif key == "ctrl-e":
        # Ctrl+E: Edit and Run (and Save back)
        editor = os.environ.get("EDITOR", "nano")
        with tempfile.NamedTemporaryFile(suffix=".sh", delete=False, mode="w") as tf:
            tf.write(command)
            temp_path = tf.name

        try:
            subprocess.run([editor, temp_path], check=True)
            with open(temp_path, "r") as f:
                edited_command = f.read().strip()
        except Exception as e:
            console.print(f"[red]Error: Failed to edit command: {e}[/red]")
            edited_command = None
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

        if edited_command:
            console.print(f"[bold blue]Edited command:[/bold blue] {edited_command}")
            confirm = console.input("Run edited command? [y/N]: ").strip().lower()
            if confirm in ("y", "yes"):
                update_snippet(sid, edited_command, selected["description"], selected["tags"])
                record_usage(sid)
                subprocess.run(edited_command, shell=True)

    elif key == "ctrl-d":
        # Ctrl+D: Delete snippet
        confirm = console.input(f"Delete snippet #{sid}? [y/N]: ").strip().lower()
        if confirm in ("y", "yes"):
            if delete_snippet(sid):
                console.print(f"[green]Deleted snippet #{sid}.[/green]")
