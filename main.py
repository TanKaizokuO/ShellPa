import typer
from shellpa.dotfiles import dotfiles_app

app = typer.Typer(
    name="sp",
    help="Shellpa (sp) - An AI-powered shell assistant and dotfiles synchronizer.",
)

app.add_typer(dotfiles_app, name="dotfiles", help="Manage and sync dotfiles")

@app.callback()
def main():
    pass

if __name__ == "__main__":
    app()
