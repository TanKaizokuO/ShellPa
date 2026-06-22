import typer

def register_dashboard_commands(app: typer.Typer) -> None:
    @app.command()
    def dashboard():
        """Launch the interactive Shellpa dashboard."""
        from shellpa.dashboard.manager import run_dashboard
        run_dashboard()
