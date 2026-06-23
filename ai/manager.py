import os
import sys
import json
import hashlib
import platform
import subprocess
import tempfile
from datetime import datetime
from typing import Optional, List, Dict

from openai import OpenAI, AuthenticationError, APIConnectionError, APIError
from rich.console import Console
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.panel import Panel
from shellpa.dotfiles.manager import DynamicPath

console = Console()

# ─── Paths ────────────────────────────────────────────────────────────────────
CACHE_PATH = DynamicPath(lambda: os.path.join(os.path.expanduser("~/.shellpa"), "ai_cache.json"))

# ─── Exception ────────────────────────────────────────────────────────────────
class AIError(Exception):
    """User-friendly wrapper for all AI-related failures."""

# ─── System Prompts ───────────────────────────────────────────────────────────
ASK_SYSTEM_PROMPT = (
    "You are a shell command expert. Given a natural language task and the "
    "user's OS/shell/cwd context, return ONLY a shell command with no "
    "explanation, no markdown, no code fences. If the task is destructive or "
    "irreversible (rm -rf, dd, mkfs, chmod -R 777, force-push, DROP TABLE, "
    "etc.), prepend a single line starting with 'WARNING:' explaining the "
    "risk, then the command on the next line."
)

EXPLAIN_SYSTEM_PROMPT = (
    "Explain this shell command in simple terms. Break it down flag by flag. "
    "Max 10 lines. Use markdown formatting."
)

FIX_SYSTEM_PROMPT = (
    "You are a shell debugging expert. Given a failed command, its exit "
    "code, and its stderr output, return ONLY a corrected shell command with "
    "no explanation, no markdown, no code fences. If you cannot determine a "
    "fix with reasonable confidence, return exactly: NO_FIX_AVAILABLE"
)

# ─── Dangerous command heuristics ─────────────────────────────────────────────
DANGEROUS_SUBSTRINGS = {
    "rm -rf",
    "sudo rm",
    "chmod 777",
    "chmod -r 777",
    "dd if=",
    "mkfs",
    ":(){ :|:& };:",
    "drop table",
}

DANGEROUS_FALLBACK_MSG = "This command may be destructive — review carefully before running."

# ─── Client ───────────────────────────────────────────────────────────────────
def get_client() -> OpenAI:
    """Builds the NIM-pointed OpenAI client. Raises AIError if key is missing."""
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        raise AIError(
            "NVIDIA_API_KEY not set. Export it in your shell profile or .env."
        )
    return OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=api_key,
    )

# ─── Context ──────────────────────────────────────────────────────────────────
def get_context() -> Dict:
    """Returns {os, shell, cwd}."""
    return {
        "os": platform.uname().system,
        "shell": os.environ.get("SHELL", ""),
        "cwd": os.getcwd(),
    }

def _context_hash(context: Dict) -> str:
    """SHA-256 of sorted JSON-encoded context dict."""
    raw = json.dumps(context, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()

# ─── NIM call ─────────────────────────────────────────────────────────────────
def call_nim(
    system_prompt: str,
    user_content: str,
    max_tokens: int = 512,
    temperature: float = 0.3,
) -> str:
    """
    Calls NVIDIA NIM and returns the stripped response text.
    Catches SDK errors and re-raises as AIError with user-friendly messages.
    Treats empty/whitespace response as AIError.
    """
    try:
        client = get_client()
        model = _get_model()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = response.choices[0].message.content
        if text:
            text = text.replace('\u200b', '').replace('\u200c', '').replace('\u200d', '').replace('\ufeff', '')
        if not text or not text.strip():
            raise AIError("Empty response from model.")
        return text.strip()
    except AuthenticationError:
        raise AIError(
            "Authentication failed — check that NVIDIA_API_KEY is valid and not expired."
        )
    except APIConnectionError:
        raise AIError(
            "Could not reach NVIDIA NIM. Check your network connection."
        )
    except APIError as e:
        raise AIError(f"NVIDIA NIM API error: {e}")
    except AIError:
        raise
    except Exception as e:
        raise AIError(f"Unexpected error calling NIM: {e}")

def _get_model() -> str:
    """Reads the model from ~/.shellpa/config.toml, falling back to the default."""
    try:
        from shellpa.dotfiles.manager import load_config
        config = load_config()
        return config.get("ai", {}).get("model", "meta/llama-3.1-70b-instruct")
    except Exception:
        return "meta/llama-3.1-70b-instruct"

# ─── Cache ────────────────────────────────────────────────────────────────────
MAX_CACHE_SIZE = 10

def load_cache() -> List[Dict]:
    """Reads ai_cache.json. Returns [] on missing/corrupt file (warn + reset)."""
    cache_path = str(CACHE_PATH)
    if not os.path.exists(cache_path):
        return []
    try:
        with open(cache_path, "r") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("Cache is not a list")
        return data
    except Exception as e:
        console.print(f"[yellow]Warning: AI cache corrupted ({e}), resetting.[/yellow]")
        _write_cache([])
        return []

def _write_cache(entries: List[Dict]) -> None:
    cache_path = str(CACHE_PATH)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(entries, f, indent=2)

def save_cache(query: str, context_hash: str, command: str) -> None:
    """Prepends new entry to cache, caps at MAX_CACHE_SIZE, writes back."""
    entries = load_cache()
    new_entry = {
        "query": query,
        "context_hash": context_hash,
        "command": command,
        "timestamp": datetime.now().isoformat(),
    }
    # Insert newest first, drop oldest beyond cap
    entries = [new_entry] + [e for e in entries if not (
        e.get("query") == query and e.get("context_hash") == context_hash
    )]
    _write_cache(entries[:MAX_CACHE_SIZE])

def find_cached(query: str, context_hash: str) -> Optional[str]:
    """Searches the cache for a matching (query, context_hash) pair. Returns command or None."""
    entries = load_cache()
    for entry in entries:
        if entry.get("query") == query and entry.get("context_hash") == context_hash:
            cmd = entry.get("command")
            if cmd:
                cleaned = cmd.replace('\u200b', '').replace('\u200c', '').replace('\u200d', '').replace('\ufeff', '').strip()
                if cleaned:
                    return cmd
    return None

# ─── Core AI functions ─────────────────────────────────────────────────────────
def ask(query: str) -> tuple[str, bool]:
    """
    Translates natural language to a shell command.
    Returns (command_text, from_cache).
    command_text may start with 'WARNING:\\n' line.
    """
    context = get_context()
    ctx_hash = _context_hash(context)

    cached = find_cached(query, ctx_hash)
    if cached:
        return cached, True

    os_str = context["os"]
    shell_str = context["shell"]
    cwd_str = context["cwd"]
    user_content = (
        f"Task: {query}\n"
        f"OS: {os_str}\n"
        f"Shell: {shell_str}\n"
        f"CWD: {cwd_str}"
    )
    result = call_nim(ASK_SYSTEM_PROMPT, user_content, max_tokens=256, temperature=0.2)
    save_cache(query, ctx_hash, result)
    return result, False

def explain(command: str) -> str:
    """Explains a shell command in markdown."""
    return call_nim(EXPLAIN_SYSTEM_PROMPT, command, max_tokens=768, temperature=0.4)

def suggest_fix(command: str, exit_code: int, stderr: str) -> Optional[str]:
    """
    Returns a corrected command string, or None if model says NO_FIX_AVAILABLE.
    """
    user_content = (
        f"Failed command: {command}\n"
        f"Exit code: {exit_code}\n"
        f"Stderr:\n{stderr}"
    )
    result = call_nim(FIX_SYSTEM_PROMPT, user_content, max_tokens=256, temperature=0.2)
    if result.strip() == "NO_FIX_AVAILABLE":
        return None
    return result

def is_dangerous(command: str) -> bool:
    """
    Local heuristic check for dangerous commands.
    Case-insensitive substring matching.
    """
    cmd_lower = command.lower()
    return any(substr in cmd_lower for substr in DANGEROUS_SUBSTRINGS)

# ─── Shell history ─────────────────────────────────────────────────────────────
def get_last_history_command() -> Optional[str]:
    """
    Reads the last non-empty command from shell history.
    Strips zsh's ': <timestamp>:0;' prefix.
    Returns None if no history file found or it's empty.
    """
    # Determine history file
    hist_file = os.environ.get("HISTFILE")
    if not hist_file:
        shell = os.environ.get("SHELL", "")
        if "zsh" in shell:
            hist_file = os.path.expanduser("~/.zsh_history")
        else:
            hist_file = os.path.expanduser("~/.bash_history")

    if not hist_file or not os.path.exists(hist_file):
        return None

    try:
        with open(hist_file, "r", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return None

    # Search from the end for a non-empty line
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        # Strip zsh extended format: ': <timestamp>:0;<command>'
        if line.startswith(": ") and ";0;" not in line:
            # zsh format is ': <timestamp>:0;<command>'
            pass
        if line.startswith(":") and ";" in line:
            # Try to parse zsh ': timestamp:0;command' format
            parts = line.split(";", 1)
            if len(parts) == 2 and parts[0].startswith(":"):
                candidate = parts[1].strip()
                if candidate:
                    return candidate
        return line

    return None


def handle_ask_result(raw_result: str, query: str = "AI suggested command") -> None:
    """Handles parsing warnings, rendering, and the interactive ask menu (Run, Save, Explain, Edit, Cancel)."""
    warning_text: Optional[str] = None
    command = raw_result

    lines = raw_result.splitlines()
    if lines and lines[0].strip().upper().startswith("WARNING:"):
        warning_text = lines[0].strip()[len("WARNING:"):].strip()
        command = "\n".join(lines[1:]).strip()

    if is_dangerous(command) and not warning_text:
        warning_text = DANGEROUS_FALLBACK_MSG

    if warning_text:
        console.print(Panel(f"[bold red]⚠ WARNING[/bold red]\n{warning_text}", border_style="red"))

    console.print(Syntax(command, "bash", theme="monokai", line_numbers=False))

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
                explanation = explain(command)
                console.print(Markdown(explanation))
            except AIError as e:
                console.print(f"[red]Error: {e}[/red]")
            # Re-show command and loop again
            console.print(Syntax(command, "bash", theme="monokai", line_numbers=False))

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
                console.print(Syntax(command, "bash", theme="monokai", line_numbers=False))

        elif choice == "c":
            console.print("[dim]Cancelled.[/dim]")
            break

        else:
            console.print("[yellow]Unknown option. Type R, S, E, Ed, or C.[/yellow]")
