import typer
from dotenv import load_dotenv

load_dotenv()

from shellpa.dotfiles import dotfiles_app
from shellpa.cheatsheet.cli import cheatsheet_app
from shellpa.ai.cli import register_ai_commands
from shellpa.sync import sync_app
from shellpa.dashboard.cli import register_dashboard_commands

app = typer.Typer(
    name="sp",
    help="ShellPa (sp) - An AI-powered shell assistant and dotfiles synchronizer.",
)

app.add_typer(dotfiles_app, name="dotfiles", help="Manage and sync dotfiles")
app.add_typer(cheatsheet_app, name="cheatsheet", help="Manage shell snippets")
app.add_typer(sync_app, name="sync", help="Sync dotfiles and cheatsheets to GitHub")

@app.command()
def search():
    """Fuzzy search and run saved shell snippets."""
    from shellpa.cheatsheet.manager import open_fzf_session
    open_fzf_session()

# Register ask, explain, fix as flat top-level commands
register_ai_commands(app)
register_dashboard_commands(app)

@app.callback()
def main():
    pass

if __name__ == "__main__":
    app()
