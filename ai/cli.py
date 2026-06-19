import os
import subprocess
import tempfile
import typer
from typing import Optional

from rich.console import Console
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.panel import Panel

from shellpa.ai import manager as ai_manager
from shellpa.ai.manager import AIError

console = Console()


def _render_command(command: str) -> None:
    console.print(Syntax(command, "bash", theme="monokai", line_numbers=False))


def _show_warning_panel(text: str) -> None:
    console.print(Panel(f"[bold red]⚠ WARNING[/bold red]\n{text}", border_style="red"))


def _ask_menu(command: str, query: str) -> None:
    """Interactive dispatch loop for the ask/fix result."""
    while True:
        console.print(
            "\n[bold][[R]un  [S]ave  [E]xplain  [Ed]it  [C]ancel][/bold] ",
            end="",
        )
        choice = input().strip().lower()

        if choice == "r":
            console.print(f"[dim]Running:[/dim] {command}")
            result = subprocess.run(command, shell=True)
            console.print(f"[dim]Exit code: {result.returncode}[/dim]")
            break

        elif choice == "s":
            from shellpa.cheatsheet.manager import add_snippet
            sid = add_snippet(command, query, tags="ai", source="ai")
            console.print(f"[green]Snippet #{sid} saved.[/green]")
            break

        elif choice == "e":
            try:
                explanation = ai_manager.explain(command)
                console.print(Markdown(explanation))
            except AIError as e:
                console.print(f"[red]Error: {e}[/red]")
            # Re-show command and loop again
            _render_command(command)

        elif choice == "ed":
            editor = os.environ.get("EDITOR", "nano")
            with tempfile.NamedTemporaryFile(suffix=".sh", delete=False, mode="w") as tf:
                tf.write(command)
                temp_path = tf.name
            try:
                subprocess.run([editor, temp_path], check=True)
                with open(temp_path, "r") as f:
                    command = f.read().strip()
            except Exception as exc:
                console.print(f"[red]Error opening editor: {exc}[/red]")
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            if command:
                _render_command(command)

        elif choice == "c":
            console.print("[dim]Cancelled.[/dim]")
            break

        else:
            console.print("[yellow]Unknown option. Type R, S, E, Ed, or C.[/yellow]")


def _fix_menu(command: str) -> None:
    """Interactive dispatch loop for the fix result (no Explain option)."""
    while True:
        console.print(
            "\n[bold][[R]un  [S]ave  [Ed]it  [C]ancel][/bold] ",
            end="",
        )
        choice = input().strip().lower()

        if choice == "r":
            console.print(f"[dim]Running:[/dim] {command}")
            result = subprocess.run(command, shell=True)
            console.print(f"[dim]Exit code: {result.returncode}[/dim]")
            break

        elif choice == "s":
            from shellpa.cheatsheet.manager import add_snippet
            sid = add_snippet(command, "Fix from sp fix", tags="ai,fix", source="ai")
            console.print(f"[green]Snippet #{sid} saved.[/green]")
            break

        elif choice == "ed":
            editor = os.environ.get("EDITOR", "nano")
            with tempfile.NamedTemporaryFile(suffix=".sh", delete=False, mode="w") as tf:
                tf.write(command)
                temp_path = tf.name
            try:
                subprocess.run([editor, temp_path], check=True)
                with open(temp_path, "r") as f:
                    command = f.read().strip()
            except Exception as exc:
                console.print(f"[red]Error opening editor: {exc}[/red]")
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            if command:
                _render_command(command)

        elif choice == "c":
            console.print("[dim]Cancelled.[/dim]")
            break

        else:
            console.print("[yellow]Unknown option. Type R, S, Ed, or C.[/yellow]")


def register_ai_commands(app: typer.Typer) -> None:
    """Registers ask, explain, fix as flat commands on the given Typer app."""

    @app.command()
    def ask(
        query: str = typer.Argument(..., help="Natural language description of the shell command you want.")
    ):
        """Translate natural language into a shell command using AI."""
        try:
            raw_result, from_cache = ai_manager.ask(query)
        except AIError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(code=1)

        # Parse optional WARNING: prefix from model
        warning_text: Optional[str] = None
        command = raw_result

        lines = raw_result.splitlines()
        if lines and lines[0].strip().upper().startswith("WARNING:"):
            warning_text = lines[0].strip()[len("WARNING:"):].strip()
            command = "\n".join(lines[1:]).strip()

        # is_dangerous backstop: show panel even if model didn't warn
        if ai_manager.is_dangerous(command) and not warning_text:
            warning_text = ai_manager.DANGEROUS_FALLBACK_MSG

        if from_cache:
            console.print("[dim](from cache)[/dim]")

        if warning_text:
            _show_warning_panel(warning_text)

        _render_command(command)
        _ask_menu(command, query)

    @app.command()
    def explain(
        command: str = typer.Argument(..., help="Shell command to explain.")
    ):
        """Explain a shell command flag by flag using AI."""
        try:
            text = ai_manager.explain(command)
        except AIError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(code=1)
        console.print(Markdown(text))

    @app.command()
    def fix():
        """Read the last failed shell command from history and suggest a fix."""
        last_cmd = ai_manager.get_last_history_command()
        if not last_cmd:
            console.print(
                "[yellow]Warning: No shell history file found or it's empty.[/yellow]"
            )
            raise typer.Exit(code=0)

        console.print(f"Last command: [bold]{last_cmd}[/bold]")
        confirm = console.input("Re-run to capture the error? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes"):
            console.print("[dim]Aborted.[/dim]")
            raise typer.Exit(code=0)

        try:
            result = subprocess.run(
                last_cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            console.print("[red]Command timed out after 30s.[/red]")
            raise typer.Exit(code=1)

        if result.returncode == 0:
            console.print("[green]Last command succeeded, nothing to fix.[/green]")
            raise typer.Exit(code=0)

        stderr = result.stderr.strip()
        console.print(f"[dim]Exit code: {result.returncode}[/dim]")
        if stderr:
            console.print(f"[dim]Stderr:[/dim]\n{stderr}")

        try:
            fix_cmd = ai_manager.suggest_fix(last_cmd, result.returncode, stderr)
        except AIError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(code=1)

        if fix_cmd is None:
            console.print("[yellow]Could not determine a fix (NO_FIX_AVAILABLE).[/yellow]")
            raise typer.Exit(code=0)

        console.print("\n[bold]Suggested fix:[/bold]")
        _render_command(fix_cmd)
        _fix_menu(fix_cmd)
