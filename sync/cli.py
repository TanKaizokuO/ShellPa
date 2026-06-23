import os
import sys
import difflib
import typer
from typing import Optional, List, Tuple, Dict, Any

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax

from shellpa.sync import manager as sync_manager
from shellpa.sync.manager import SyncError
from shellpa.dotfiles.manager import load_config, save_config

console = Console()
sync_app = typer.Typer(
    name="sync",
    help="Sync dotfiles and cheatsheet snippets to a private GitHub repository.",
)


@sync_app.command("setup")
def setup():
    """Interactive wizard to configure GitHub repository sync and optional encryption."""
    console.print(Panel("[bold blue]ShellPa Sync Setup Wizard[/bold blue]", border_style="blue"))

    # 1. Prompt token
    token = typer.prompt("Enter your GitHub Personal Access Token (PAT)", hide_input=True)
    if not token.strip():
        console.print("[red]Error: Token cannot be empty.[/red]")
        raise typer.Exit(code=1)

    # 2. Prompt repo
    repo_name = typer.prompt("Enter repository name", default="shellpa-backup")

    try:
        # Validate connection and repo
        client = sync_manager.Github(token.strip())
        repo = sync_manager.ensure_repo(client, repo_name)
        username = client.get_user().login
    except SyncError as e:
        console.print(f"[bold red]Validation Error:[/bold red] {e}")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[bold red]Unexpected Error:[/bold red] {e}")
        raise typer.Exit(code=1)

    # 3. Store Token
    try:
        sync_manager.store_token(token.strip())
    except SyncError as e:
        console.print(f"[red]Warning: {e}[/red]")

    # 4. Optional encryption
    encrypt_confirm = typer.confirm(
        "Enable encryption-at-rest for your backed-up data? (passphrase requested at push/pull)",
        default=False,
    )

    # 5. Save Config
    config = load_config()
    if "sync" not in config:
        config["sync"] = {}
    config["sync"]["repo"] = repo_name
    config["sync"]["username"] = username
    config["sync"]["encryption"] = encrypt_confirm
    save_config(config)

    console.print(
        Panel(
            f"[green]✓ Sync Setup Completed Successfully![/green]\n\n"
            f"[bold]Username:[/bold] {username}\n"
            f"[bold]Repository:[/bold] {repo_name} (private)\n"
            f"[bold]Encryption-at-rest:[/bold] {'Enabled' if encrypt_confirm else 'Disabled'}",
            border_style="green",
        )
    )


@sync_app.command("push")
def push(
    message: Optional[str] = typer.Option(
        None, "--message", "-m", help="Custom commit message override."
    )
):
    """Back up local dotfiles and cheatsheet snippets to GitHub."""
    config = load_config()
    repo_name = config.get("sync", {}).get("repo")
    if not repo_name:
        console.print(
            "[red]Error: Repository not configured. Run `sp sync setup` first.[/red]"
        )
        raise typer.Exit(code=1)

    encrypt_enabled = config.get("sync", {}).get("encryption", False)
    passphrase = None
    if encrypt_enabled:
        passphrase = typer.prompt("Enter encryption passphrase", hide_input=True)
        if not passphrase:
            console.print("[red]Error: Passphrase required when encryption is enabled.[/red]")
            raise typer.Exit(code=1)

    try:
        client = sync_manager.get_github_client()
        repo = sync_manager.ensure_repo(client, repo_name)

        console.print("[dim]Building backup payload...[/dim]")
        payload = sync_manager.build_payload(passphrase)

        if not message:
            num_snippets = len(sync_manager.export_snippets_json())
            num_dotfiles = sum(1 for k in payload if k.startswith("dotfiles/"))
            timestamp = sync_manager.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            message = (
                f"shellpa sync — {timestamp} | {num_snippets} snippets | {num_dotfiles} dotfiles"
            )

        console.print(f"[dim]Pushing commit to branch '{repo.default_branch}'...[/dim]")
        sync_manager.push(client, repo, payload, message)
        console.print(f"[green]✓ Successfully backed up to '{repo_name}'![/green]")
    except SyncError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]Unexpected push failure: {e}[/red]")
        raise typer.Exit(code=1)


@sync_app.command("status")
def status():
    """Dry-run comparing local configuration/cheatsheet status with remote backup."""
    config = load_config()
    repo_name = config.get("sync", {}).get("repo")
    if not repo_name:
        console.print(
            "[red]Error: Repository not configured. Run `sp sync setup` first.[/red]"
        )
        raise typer.Exit(code=1)

    encrypt_enabled = config.get("sync", {}).get("encryption", False)
    passphrase = None
    if encrypt_enabled:
        passphrase = typer.prompt("Enter encryption passphrase", hide_input=True)

    try:
        client = sync_manager.get_github_client()
        repo = sync_manager.ensure_repo(client, repo_name)

        console.print("[dim]Analyzing local vs remote status...[/dim]")
        report = sync_manager.diff_status(client, repo, passphrase)

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
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]Unexpected status check failure: {e}[/red]")
        raise typer.Exit(code=1)


@sync_app.command("pull")
def pull():
    """Pull remote configurations and cheatsheet snippets, merging conflicts interactively."""
    config = load_config()
    repo_name = config.get("sync", {}).get("repo")
    if not repo_name:
        console.print(
            "[red]Error: Repository not configured. Run `sp sync setup` first.[/red]"
        )
        raise typer.Exit(code=1)

    encrypt_enabled = config.get("sync", {}).get("encryption", False)
    passphrase = None
    if encrypt_enabled:
        passphrase = typer.prompt("Enter decryption passphrase", hide_input=True)

    try:
        client = sync_manager.get_github_client()
        repo = sync_manager.ensure_repo(client, repo_name)

        console.print("[dim]Fetching remote updates...[/dim]")
        report = sync_manager.diff_status(client, repo, passphrase)

        s_rep = report["snippets"]
        d_rep = report["dotfiles"]

        resolved_snippets: List[Tuple[str, str, Dict[str, Any]]] = []
        resolved_dotfiles: List[Tuple[str, str, bytes]] = []

        # 1. Handle Snippet Conflicts
        if s_rep["conflicts"]:
            console.print("\n[yellow]Snippet Conflicts Found:[/yellow]")
            for local, remote in s_rep["conflicts"]:
                # Print a neat conflict comparison table
                t = Table(title=f"Conflict for Snippet UUID: {local['uuid']}", border_style="red")
                t.add_column("Field", style="cyan")
                t.add_column("Local Copy", style="yellow")
                t.add_column("Remote Copy", style="green")
                t.add_row("Command", local["command"], remote["command"])
                t.add_row("Description", local["description"], remote["description"])
                t.add_row("Tags", local["tags"], remote["tags"])
                console.print(t)

                while True:
                    console.print(
                        "[bold]How would you like to merge?[/bold] "
                        "[[L]ocal (keep mine)  [R]emote (overwrite mine)  [B]oth (keep both)  [S]kip]: ",
                        end="",
                    )
                    choice = input().strip().lower()
                    if choice in ("l", "local"):
                        resolved_snippets.append((local["uuid"], "local", remote))
                        break
                    elif choice in ("r", "remote"):
                        resolved_snippets.append((local["uuid"], "remote", remote))
                        break
                    elif choice in ("b", "both"):
                        resolved_snippets.append((local["uuid"], "both", remote))
                        break
                    elif choice in ("s", "skip"):
                        break
                    else:
                        console.print("[red]Invalid choice. Choose L, R, B, or S.[/red]")

        # 2. Handle Dotfile Conflicts
        if d_rep["conflicts"]:
            console.print("\n[yellow]Dotfile Conflicts Found (Backup copies differ):[/yellow]")
            for rel_path, local_bytes, remote_bytes in d_rep["conflicts"]:
                console.print(f"\n[bold]File: {rel_path}[/bold]")
                # Display diff
                try:
                    l_text = local_bytes.decode("utf-8", errors="replace")
                    r_text = remote_bytes.decode("utf-8", errors="replace")
                    diff = list(
                        difflib.unified_diff(
                            l_text.splitlines(keepends=True),
                            r_text.splitlines(keepends=True),
                            fromfile="Local Backup",
                            tofile="Remote Backup",
                        )
                    )
                    if diff:
                        console.print(Syntax("".join(diff), "diff", theme="monokai"))
                except Exception:
                    console.print("[dim]Binary file differences (diff not shown).[/dim]")

                while True:
                    console.print(
                        "[bold]Action?[/bold] [[L]ocal (keep mine)  [R]emote (overwrite backup)  [S]kip]: ",
                        end="",
                    )
                    choice = input().strip().lower()
                    if choice in ("l", "local"):
                        resolved_dotfiles.append((rel_path, "local", remote_bytes))
                        break
                    elif choice in ("r", "remote"):
                        resolved_dotfiles.append((rel_path, "remote", remote_bytes))
                        break
                    elif choice in ("s", "skip"):
                        break
                    else:
                        console.print("[red]Invalid choice. Choose L, R, or S.[/red]")

        # 3. Apply updates
        console.print("[dim]Applying updates locally...[/dim]")
        sync_manager.apply_pull(report, resolved_snippets, resolved_dotfiles)

        # 4. Summary
        s_added = len(s_rep["remote_added"])
        s_updated = sum(1 for _, action, _ in resolved_snippets if action == "remote")
        s_both = sum(1 for _, action, _ in resolved_snippets if action == "both")
        s_skipped = len(s_rep["conflicts"]) - (s_updated + s_both + sum(
            1 for _, action, _ in resolved_snippets if action == "local"
        ))

        d_added = len(d_rep["remote_added"])
        d_updated = sum(1 for _, action, _ in resolved_dotfiles if action == "remote")
        d_skipped = len(d_rep["conflicts"]) - (d_updated + sum(
            1 for _, action, _ in resolved_dotfiles if action == "local"
        ))

        console.print(
            Panel(
                f"[green]✓ Merge pull completed![/green]\n\n"
                f"[bold]Snippets Merge:[/bold]\n"
                f"  - Added remote: {s_added}\n"
                f"  - Updated locally: {s_updated}\n"
                f"  - Duplicated (Both): {s_both}\n"
                f"  - Skipped conflicts: {s_skipped}\n\n"
                f"[bold]Dotfiles Merge:[/bold]\n"
                f"  - Backup copies added: {d_added}\n"
                f"  - Backup copies updated: {d_updated}\n"
                f"  - Skipped conflicts: {d_skipped}\n\n"
                f"[dim]Note: Live files (e.g. ~/.bashrc) were NOT modified. Run `sp dotfiles restore` to apply.[/dim]",
                border_style="green",
            )
        )

    except SyncError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]Unexpected pull failure: {e}[/red]")
        raise typer.Exit(code=1)


@sync_app.command("auto")
def auto(
    interval_hours: int = typer.Option(
        6, "--interval-hours", "-i", help="Sync check interval in hours."
    ),
    disable: bool = typer.Option(
        False, "--disable", "-d", help="Disable the sync scheduler."
    ),
):
    """Enable or disable recurring back up cron jobs."""
    try:
        if disable:
            if platform.system() == "Darwin":
                sync_manager.remove_launchd()
            else:
                sync_manager.remove_cron()
            console.print("[green]✓ Recurring backup sync scheduler disabled.[/green]")
        else:
            if platform.system() == "Darwin":
                sync_manager.setup_launchd(interval_hours)
            else:
                sync_manager.setup_cron(interval_hours)
            console.print(
                f"[green]✓ Backup sync scheduler set up to run every {interval_hours} hours.[/green]"
            )
    except SyncError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[red]Failed to configure auto-scheduler: {e}[/red]")
        raise typer.Exit(code=1)
