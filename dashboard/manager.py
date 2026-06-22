import os
import pwd
import sys
import termios
import tty
import subprocess
from datetime import datetime
from typing import Dict, Any, List, NoReturn, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.align import Align
from rich.syntax import Syntax
from rich.markdown import Markdown

from shellpa.dotfiles.manager import load_config, get_status_data, show_status
from shellpa.cheatsheet.manager import get_all_snippets, open_fzf_session
from shellpa.ai.manager import load_cache, ask, handle_ask_result, AIError


def resolve_login_shell() -> str:
    """Resolves the user's real login shell from passwd, guarding against recursion loops."""
    try:
        uid = os.getuid()
        shell = pwd.getpwuid(uid).pw_shell
    except Exception:
        shell = "/bin/bash"

    basename = os.path.basename(shell)
    if basename in {"sp", "shellpa"}:
        console = Console()
        console.print(
            "[bold red]WARNING: Recursion guard triggered! Resolved login shell is shellpa. "
            "Falling back to /bin/bash to prevent infinite loops.[/bold red]"
        )
        shell = "/bin/bash"

    return shell


def enter_shell() -> NoReturn:
    """Hands off execution to the resolved login shell, replacing the current process."""
    shell = resolve_login_shell()
    os.execvp(shell, [shell, "-l"])


def gather_stats() -> Dict[str, Any]:
    """Pulls counts and sync states from modules safely."""
    stats = {}

    # 1. Cheatsheet Stats
    try:
        snippets = get_all_snippets()
        total_snippets = len(snippets)
        used_last_7_days = 0
        now = datetime.now()
        for s in snippets:
            last_used_str = s.get("last_used")
            if last_used_str:
                try:
                    dt = datetime.fromisoformat(last_used_str)
                    if (now - dt).days < 7:
                        used_last_7_days += 1
                except Exception:
                    pass
        stats["cheatsheet"] = f"{total_snippets} snippets, {used_last_7_days} used in last 7 days"
    except Exception:
        stats["cheatsheet"] = "—"

    # 2. Dotfiles Stats
    try:
        data = get_status_data()
        total_tracked = len(data)
        out_of_sync = sum(1 for item in data if item["sync_status"] == "out-of-sync")
        missing = sum(1 for item in data if item["disk_status"] == "missing")
        stats["dotfiles"] = f"{total_tracked} tracked ({out_of_sync} out-of-sync, {missing} missing)"
    except Exception:
        stats["dotfiles"] = "—"

    # 3. Last Sync Stats
    try:
        config = load_config()
        last_sync = config.get("sync", {}).get("last_sync", "never")
        stats["last_sync"] = last_sync
    except Exception:
        stats["last_sync"] = "never"

    # 4. AI Cache Stats
    try:
        cache = load_cache()
        stats["ai_cache"] = f"{len(cache)} cached responses"
    except Exception:
        stats["ai_cache"] = "—"

    return stats


def read_key() -> str:
    """Reads a single keypress from standard input in raw mode."""
    if not sys.stdin.isatty():
        return sys.stdin.read(1)

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def render_header(stats: Dict[str, Any]) -> Panel:
    """Renders the stats panel."""
    table = Table.grid(expand=True)
    table.add_column(justify="left", style="bold cyan")
    table.add_column(justify="right", style="bold green")

    table.add_row(" Cheatsheet: ", f"{stats['cheatsheet']} ")
    table.add_row(" Dotfiles:   ", f"{stats['dotfiles']} ")
    table.add_row(" Last Sync:  ", f"{stats['last_sync']} ")
    table.add_row(" AI Cache:   ", f"{stats['ai_cache']} ")

    return Panel(
        table,
        title="[bold yellow]✨ Shellpa Dashboard Stats ✨[/bold yellow]",
        border_style="cyan",
        padding=(1, 2)
    )


def render_menu() -> Panel:
    """Renders the menu shortcut panel."""
    menu_text = Text()
    menu_text.append("\n  [1] ", style="bold green")
    menu_text.append("Search cheatsheet       ", style="bold white")
    menu_text.append("→ ", style="dim")
    menu_text.append("sp search\n", style="italic yellow")

    menu_text.append("  [2] ", style="bold green")
    menu_text.append("Ask AI                  ", style="bold white")
    menu_text.append("→ ", style="dim")
    menu_text.append("runs sp ask flow\n", style="italic yellow")

    menu_text.append("  [3] ", style="bold green")
    menu_text.append("Dotfiles status         ", style="bold white")
    menu_text.append("→ ", style="dim")
    menu_text.append("renders status table\n", style="italic yellow")

    menu_text.append("  [4] ", style="bold green")
    menu_text.append("Sync status             ", style="bold white")
    menu_text.append("→ ", style="dim")
    menu_text.append("renders diff_status()\n", style="italic yellow")

    menu_text.append("\n  [s] ", style="bold magenta")
    menu_text.append("Enter Shell             ", style="bold white")
    menu_text.append("→ ", style="dim")
    menu_text.append("hands off to real shell\n", style="italic cyan")

    menu_text.append("  [q] ", style="bold red")
    menu_text.append("Quit dashboard          ", style="bold white")
    menu_text.append("→ ", style="dim")
    menu_text.append("same as Enter Shell\n", style="italic cyan")

    return Panel(
        menu_text,
        title="[bold yellow]⚡ Quick Shortcuts ⚡[/bold yellow]",
        border_style="magenta",
        padding=(1, 2)
    )


def render_dashboard(stats: Dict[str, Any]) -> Align:
    """Centers the dashboard grid in the middle of the terminal."""
    grid = Table.grid(expand=False)
    grid.add_column(width=60)
    
    grid.add_row(render_header(stats))
    grid.add_row("")  # spacer
    grid.add_row(render_menu())
    
    return Align.center(grid, vertical="middle")


def show_sync_status_in_dashboard(console: Console) -> None:
    """Queries and renders remote sync status tables."""
    config = load_config()
    repo_name = config.get("sync", {}).get("repo")
    if not repo_name:
        console.print("[red]Error: Repository not configured. Run `sp sync setup` first.[/red]")
        return

    encrypt_enabled = config.get("sync", {}).get("encryption", False)
    passphrase = None
    if encrypt_enabled:
        passphrase = console.input("Enter decryption passphrase: ", password=True)

    try:
        console.print("[dim]Analyzing local vs remote status...[/dim]")
        from shellpa.sync.manager import get_github_client, ensure_repo, diff_status, SyncError
        client = get_github_client()
        repo = ensure_repo(client, repo_name)
        report = diff_status(client, repo, passphrase)

        s_rep = report["snippets"]
        d_rep = report["dotfiles"]

        # Snippets table
        s_table = Table(title="Snippet Sync Status", border_style="blue")
        s_table.add_column("Category", style="cyan")
        s_table.add_column("Count", style="green")
        s_table.add_row("Remote added (pull will add locally)", str(len(s_rep["remote_added"])))
        s_table.add_row("Local pending push (push will send)", str(len(s_rep["pending_push"])))
        s_table.add_row("Conflicted (commands differ)", str(len(s_rep["conflicts"])))
        s_table.add_row("Identical / Up-to-date", str(len(s_rep["noop"])))
        console.print(s_table)

        # Dotfiles table
        d_table = Table(title="Dotfile Backup Sync Status", border_style="blue")
        d_table.add_column("Category", style="cyan")
        d_table.add_column("Count", style="green")
        d_table.add_row("Remote added (pull will write local backup)", str(len(d_rep["remote_added"])))
        d_table.add_row("Local pending push (push will send)", str(len(d_rep["pending_push"])))
        d_table.add_row("Conflicted (hashes differ)", str(len(d_rep["conflicts"])))
        d_table.add_row("Identical / Up-to-date", str(len(d_rep["noop"])))
        console.print(d_table)

    except SyncError as e:
        console.print(f"[red]Error: {e}[/red]")
    except Exception as e:
        console.print(f"[red]Unexpected status check failure: {e}[/red]")


def run_dashboard() -> NoReturn:
    """Launches the interactive full-screen dashboard."""
    try:
        config = load_config()
        enabled = config.get("dashboard", {}).get("enabled", True)
    except Exception:
        enabled = True

    if not enabled:
        enter_shell()

    console = Console()
    stats = gather_stats()

    try:
        with Live(render_dashboard(stats), console=console, auto_refresh=False) as live:
            while True:
                stats = gather_stats()
                live.update(render_dashboard(stats))
                live.refresh()

                key = read_key()
                
                if key in ("s", "q", "\x03"):
                    break
                
                elif key == "1":
                    live.stop()
                    live.console.clear()
                    try:
                        open_fzf_session()
                    except Exception as e:
                        live.console.print(f"[red]Error searching cheatsheet: {e}[/red]")
                        live.console.input("\nPress Enter to return to dashboard...")
                    live.console.clear()
                    live.start()
                    
                elif key == "2":
                    live.stop()
                    live.console.clear()
                    try:
                        query = live.console.input("[bold cyan]Ask AI: [/bold cyan]").strip()
                        if query:
                            try:
                                raw_result, from_cache = ask(query)
                                if from_cache:
                                    live.console.print("[dim](from cache)[/dim]")
                                handle_ask_result(raw_result, query)
                            except AIError as e:
                                live.console.print(f"[red]Error: {e}[/red]")
                                live.console.input("\nPress Enter to return to dashboard...")
                    except KeyboardInterrupt:
                        pass
                    live.console.clear()
                    live.start()
                    
                elif key == "3":
                    live.stop()
                    live.console.clear()
                    try:
                        show_status()
                    except Exception as e:
                        live.console.print(f"[red]Error showing status: {e}[/red]")
                    live.console.input("\nPress Enter to return to dashboard...")
                    live.console.clear()
                    live.start()
                    
                elif key == "4":
                    live.stop()
                    live.console.clear()
                    try:
                        show_sync_status_in_dashboard(live.console)
                    except Exception as e:
                        live.console.print(f"[red]Error showing sync status: {e}[/red]")
                    live.console.input("\nPress Enter to return to dashboard...")
                    live.console.clear()
                    live.start()

    except KeyboardInterrupt:
        pass
    except Exception as e:
        console.print(f"[red]Uncaught dashboard exception: {e}[/red]")
        import traceback
        traceback.print_exc()
        console.input("\nPress Enter to drop to shell...")
        
    enter_shell()
