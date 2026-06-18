import typer
from typing import Optional
from datetime import datetime
from rich.console import Console
from rich.table import Table
from shellpa.cheatsheet import manager

console = Console()

cheatsheet_app = typer.Typer(
    name="cheatsheet",
    help="Manage shell command snippets.",
    no_args_is_help=True
)

@cheatsheet_app.command(name="add")
def add():
    """Interactively add a new shell snippet."""
    command = typer.prompt("Command").strip()
    if not command:
        console.print("[red]Error: Command must not be empty.[/red]")
        raise typer.Exit(code=1)

    description = typer.prompt("Description").strip()
    tags = typer.prompt("Tags", default="", show_default=False).strip()

    sid = manager.add_snippet(command, description, tags, source="manual")
    console.print(f"Snippet #{sid} saved.")

@cheatsheet_app.command(name="list")
def list_snippets():
    """List all saved snippets."""
    snippets = manager.get_all_snippets()
    if not snippets:
        console.print("[yellow]No snippets saved yet. Use `sp cheatsheet add` to save your first one.[/yellow]")
        return

    # Sort by use_count DESC, then created_at DESC
    # SQLite datetime ISO strings sort correctly lexicographically
    snippets.sort(key=lambda s: (s.get("use_count", 0), s.get("created_at", "")), reverse=True)

    table = Table(title="Saved Snippets")
    table.add_column("ID", style="cyan", justify="right")
    table.add_column("Command", style="green")
    table.add_column("Description", style="white")
    table.add_column("Tags", style="yellow")
    table.add_column("Source", style="magenta")
    table.add_column("Uses", style="blue", justify="right")
    table.add_column("Last Used", style="cyan")

    for s in snippets:
        last_used_str = s.get("last_used")
        if last_used_str:
            try:
                dt = datetime.fromisoformat(last_used_str)
                last_used_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        else:
            last_used_str = "Never"

        # Limit command display length or show clean inline representation
        cmd_display = s["command"].replace("\n", " ; ")
        
        table.add_row(
            str(s["id"]),
            cmd_display,
            s["description"],
            s["tags"],
            s["source"],
            str(s["use_count"]),
            last_used_str
        )

    console.print(table)

@cheatsheet_app.command(name="delete")
def delete(
    sid: int = typer.Argument(..., help="ID of the snippet to delete.")
):
    """Deletes a snippet from the database."""
    snippet = manager.get_snippet(sid)
    if not snippet:
        console.print(f"[red]Snippet #{sid} not found.[/red]")
        raise typer.Exit(code=1)

    confirm = console.input(f"Delete snippet #{sid} '{snippet['command']}'? [y/N]: ").strip().lower()
    if confirm in ("y", "yes"):
        if manager.delete_snippet(sid):
            console.print("Deleted.")
        else:
            console.print(f"[red]Error: Failed to delete snippet #{sid}.[/red]")
            raise typer.Exit(code=1)
    else:
        console.print("[blue]Deletion cancelled.[/blue]")

@cheatsheet_app.command(name="tag")
def tag(
    sid: int = typer.Argument(..., help="ID of the snippet to tag."),
    tag_name: str = typer.Argument(..., help="Tag to add to the snippet.")
):
    """Appends a tag to a snippet."""
    snippet = manager.get_snippet(sid)
    if not snippet:
        console.print(f"[red]Snippet #{sid} not found.[/red]")
        raise typer.Exit(code=1)

    if manager.tag_snippet(sid, tag_name):
        console.print("Tag added successfully.")
    else:
        console.print(f"[red]Error: Failed to tag snippet #{sid}.[/red]")
        raise typer.Exit(code=1)

@cheatsheet_app.command(name="edit")
def edit(
    sid: int = typer.Argument(..., help="ID of the snippet to edit.")
):
    """Interactively edits a snippet."""
    snippet = manager.get_snippet(sid)
    if not snippet:
        console.print(f"[red]Snippet #{sid} not found.[/red]")
        raise typer.Exit(code=1)

    command = typer.prompt("Command", default=snippet["command"]).strip()
    if not command:
        console.print("[red]Error: Command must not be empty.[/red]")
        raise typer.Exit(code=1)

    description = typer.prompt("Description", default=snippet["description"]).strip()
    tags = typer.prompt("Tags", default=snippet["tags"], show_default=True).strip()

    if manager.update_snippet(sid, command, description, tags):
        console.print("Saved changes.")
    else:
        console.print(f"[red]Error: Failed to update snippet #{sid}.[/red]")
        raise typer.Exit(code=1)

@cheatsheet_app.command(name="preview", hidden=True)
def preview(
    item_arg: str = typer.Argument(..., help="Snippet string or raw ID")
):
    """Hidden command used by fzf to get snippet details by ID."""
    # item_arg might be the raw ID (e.g. "1") or "[1]"
    # Let's extract the integer ID
    id_str = item_arg
    if id_str.startswith("["):
        id_str = id_str.lstrip("[").split("]")[0]
        
    try:
        sid = int(id_str)
    except ValueError:
        console.print("Snippet not found.")
        raise typer.Exit(code=0)

    snippet = manager.get_snippet(sid)
    if not snippet:
        console.print("Snippet not found.")
        raise typer.Exit(code=0)

    # Output formatted details for fzf preview
    console.print(f"[bold cyan]Command[/bold cyan]     : {snippet['command']}")
    console.print(f"[bold cyan]Description[/bold cyan] : {snippet['description']}")
    console.print(f"[bold cyan]Tags[/bold cyan]        : {snippet['tags']}")
    console.print(f"[bold cyan]Source[/bold cyan]      : {snippet['source']}")
    console.print(f"[bold cyan]Uses[/bold cyan]        : {snippet['use_count']}")
    
    last_used_str = snippet.get("last_used")
    if last_used_str:
        try:
            dt = datetime.fromisoformat(last_used_str)
            last_used_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    else:
        last_used_str = "Never"
        
    console.print(f"[bold cyan]Last used[/bold cyan]   : {last_used_str}")
