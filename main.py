"""CLI entry point: prompt loop, slash commands, UI rendering."""

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from agent import Agent
from config import Config, is_configured, _run_setup_wizard
from llm import LLMClient
from memory import get_memory_sources
from models import get_context_window, estimate_tokens

APP_VERSION = "0.1"

MASCOT = r"""   ╭──────╮
   │ o  o │
   │ \__/ │
   ╰─┬──┬─╯
     │  │
    _┴──┴_"""


class SlashCommandCompleter(Completer):
    """Autocomplete for / commands, @file refs, and path completion."""

    COMMANDS = [
        "/undo", "/clear", "/history", "/resume", "/context",
        "/compact", "/plan", "/help", "/exit", "/settings", "/memory",
    ]

    IGNORE_DIRS = {
        "__pycache__", ".git", ".pytest_cache", ".myai",
        "node_modules", ".claude", "docs", "tests", ".env",
    }

    def __init__(self):
        self.path_completer = PathCompleter()
        self.file_index = self._build_file_index()

    def _build_file_index(self) -> List[str]:
        files = []
        for root, dirs, filenames in os.walk("."):
            dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS]
            for filename in filenames:
                if filename.startswith("."):
                    continue
                filepath = os.path.join(root, filename)
                files.append(filepath.lstrip("./").replace("\\", "/"))
        return sorted(files)

    def _get_at_query(self, text: str) -> tuple[str, str]:
        if "#L" in text:
            at_part = text[:text.index("#L")]
            line_range = text[text.index("#L"):]
        else:
            at_part = text
            line_range = ""
        query = at_part[1:] if at_part.startswith("@") else at_part
        return query, line_range

    def get_completions(self, document, complete_event):
        text = document.text
        if text.startswith("/"):
            for cmd in self.COMMANDS:
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text))
        elif text.startswith("@"):
            query, line_range = self._get_at_query(text)
            for filepath in self.file_index:
                if query in filepath:
                    yield Completion(f"@{filepath}{line_range}", start_position=-len(text))
        elif "/" in text or (len(text) > 1 and text[0] == "."):
            for c in self.path_completer.get_completions(document, complete_event):
                yield c


class ClaudeCLI:
    """Interactive CLI shell. Delegates LLM + tool work to Agent."""

    def __init__(self):
        self.console = Console()
        self.config = Config()
        self.agent: Optional[Agent] = None
        self._pending_confirmation = False
        self._kb = self._build_keybindings()

    def _build_keybindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add(Keys.ControlC, eager=True)
        def _(event):
            if self._pending_confirmation:
                self._pending_confirmation = False
                self.console.print("\n[yellow]Operation cancelled.[/yellow]")
                event.app.exit()
            else:
                event.app.exit()

        @kb.add(Keys.Tab)
        def _(event):
            buffer = event.app.current_buffer
            if not buffer.complete_state:
                buffer.start_completion()

        @kb.add(Keys.ControlJ)
        def _(event):
            buffer = event.app.current_buffer
            if buffer.complete_state:
                buffer.complete_state = None

        @kb.add(Keys.Enter, eager=True)
        def _(event):
            buffer = event.app.current_buffer
            if buffer.complete_state:
                buffer.complete_state = None
            else:
                buffer.validate_and_handle()

        return kb

    # ---- lifecycle ---------------------------------------------------

    def run(self) -> None:
        if not is_configured():
            self.console.print("[yellow]First time setup required.[/yellow]\n")
            self.config = _run_setup_wizard()

        self._init_agent()

        self.console.print()
        self.console.print(self._render_banner())
        self.console.print()

        session = PromptSession(
            completer=SlashCommandCompleter(),
            key_bindings=self._kb,
            auto_suggest=AutoSuggestFromHistory(),
        )

        while True:
            try:
                from prompt_toolkit.formatted_text import FormattedText
                prompt_fragments = (
                    [("ansicyan bold", "plan \u276f ")]
                    if self.agent.plan_mode
                    else [("", "\u276f ")]
                )
                user_input = session.prompt(FormattedText(prompt_fragments))
            except KeyboardInterrupt:
                continue

            if not user_input.strip():
                continue

            if user_input.startswith("/") and self._handle_slash_command(user_input):
                continue

            self.agent.process(user_input)

    def _init_agent(self) -> None:
        base_url = self.config.base_url.lower()
        provider = "anthropic" if "anthropic" in base_url else "openai"
        llm = LLMClient(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            model=self.config.model,
            temperature=self.config.temperature,
            provider=provider,
        )
        self.agent = Agent(
            llm=llm,
            config=self.config,
            console=self.console,
            confirm_callback=self._confirm,
        )

    # ---- slash command dispatch --------------------------------------

    def _handle_slash_command(self, command: str) -> bool:
        cmd = command.strip().lower()
        handlers = {
            "/help": self._show_help,
            "/settings": self._open_settings,
            "/history": self._show_history,
            "/clear": self._clear_history,
            "/exit": self._exit,
            "/undo": self._undo,
            "/memory": self._show_memory,
            "/resume": self._resume,
            "/context": self._show_context,
            "/compact": self._compact,
            "/plan": self._toggle_plan,
        }
        handler = handlers.get(cmd)
        if handler:
            handler()
            return True
        return False

    # ---- UI rendering ------------------------------------------------

    def _render_banner(self):
        model = self.config.get("model", "?")
        provider = self.config.get("provider", "?")
        cwd = str(Path.cwd())

        left = Group(
            Align.center(Text("Welcome back!", style="bold white")),
            Text(""),
            Align.center(Text(MASCOT, style="bold cyan")),
            Text(""),
            Align.center(Text.assemble(
                (model, "bold green"), ("  \u00b7  ", "dim"), (provider, "cyan"),
            )),
            Align.center(Text(cwd, style="dim")),
        )

        tips = Table.grid(padding=(0, 1))
        tips.add_column(style="cyan", no_wrap=True)
        tips.add_column(style="dim")
        tips.add_row("/help", "show all commands")
        tips.add_row("/memory", "view loaded CLAUDE.md files")
        tips.add_row("@file", "include a file in your message")

        sources = get_memory_sources()
        if sources:
            mem_body = Table.grid(padding=(0, 1))
            mem_body.add_column(style="cyan", no_wrap=True)
            mem_body.add_column(style="dim", no_wrap=True)
            mem_body.add_column(justify="right", style="green")
            for s in sources:
                mem_body.add_row(s.label, str(s.path.name), f"{s.chars:,}")
        else:
            mem_body = Text(
                "No memory loaded. Create ./CLAUDE.md to add project rules.",
                style="dim",
            )

        right = Group(
            Text("Tips for getting started", style="bold cyan underline"),
            tips,
            Text(""),
            Text("Memory", style="bold cyan underline"),
            mem_body,
        )

        layout = Table.grid(expand=True, padding=(0, 2))
        layout.add_column(ratio=1)
        layout.add_column(ratio=1)
        layout.add_row(left, right)

        return Panel(
            layout,
            title=f"[bold cyan]My Claude Code[/bold cyan] [dim]v{APP_VERSION}[/dim]",
            border_style="cyan",
            padding=(1, 2),
        )

    def _show_help(self) -> None:
        commands_table = Table(
            show_header=True, header_style="bold cyan",
            border_style="dim", pad_edge=False,
        )
        commands_table.add_column("Command", style="cyan", width=12)
        commands_table.add_column("Description")
        rows = [
            ("/help", "Show this help message"),
            ("/settings", "Display current configuration"),
            ("/history", "Show current session's messages"),
            ("/resume", "Continue the most recent previous session"),
            ("/context", "Show token usage vs. model's context window"),
            ("/compact", "Summarize history to free up context"),
            ("/plan", "Toggle read-only plan mode (no file writes)"),
            ("/clear", "Clear the current session"),
            ("/undo", "Undo the last file edit"),
            ("/memory", "Show loaded memory (CLAUDE.md files)"),
            ("/exit", "Exit the program"),
        ]
        for c, d in rows:
            commands_table.add_row(c, d)

        examples = Table(show_header=False, border_style="dim", pad_edge=False)
        examples.add_column("Example", style="dim italic")
        examples.add_row("Create a README.md with # My Project")
        examples.add_row("Edit app.py to add print('hello') at line 5")
        examples.add_row("Delete lines 1-3 in temp.txt")

        self.console.print(Panel(commands_table, title="[bold]Commands[/bold]",
                                 border_style="cyan", padding=(0, 2)))
        self.console.print(Panel(examples, title="[bold]Examples[/bold]",
                                 border_style="green", padding=(0, 2)))
        self.console.print(Panel(
            "[dim]- All file modifications require confirmation (y/N)\n"
            "- Files are backed up before changes\n"
            "- Use [cyan]/undo[/cyan] to restore from backup[/dim]",
            title="[bold]Safety[/bold]", border_style="yellow", padding=(0, 2),
        ))

    def _open_settings(self) -> None:
        table = Table(show_header=False, border_style="dim", pad_edge=False)
        table.add_column("Key", style="bold cyan", width=18)
        table.add_column("Value", style="green")

        api_key = self.config.api_key
        masked = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"

        table.add_row("Provider", self.config.get("provider", ""))
        table.add_row("API Key", masked)
        table.add_row("Base URL", self.config.get("base_url", ""))
        table.add_row("Model", self.config.get("model", ""))
        table.add_row("Temperature", str(self.config.get("temperature", "")))
        table.add_row("Max History Tokens", str(self.config.get("max_history_tokens", "")))

        self.console.print(Panel(table, title="[bold]Current Settings[/bold]",
                                 border_style="cyan", padding=(0, 2)))

    def _show_history(self) -> None:
        if not self.agent.messages:
            self.console.print("[dim]No conversation history in this session.[/dim]")
            return
        from rich.markup import escape
        lines = ["Conversation Summary:"]
        for i, msg in enumerate(self.agent.messages, 1):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if len(content) > 100:
                content = content[:97] + "..."
            lines.append(f"{i}. {role}: {content}")
        self.console.print(f"[dim]{escape(chr(10).join(lines))}[/dim]")

    def _clear_history(self) -> None:
        self.agent.clear()
        self.console.print("[green]\u2713[/green] Conversation history cleared.")

    def _resume(self) -> None:
        target, count = self.agent.resume_latest()
        if target is None:
            self.console.print("[dim]No previous sessions to resume.[/dim]")
            return
        self.console.print(
            f"[green]\u2713[/green] Resumed [cyan]{target.name}[/cyan] "
            f"— {count} message(s) loaded."
        )

    def _show_memory(self) -> None:
        sources = get_memory_sources()
        if not sources:
            self.console.print(
                "[dim]No memory loaded. Create [cyan]./CLAUDE.md[/cyan] or "
                "[cyan]~/.claude/CLAUDE.md[/cyan] to add persistent instructions.[/dim]"
            )
            return

        table = Table(show_header=True, header_style="bold cyan",
                      border_style="dim", pad_edge=False)
        table.add_column("Layer", style="cyan", width=8)
        table.add_column("Path")
        table.add_column("Chars", justify="right", style="green")

        total = 0
        for src in sources:
            table.add_row(src.label, str(src.path), f"{src.chars:,}")
            total += src.chars

        self.console.print(Panel(
            table,
            title=f"[bold]Loaded memory[/bold] ([green]{total:,}[/green] chars total)",
            border_style="cyan", padding=(0, 2),
        ))

    def _show_context(self) -> None:
        model = self.config.model
        window = get_context_window(model)
        used = self.agent.last_input_tokens

        if used == 0 and self.agent.messages:
            used = sum(estimate_tokens(m.get("content", "")) for m in self.agent.messages)
            source = "estimated"
        else:
            source = "last API response"

        pct = (used / window * 100) if window else 0.0
        bar_width = 30
        filled = min(bar_width, int(bar_width * used / window)) if window else 0
        bar = (
            "[green]" + "\u2588" * filled + "[/green]"
            + "[dim]" + "\u2591" * (bar_width - filled) + "[/dim]"
        )

        table = Table(show_header=False, border_style="dim", pad_edge=False)
        table.add_column("", style="bold cyan", width=14)
        table.add_column("")
        table.add_row("Model", model)
        table.add_row("Window", f"{window:,} tokens")
        table.add_row("Used", f"{used:,} tokens ({pct:.1f}%) [dim]\u00b7 {source}[/dim]")
        table.add_row("", bar)
        table.add_row("Last output", f"{self.agent.last_output_tokens:,} tokens")
        table.add_row("Messages", f"{len(self.agent.messages)}")

        self.console.print(Panel(
            table, title="[bold]Context usage[/bold]",
            border_style="cyan", padding=(0, 2),
        ))

    def _compact(self) -> None:
        self.agent.compact()

    def _toggle_plan(self) -> None:
        self.agent.plan_mode = not self.agent.plan_mode
        if self.agent.plan_mode:
            self.console.print(
                "[cyan]\u25b6 Plan mode ON[/cyan] — file writes blocked. "
                "The model will produce a plan only."
            )
        else:
            self.console.print(
                "[green]\u25cf Plan mode OFF[/green] — file writes re-enabled."
            )

    def _undo(self) -> None:
        result = self.agent.undo_last_edit()
        mark = "[green]\u2713[/green]" if result["success"] else "[red]\u2717[/red]"
        self.console.print(f"{mark} {result['message']}")

    def _exit(self) -> None:
        self.console.print(Rule(style="dim"))
        self.console.print("[bold green]Goodbye![/bold green]")
        sys.exit(0)

    # ---- confirmation callback (passed into Agent) -------------------

    def _confirm(self, result: Dict[str, Any]) -> bool:
        """Render diff or command and ask y/N. Invoked by Agent for destructive tools."""
        self._pending_confirmation = True

        diff = result.get("diff")
        command = result.get("command")
        if diff:
            syntax = Syntax(diff, "diff", theme="monokai", line_numbers=True)
            self.console.print(Panel(
                syntax, title="[bold]Proposed changes[/bold]",
                border_style="yellow", padding=(0, 1),
            ))
        elif command:
            description = result.get("description") or ""
            timeout = result.get("timeout", "")
            body = Syntax(command, "bash", theme="monokai", word_wrap=True)
            subtitle = f"timeout {timeout}s" + (f" · {description}" if description else "")
            self.console.print(Panel(
                body, title="[bold]Run command[/bold]",
                subtitle=f"[dim]{subtitle}[/dim]",
                border_style="yellow", padding=(0, 1),
            ))

        self.console.print()
        session = PromptSession(key_bindings=self._kb)
        try:
            reply = session.prompt("Do you want to proceed? [y/N] ", key_bindings=self._kb)
        except KeyboardInterrupt:
            reply = "n"

        self._pending_confirmation = False
        return reply.lower() == "y"


def main():
    ClaudeCLI().run()


if __name__ == "__main__":
    main()
