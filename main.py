"""Main CLI program for Claude CLI."""

import glob
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from rich.align import Align
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.status import Status

APP_VERSION = "0.1"

_DEBUG = os.environ.get("MYAI_DEBUG") == "1"


def _trace(msg: str) -> None:
    """Emit a trace log to stderr when MYAI_DEBUG=1, otherwise silent.

    Separates debug noise from the interactive UI (which owns stdout).
    """
    if _DEBUG:
        print(f"[TRACE] {msg}", file=sys.stderr)

MASCOT = r"""   ╭──────╮
   │ o  o │
   │ \__/ │
   ╰─┬──┬─╯
     │  │
    _┴──┴_"""

from config import Config, is_configured, _run_setup_wizard
from history import SessionHistory, list_sessions, preview_session
from llm import LLMClient, ToolCall
from memory import build_system_prompt, get_memory_sources
from tools import FileWriteTool, FileEditTool, FileReadTool, GrepTool, PathSecurityError, FileToolError


class SlashCommandCompleter(Completer):
    """Custom completer for slash commands, file paths, and @ file references."""

    COMMANDS = ["/undo", "/clear", "/history", "/resume", "/help", "/exit", "/settings", "/memory"]

    # Ignore patterns for file index
    IGNORE_DIRS = {'__pycache__', '.git', '.pytest_cache', '.myai', 'node_modules', '.claude', 'docs', 'tests', '.env'}

    def __init__(self, file_tools: FileEditTool):
        self.path_completer = PathCompleter()
        # Build file index at startup
        self.file_index = self._build_file_index()

    def _build_file_index(self) -> List[str]:
        """Build a list of files for @ autocomplete."""
        files = []
        for root, dirs, filenames in os.walk('.'):
            # Filter out ignored directories in-place
            dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS]
            for filename in filenames:
                if filename.startswith('.'):
                    continue
                filepath = os.path.join(root, filename)
                # Use forward slashes for consistency
                files.append(filepath.lstrip('./').replace('\\', '/'))
        return sorted(files)

    def _get_at_query(self, text: str) -> tuple[str, str]:
        """Extract query and line range from @ text.

        Returns:
            (query, line_range) - e.g., ("config", "#L10-20") or ("conf", "")
        """
        # Handle @filepath#L10-20 format
        if '#L' in text:
            at_part = text[:text.index('#L')]
            line_range = text[text.index('#L'):]
        else:
            at_part = text
            line_range = ""

        # Extract query (remove @ prefix)
        query = at_part[1:] if at_part.startswith('@') else at_part
        return query, line_range

    def get_completions(self, document, complete_event):
        """Generate completions based on input."""
        text = document.text

        # If starts with /, complete commands
        if text.startswith("/"):
            for cmd in self.COMMANDS:
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text))

        # If starts with @, complete file references
        elif text.startswith("@"):
            query, line_range = self._get_at_query(text)
            for filepath in self.file_index:
                if query in filepath:
                    # Append line range if present in original text
                    completion_text = f"@{filepath}{line_range}"
                    yield Completion(completion_text, start_position=-len(text))

        # If starts with a path, complete file paths
        elif "/" in text or (len(text) > 1 and text[0] == "."):
            # Use path completer for file paths
            for completion in self.path_completer.get_completions(document, complete_event):
                yield completion


class ClaudeCLI:
    """Main CLI application class."""

    def __init__(self):
        self.console = Console()
        self.config = Config()
        self.history = SessionHistory()
        # In-memory LLM-format context. Starts empty on every launch; populated
        # by new user messages or /resume. Persistence lives in self.history.
        self.messages: List[Dict[str, Any]] = []
        self.read_tool = FileReadTool()
        self.write_tool = FileWriteTool()
        self.edit_tool = FileEditTool(read_tool=self.read_tool)
        self.grep_tool = GrepTool()
        self.llm: Optional[LLMClient] = None
        self._pending_confirmation = False
        self._last_tool_cancelled = False

        # Key bindings for CLI
        self._kb = KeyBindings()

        @self._kb.add(Keys.ControlC, eager=True)
        def _(event):
            """Handle Ctrl+C to cancel pending operation."""
            if self._pending_confirmation:
                self._pending_confirmation = False
                self.console.print("\n[yellow]Operation cancelled.[/yellow]")
                event.app.exit()
            else:
                event.app.exit()

        @self._kb.add(Keys.Tab)
        def trigger_completion(event):
            """Tab: trigger completion if none active."""
            buffer = event.app.current_buffer
            if not buffer.complete_state:
                buffer.start_completion()

        @self._kb.add(Keys.ControlJ)
        def accept_completion_only(event):
            """Ctrl+J: accept completion and stay in input."""
            buffer = event.app.current_buffer
            if buffer.complete_state:
                buffer.complete_state = None

        @self._kb.add(Keys.Enter, eager=True)
        def handle_enter(event):
            """Enter: accept completion without submitting when menu is active."""
            buffer = event.app.current_buffer
            if buffer.complete_state:
                buffer.complete_state = None
            else:
                buffer.validate_and_handle()

    def _run_setup_wizard(self) -> None:
        """Run the initial setup wizard."""
        config = _run_setup_wizard()
        self.config = config
        self._init_llm()

    def _init_llm(self) -> None:
        """Initialize the LLM client."""
        # Determine provider from base_url
        base_url = self.config.base_url.lower()
        if "anthropic" in base_url:
            provider = "anthropic"
        else:
            provider = "openai"

        self.llm = LLMClient(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            model=self.config.model,
            temperature=self.config.temperature,
            provider=provider,
        )

    def _handle_slash_command(self, command: str) -> bool:
        """
        Handle a slash command.

        Returns:
            True if the command was handled, False if it should be passed to LLM.
        """
        cmd = command.strip().lower()

        if cmd == "/help":
            self._show_help()
            return True
        elif cmd == "/settings":
            self._open_settings()
            return True
        elif cmd == "/history":
            self._show_history()
            return True
        elif cmd == "/clear":
            self._clear_history()
            return True
        elif cmd == "/exit":
            self._exit()
            return True
        elif cmd == "/undo":
            self._undo()
            return True
        elif cmd == "/memory":
            self._show_memory()
            return True
        elif cmd == "/resume":
            self._resume()
            return True

        return False

    def _render_banner(self):
        """Build a two-column welcome banner (inspired by Claude Code's start screen).

        Left column: greeting + mascot + current config (model, provider, cwd).
        Right column: Tips (common commands) + Memory status (loaded CLAUDE.md files).
        """
        model = self.config.get("model", "?")
        provider = self.config.get("provider", "?")
        cwd = str(Path.cwd())

        welcome = Text("Welcome back!", style="bold white")
        mascot = Text(MASCOT, style="bold cyan")
        config_line = Text.assemble(
            (model, "bold green"),
            ("  ·  ", "dim"),
            (provider, "cyan"),
        )
        cwd_line = Text(cwd, style="dim")

        left = Group(
            Align.center(welcome),
            Text(""),
            Align.center(mascot),
            Text(""),
            Align.center(config_line),
            Align.center(cwd_line),
        )

        # Right column: Tips section
        tips_title = Text("Tips for getting started", style="bold cyan underline")
        tips = Table.grid(padding=(0, 1))
        tips.add_column(style="cyan", no_wrap=True)
        tips.add_column(style="dim")
        tips.add_row("/help", "show all commands")
        tips.add_row("/memory", "view loaded CLAUDE.md files")
        tips.add_row("@file", "include a file in your message")

        # Right column: Memory section
        memory_title = Text("Memory", style="bold cyan underline")
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
            tips_title,
            tips,
            Text(""),
            memory_title,
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
        """Display help information."""
        commands_table = Table(show_header=True, header_style="bold cyan", border_style="dim", pad_edge=False)
        commands_table.add_column("Command", style="cyan", width=12)
        commands_table.add_column("Description")
        commands_table.add_row("/help", "Show this help message")
        commands_table.add_row("/settings", "Display current configuration")
        commands_table.add_row("/history", "Show current session's messages")
        commands_table.add_row("/resume", "Continue the most recent previous session")
        commands_table.add_row("/clear", "Clear the current session")
        commands_table.add_row("/undo", "Undo the last file edit")
        commands_table.add_row("/memory", "Show loaded memory (CLAUDE.md files)")
        commands_table.add_row("/exit", "Exit the program")

        examples_table = Table(show_header=False, border_style="dim", pad_edge=False)
        examples_table.add_column("Example", style="dim italic")
        examples_table.add_row("Create a README.md with # My Project")
        examples_table.add_row("Edit app.py to add print('hello') at line 5")
        examples_table.add_row("Delete lines 1-3 in temp.txt")

        self.console.print(Panel(
            commands_table,
            title="[bold]Commands[/bold]",
            border_style="cyan",
            padding=(0, 2),
        ))
        self.console.print(Panel(
            examples_table,
            title="[bold]Examples[/bold]",
            border_style="green",
            padding=(0, 2),
        ))
        self.console.print(Panel(
            "[dim]- All file modifications require confirmation (y/N)\n"
            "- Files are backed up before changes\n"
            "- Use [cyan]/undo[/cyan] to restore from backup[/dim]",
            title="[bold]Safety[/bold]",
            border_style="yellow",
            padding=(0, 2),
        ))

    def _open_settings(self) -> None:
        """Display current configuration."""
        table = Table(show_header=False, border_style="dim", pad_edge=False)
        table.add_column("Key", style="bold cyan", width=18)
        table.add_column("Value", style="green")

        # Mask API key for display
        api_key = self.config.api_key
        masked_key = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"

        table.add_row("Provider", self.config.get("provider", ""))
        table.add_row("API Key", masked_key)
        table.add_row("Base URL", self.config.get("base_url", ""))
        table.add_row("Model", self.config.get("model", ""))
        table.add_row("Temperature", str(self.config.get("temperature", "")))
        table.add_row("Max History Tokens", str(self.config.get("max_history_tokens", "")))

        self.console.print(Panel(table, title="[bold]Current Settings[/bold]", border_style="cyan", padding=(0, 2)))

    def _show_history(self) -> None:
        """Display the current session's in-memory messages."""
        if not self.messages:
            self.console.print("[dim]No conversation history in this session.[/dim]")
            return
        from rich.markup import escape
        lines = ["Conversation Summary:"]
        for i, msg in enumerate(self.messages, 1):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if len(content) > 100:
                content = content[:97] + "..."
            lines.append(f"{i}. {role}: {content}")
        self.console.print(f"[dim]{escape(chr(10).join(lines))}[/dim]")

    def _clear_history(self) -> None:
        """Clear the current session (both in-memory and its jsonl file)."""
        self.messages = []
        self.history.clear()
        self.console.print("[green]\u2713[/green] Conversation history cleared.")

    def _resume(self) -> None:
        """Load the most recent previous session into the current context.

        Subsequent messages continue writing into the resumed session's file,
        matching Claude Code's `--resume` semantics.
        """
        previous = list_sessions(limit=5, exclude=self.history.history_file)
        if not previous:
            self.console.print("[dim]No previous sessions to resume.[/dim]")
            return

        target = previous[0]
        loaded = self.history.resume_from(target)

        # Translate persisted jsonl entries into LLM-API message format.
        self.messages = []
        for entry in loaded:
            role = entry.get("role")
            content = entry.get("content", "")
            if role == "user":
                self.messages.append({"role": "user", "content": content})
            elif role == "assistant" and content:
                self.messages.append({"role": "assistant", "content": content})
            elif role == "tool":
                self.messages.append({
                    "role": "tool",
                    "content": content,
                    "tool_call_id": entry.get("tool_call_id", ""),
                })

        self.console.print(
            f"[green]\u2713[/green] Resumed [cyan]{target.name}[/cyan] "
            f"— {len(self.messages)} message(s) loaded."
        )

    def _show_memory(self) -> None:
        """Display which CLAUDE.md files are loaded into the system prompt."""
        sources = get_memory_sources()
        if not sources:
            self.console.print(
                "[dim]No memory loaded. Create [cyan]./CLAUDE.md[/cyan] or "
                "[cyan]~/.claude/CLAUDE.md[/cyan] to add persistent instructions.[/dim]"
            )
            return

        table = Table(show_header=True, header_style="bold cyan", border_style="dim", pad_edge=False)
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
            border_style="cyan",
            padding=(0, 2),
        ))

    def _undo(self) -> None:
        """Undo the last file edit."""
        result = self.edit_tool.undo_last()
        if result["success"]:
            self.console.print(f"[green]\u2713[/green] {result['message']}")
        else:
            self.console.print(f"[red]\u2717[/red] {result['message']}")

    def _exit(self) -> None:
        """Exit the program."""
        self.console.print(Rule(style="dim"))
        self.console.print("[bold green]Goodbye![/bold green]")
        sys.exit(0)

    def run(self) -> None:
        """Main interactive loop."""
        _trace("run() started")  # 日志: 函数入口

        # Check configuration
        if not is_configured():
            _trace("is_configured() = False, running setup wizard")
            self.console.print("[yellow]First time setup required.[/yellow]\n")
            self._run_setup_wizard()
        else:
            _trace("is_configured() = True, initializing LLM")
            self._init_llm()

        _trace("LLM initialized, printing banner")
        self.console.print()
        self.console.print(self._render_banner())
        self.console.print()

        # Create prompt session
        _trace("Creating PromptSession")
        session = PromptSession(
            completer=SlashCommandCompleter(self.edit_tool),
            key_bindings=self._kb,
            auto_suggest=AutoSuggestFromHistory(),
        )

        _trace("Entering main loop")
        while True:
            try:
                _trace("session.prompt() waiting for user input...")
                user_input = session.prompt("\u276f ")
                _trace(f"User input received: {user_input[:50]!r}{'...' if len(user_input) > 50 else ''}")
            except KeyboardInterrupt:
                _trace("KeyboardInterrupt received, continue")
                continue

            if not user_input.strip():
                _trace("Empty input, skip")
                continue

            # Handle slash commands
            if user_input.startswith("/"):
                _trace(f"Slash command detected: {user_input}")
                if self._handle_slash_command(user_input):
                    _trace("Slash command handled, continue loop")
                    continue
                # Unknown command, continue to LLM

            _trace("Calling _process_user_message()")
            # Process user message
            self._process_user_message(user_input)

    def _call_llm_with_retry(self, messages, max_retries=3):
        """Call LLM with retry for transient errors (429, 500)."""
        import time
        for attempt in range(max_retries):
            try:
                response = self.llm.chat(messages)
                if response.content or response.tool_calls:
                    return response
                # Empty response - retry if not last attempt
                if attempt < max_retries - 1:
                    self.console.print(f"[dim]Empty response, retrying ({attempt + 2}/{max_retries})...[/dim]")
                    time.sleep(2)
                    continue
                return response
            except Exception as e:
                err_str = str(e)
                # Retry on 429 (rate limit) and 500 (server error)
                if attempt < max_retries - 1 and ('429' in err_str or '500' in err_str):
                    wait = 3 * (attempt + 1)
                    self.console.print(f"[dim]API error, retrying in {wait}s ({attempt + 2}/{max_retries})...[/dim]")
                    time.sleep(wait)
                    continue
                raise
        return self.llm.chat(messages)

    def _process_user_message(self, message: str) -> None:
        """Process a user message through the LLM with agentic loop support."""
        _trace(f"_process_user_message() called with: {message[:50]!r}{'...' if len(message) > 50 else ''}")

        # Expand @file references
        expanded_message = self._expand_file_references(message)
        _trace(f"@file references expanded, expanded_message length: {len(expanded_message)}")

        # Add user message to history
        self._append_message("user", expanded_message)
        _trace("User message added to history")

        # Agentic loop: keep calling LLM until it returns no more tool calls
        MAX_TOOL_LOOPS = 10
        loop_count = 0

        while loop_count < MAX_TOOL_LOOPS:
            loop_count += 1
            _trace(f"Agentic loop iteration {loop_count}/{MAX_TOOL_LOOPS}")
            self.console.print(Rule(style="dim"))

            # Build messages from history
            messages = self._build_llm_messages()
            _trace(f"Built {len(messages)} messages for LLM")

            try:
                with self.console.status(
                    "[dim]Thinking...[/dim]",
                    spinner="dots",
                ):
                    response = self._call_llm_with_retry(messages)
                _trace(f"LLM response received, content length: {len(response.content) if response.content else 0}, tool_calls: {len(response.tool_calls) if response.tool_calls else 0}")
            except Exception as e:
                _trace(f"Exception calling LLM: {e}")
                self.console.print(f"[red]\u2717 Error calling LLM:[/red] {e}")
                return

            # If no tool calls, this is the final response
            if not response.tool_calls:
                _trace("No tool calls in response, this is final")
                if response.content:
                    _trace(f"Displaying markdown response, length: {len(response.content)}")
                    self._display_markdown(response.content)
                    self._append_message("assistant", response.content)
                else:
                    _trace("Empty response from AI")
                    self.console.print("[yellow]AI returned empty response. The API may be unstable, please try again.[/yellow]")
                break

            # Execute tools and continue loop
            _trace(f"Executing {len(response.tool_calls)} tool call(s)")
            tool_results = self._execute_tools(response.content, response.tool_calls)

            # If user cancelled, stop the loop
            if tool_results.cancelled:
                _trace("User cancelled, breaking loop")
                break

        _trace(f"_process_user_message() finished after {loop_count} iteration(s)")

    def _append_message(
        self,
        role: str,
        content: str,
        tool_call_id: Optional[str] = None,
    ) -> None:
        """Append to in-memory context AND persist to this session's jsonl."""
        msg: Dict[str, Any] = {"role": role, "content": content}
        if tool_call_id:
            msg["tool_call_id"] = tool_call_id
        self.messages.append(msg)
        self.history.add_message(role, content, tool_call_id=tool_call_id)

    def _build_llm_messages(self) -> List[Dict[str, str]]:
        """LLM messages = CLAUDE.md system prompt + in-memory session context.

        New launches start with an empty context; /resume fills it from a
        previous session file. No automatic cross-session replay — that
        behavior belongs to CLAUDE.md, not session history.
        """
        messages: List[Dict[str, str]] = []
        system_prompt = build_system_prompt()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(self.messages)
        return messages

    def _expand_file_references(self, message: str) -> str:
        """Expand @filename references with file contents."""
        import re

        # Match @filename patterns (alphanumeric, underscore, hyphen, dot, slash)
        pattern = r'@([\w./\\-]+)'

        def replace_match(match):
            filename = match.group(1)
            try:
                # Strip leading slashes to avoid Windows drive-root resolution
                # e.g. Path.cwd() / "/foo" → "C:\foo" on Windows
                filename = filename.lstrip("/\\")
                file_path = Path(filename)
                if not file_path.is_absolute():
                    file_path = Path.cwd() / file_path

                # Security check - ensure within project
                file_path = file_path.resolve()
                try:
                    file_path.relative_to(Path.cwd().resolve())
                except ValueError:
                    return f"@[{filename} - access denied]"

                if file_path.exists() and file_path.is_file():
                    content = file_path.read_text(encoding="utf-8")
                    # Truncate very long files
                    if len(content) > 5000:
                        content = content[:5000] + f"\n... [truncated, {len(content)-5000} more chars]"
                    return f"@file: {filename}\n```\n{content}\n```"
                else:
                    return f"@[{filename} - file not found]"
            except Exception as e:
                return f"@[{filename} - error: {e}]"

        return re.sub(pattern, replace_match, message)

    @dataclass
    class ToolExecutionResult:
        """Result of executing tools, returned to the agentic loop."""
        cancelled: bool = False  # True if user rejected the operation

    def _execute_tools(self, content: str, tool_calls: List[ToolCall]) -> ToolExecutionResult:
        """Execute tool calls from the LLM and add results to history.

        Returns:
            ToolExecutionResult indicating if the user cancelled.
        """
        _trace(f"_execute_tools() called with {len(tool_calls)} tool_call(s)")

        # If there's content, display it first
        if content:
            self._display_markdown(content)

        for tool_call in tool_calls:
            tool_name = tool_call.name
            args = tool_call.arguments
            _trace(f"Executing tool: {tool_name}, args keys: {list(args.keys())}")

            self.console.print(f"[dim]\u2699 Calling tool: [cyan]{tool_name}[/cyan]...[/dim]")

            try:
                if tool_name == "read_file":
                    result = self.read_tool.read_file(args["file_path"])
                    # Ensure message field exists for reporting
                    if "message" not in result:
                        result["message"] = result.get("content", "")[:500]
                elif tool_name == "create_file":
                    # Check if user intent is to edit existing file
                    file_path = args.get("file_path", "")
                    target_path = Path(file_path) if Path(file_path).is_absolute() else Path.cwd() / file_path

                    if target_path.exists() and args.get("content"):
                        # File exists but user wants to modify content
                        # Must read first, then edit
                        self.read_tool.read_file(file_path)
                        result = self.edit_tool.edit_file(
                            file_path,
                            old_string=self.read_tool.get_read_content(file_path),
                            new_string=args["content"]
                        )
                    else:
                        result = self.write_tool.create_file(args["file_path"], args["content"])
                elif tool_name == "edit_file":
                    result = self.edit_tool.edit_file(
                        args["file_path"],
                        old_string=args.get("old_string"),
                        new_string=args.get("new_string"),
                        replace_all=args.get("replace_all", False)
                    )
                elif tool_name == "grep":
                    grep_result = self.grep_tool.search(
                        pattern=args["pattern"],
                        path=args.get("path"),
                        glob=args.get("glob"),
                        output_mode=args.get("output_mode", "files_with_matches"),
                        case_insensitive=args.get("case_insensitive", False),
                        head_limit=args.get("head_limit"),
                    )
                    # Convert GrepResult to dict with message
                    if grep_result.content:
                        message = grep_result.content
                    else:
                        message = f"Found {grep_result.num_files} file(s): {', '.join(grep_result.filenames[:10])}"
                        if grep_result.num_files > 10:
                            message += f" ... and {grep_result.num_files - 10} more"
                    result = {
                        "success": True,
                        "message": message,
                        "content": grep_result.content or "\n".join(grep_result.filenames),
                        "num_files": grep_result.num_files,
                        "filenames": grep_result.filenames,
                    }
                else:
                    result = {"success": False, "message": f"Unknown tool: {tool_name}"}

                self._report_tool_result(tool_call.id, result)

            except PathSecurityError as e:
                self.console.print(f"[red]\u2717 Security Error:[/red] {e}")
                self._append_message("tool", f"Error: {e}", tool_call_id=tool_call.id)
            except FileToolError as e:
                self.console.print(f"[red]\u2717 File Error:[/red] {e}")
                self._append_message("tool", f"Error: {e}", tool_call_id=tool_call.id)
            except Exception as e:
                self.console.print(f"[red]\u2717 Error:[/red] {e}")
                self._append_message("tool", f"Error: {e}", tool_call_id=tool_call.id)

        # Check if last operation was cancelled (flag set by _report_tool_result)
        return self.ToolExecutionResult(cancelled=self._last_tool_cancelled)

    def _report_tool_result(self, tool_call_id: str, result: Dict[str, Any]) -> None:
        """Report the result of a tool execution."""
        _trace(f"_report_tool_result() called, success={result.get('success')}, needs_confirmation={result.get('needs_confirmation')}")
        self._last_tool_cancelled = False  # Reset on each tool

        if result.get("needs_confirmation"):
            # Show diff and request confirmation
            # _request_confirmation handles history logging internally
            _trace("Tool needs confirmation, calling _request_confirmation()")
            self._request_confirmation(result, tool_call_id)
            return

        # Report success/failure
        if result["success"]:
            _trace(f"Tool succeeded: {result['message'][:80]}")
            self.console.print(f"[green]\u2713[/green] {result['message']}")
        else:
            _trace(f"Tool failed: {result['message'][:80]}")
            self.console.print(f"[red]\u2717[/red] {result['message']}")

        self._append_message(
            "tool",
            result["message"],
            tool_call_id=tool_call_id,
        )

    def _request_confirmation(self, result: Dict[str, Any], tool_call_id: str) -> None:
        """Display diff and request user confirmation."""
        self._pending_confirmation = True

        # Display diff with syntax highlighting
        if "diff" in result:
            diff = result["diff"]
            if diff:
                syntax = Syntax(diff, "diff", theme="monokai", line_numbers=True)
                self.console.print(Panel(
                    syntax,
                    title="[bold]Proposed changes[/bold]",
                    border_style="yellow",
                    padding=(0, 1),
                ))

        self.console.print()

        from prompt_toolkit import PromptSession
        from prompt_toolkit.keys import Keys

        session = PromptSession(key_bindings=self._kb)

        try:
            confirm = session.prompt(
                "Do you want to proceed? [y/N] ",
                key_bindings=self._kb
            )
        except KeyboardInterrupt:
            confirm = "n"

        self._pending_confirmation = False

        if confirm.lower() == "y":
            # Determine which confirmation to execute
            file_path = result.get("file_path", "")

            if "content" in result and "diff" not in result:
                # This was a create operation
                create_result = self.write_tool.confirm_create(
                    file_path,
                    result.get("content", "")
                )
                if create_result["success"]:
                    self.console.print(f"[green]\u2713[/green] {create_result['message']}")
                else:
                    self.console.print(f"[red]\u2717[/red] {create_result['message']}")
                self._append_message("tool", create_result["message"], tool_call_id=tool_call_id)
            else:
                # This was an edit operation
                edit_result = self.edit_tool.confirm_edit()
                if edit_result["success"]:
                    self.console.print(f"[green]\u2713[/green] {edit_result['message']}")
                else:
                    self.console.print(f"[red]\u2717[/red] {edit_result['message']}")
                self._append_message("tool", edit_result["message"], tool_call_id=tool_call_id)
        else:
            msg = "Operation cancelled."
            self.console.print(f"[yellow]\u2717[/yellow] {msg}")
            self._append_message("tool", msg, tool_call_id=tool_call_id)
            self._last_tool_cancelled = True

    def _display_markdown(self, content: str) -> None:
        """Display markdown content using rich."""
        md = Markdown(content)
        self.console.print(md)


def main():
    """Entry point."""
    cli = ClaudeCLI()
    cli.run()


if __name__ == "__main__":
    main()
