import typer
from shellpa.dotfiles import dotfiles_app
from shellpa.cheatsheet.cli import cheatsheet_app

app = typer.Typer(
    name="sp",
    help="Shellpa (sp) - An AI-powered shell assistant and dotfiles synchronizer.",
)

app.add_typer(dotfiles_app, name="dotfiles", help="Manage and sync dotfiles")
app.add_typer(cheatsheet_app, name="cheatsheet", help="Manage shell snippets")

@app.command()
def search():
    """Fuzzy search and run saved shell snippets."""
    from shellpa.cheatsheet.manager import open_fzf_session
    open_fzf_session()

@app.callback()
def main():
    pass

if __name__ == "__main__":
    app()
