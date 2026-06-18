import typer
from typing import Optional
from shellpa.dotfiles import manager

dotfiles_app = typer.Typer(
    name="dotfiles",
    help="Manage and synchronize tracked dotfiles.",
    no_args_is_help=True
)

@dotfiles_app.command(name="backup")
def backup():
    """Copies all tracked files to ~/.shellpa/dotfiles/ with metadata."""
    try:
        manager.backup_files()
    except Exception as e:
        # manager already prints detailed messages
        raise typer.Exit(code=1)

@dotfiles_app.command(name="restore")
def restore(
    path: Optional[str] = typer.Argument(
        None,
        help="Optional path to a single file to restore. If omitted, restores all files."
    )
):
    """Restores files from backup to their original locations."""
    try:
        manager.restore_files(path)
    except Exception as e:
        raise typer.Exit(code=1)

@dotfiles_app.command(name="status")
def status():
    """Shows status of all tracked files on disk vs backup."""
    try:
        manager.show_status()
    except Exception as e:
        raise typer.Exit(code=1)

@dotfiles_app.command(name="list")
def list_tracked():
    """Lists all tracked file paths from config.toml."""
    try:
        manager.list_files()
    except Exception as e:
        raise typer.Exit(code=1)

@dotfiles_app.command(name="add")
def add(
    path: str = typer.Argument(..., help="Path of the file to add to tracked files list.")
):
    """Adds a new file to the tracked list in config.toml."""
    try:
        manager.add_file(path)
    except ValueError as e:
        raise typer.BadParameter(str(e))
    except Exception as e:
        raise typer.Exit(code=1)

@dotfiles_app.command(name="remove")
def remove(
    path: str = typer.Argument(..., help="Path of the file to remove from tracked files list.")
):
    """Removes a file from the tracked list and optionally deletes its backup."""
    try:
        manager.remove_file(path)
    except ValueError as e:
        raise typer.BadParameter(str(e))
    except Exception as e:
        raise typer.Exit(code=1)
