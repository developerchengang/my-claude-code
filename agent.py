"""Agent loop: LLM + tools + in-memory context.

Owns everything that talks to the LLM or the filesystem tools. UI concerns
(banner, slash commands, confirmation dialogs) live in the CLI layer and
reach the agent through a confirmation callback.
"""

import re
import sys
import time
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from rich.console import Console
from rich.markdown import Markdown
from rich.rule import Rule

from config import Config
from history import SessionHistory, list_sessions
from hooks import load_hooks, run_hooks
from llm import LLMClient, LLMResponse, ToolCall
from memory import build_system_prompt
from models import get_context_window
from tools import (
    FileEditTool,
    FileReadTool,
    FileToolError,
    FileWriteTool,
    GrepTool,
    PathSecurityError,
)


_DEBUG = os.environ.get("MYAI_DEBUG") == "1"


def _trace(msg: str) -> None:
    if _DEBUG:
        print(f"[TRACE] {msg}", file=sys.stderr)


# Callback signature: given a tool result dict (with `diff` or `content`),
# render it and ask the user y/N. Returns True on approve.
ConfirmCallback = Callable[[Dict[str, Any]], bool]


class Agent:
    """Single-turn agent: one user message → tool loop → final response."""

    MAX_TOOL_LOOPS = 10
    AUTO_COMPACT_THRESHOLD = 0.8
    KEEP_RECENT_ON_COMPACT = 2
    MAX_FILE_INLINE_CHARS = 5000

    # Tools that mutate the filesystem; blocked in plan mode or for sub-agents.
    WRITE_TOOLS = frozenset({"create_file", "edit_file"})
    # Tools hidden from sub-agents (so they cannot recursively spawn).
    SUBAGENT_DISABLED_TOOLS = frozenset({"task"})

    PLAN_MODE_SYSTEM = (
        "You are in PLAN MODE. Do NOT modify any files. "
        "Use only read_file and grep to inspect the codebase. "
        "Produce a concrete, step-by-step plan listing exact file paths and the "
        "changes you intend to make, then stop and wait for the user to exit "
        "plan mode. If the user asks to proceed without exiting, remind them to "
        "run /plan to leave plan mode."
    )

    def __init__(
        self,
        llm: LLMClient,
        config: Config,
        console: Console,
        confirm_callback: ConfirmCallback,
        readonly: bool = False,
        persist_history: bool = True,
        can_spawn_subagents: bool = True,
    ):
        self.llm = llm
        self.config = config
        self.console = console
        self._confirm = confirm_callback
        self.readonly = readonly
        self.can_spawn = can_spawn_subagents

        self.read_tool = FileReadTool()
        self.write_tool = FileWriteTool()
        self.edit_tool = FileEditTool(read_tool=self.read_tool)
        self.grep_tool = GrepTool()

        self.history = SessionHistory(persist=persist_history)
        self.messages: List[Dict[str, Any]] = []
        self.last_input_tokens = 0
        self.last_output_tokens = 0
        self.plan_mode = False
        self.hooks = load_hooks()
        self._warn_hook_errors()

    # ---- public API ---------------------------------------------------

    def process(self, user_message: str) -> None:
        """Run one user turn: expand @files, enter tool loop, print final reply."""
        expanded = self._expand_file_references(user_message)
        self._append_message("user", expanded)
        self._maybe_auto_compact()

        for i in range(self.MAX_TOOL_LOOPS):
            _trace(f"agent loop iteration {i + 1}/{self.MAX_TOOL_LOOPS}")
            self.console.print(Rule(style="dim"))

            messages = self._build_llm_messages()

            try:
                with self.console.status("[dim]Thinking...[/dim]", spinner="dots"):
                    response = self._call_llm_with_retry(messages)
            except Exception as e:
                self.console.print(f"[red]\u2717 Error calling LLM:[/red] {e}")
                return

            if not response.tool_calls:
                if response.content:
                    self._display_markdown(response.content)
                    self._append_message("assistant", response.content)
                else:
                    self.console.print(
                        "[yellow]AI returned empty response. "
                        "The API may be unstable, please try again.[/yellow]"
                    )
                return

            cancelled = self._execute_tools(response.content, response.tool_calls)
            if cancelled:
                return

    def compact(self) -> None:
        """Summarize history via the LLM and keep only a short tail verbatim.

        Tool messages are dropped from the tail because they would dangle
        without their preceding tool_calls; the tail retains user/assistant.
        """
        if len(self.messages) < 3:
            self.console.print("[dim]Nothing meaningful to compact.[/dim]")
            return

        summary_request = list(self.messages) + [{
            "role": "user",
            "content": (
                "Summarize the conversation above for use as a replacement context. "
                "Be specific about: the user's goal, decisions made, files touched "
                "(with paths), code changes applied, and any open questions. "
                "Write in the same language as the conversation. Do not invent details."
            ),
        }]

        try:
            with self.console.status("[dim]Compacting conversation...[/dim]", spinner="dots"):
                response = self.llm.chat(summary_request, tools=False)
            summary = response.content.strip()
        except Exception as e:
            self.console.print(f"[red]\u2717 Compact failed:[/red] {e}")
            return

        if not summary:
            self.console.print(
                "[red]\u2717 LLM returned empty summary; keeping history as-is.[/red]"
            )
            return

        recent: List[Dict[str, Any]] = []
        for m in reversed(self.messages):
            if len(recent) >= self.KEEP_RECENT_ON_COMPACT:
                break
            if m.get("role") in ("user", "assistant"):
                recent.insert(0, m)

        old_count = len(self.messages)
        self.messages = [
            {"role": "system", "content": f"[Prior conversation summary]\n{summary}"}
        ] + recent
        # Prevent immediate re-triggering: the summarization call's input_tokens
        # still reflect the pre-compact size. Force a fresh measurement.
        self.last_input_tokens = 0

        self.console.print(
            f"[green]\u2713[/green] Compacted {old_count} messages into "
            f"summary + {len(recent)} recent message(s)."
        )

    def clear(self) -> None:
        """Reset in-memory context and delete this session's jsonl."""
        self.messages = []
        self.history.clear()

    def resume_latest(self) -> Tuple[Optional[Path], int]:
        """Load the most recent previous session into context.

        Returns (resumed_path, messages_loaded). Path is None if nothing found.
        """
        previous = list_sessions(limit=5, exclude=self.history.history_file)
        if not previous:
            return None, 0

        target = previous[0]
        loaded = self.history.resume_from(target)

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
        return target, len(self.messages)

    def undo_last_edit(self) -> Dict[str, Any]:
        return self.edit_tool.undo_last()

    def _warn_hook_errors(self) -> None:
        errors = self.hooks.pop("_error", None)
        if errors:
            for e in errors:
                self.console.print(f"[yellow]\u26a0 {e}[/yellow]")

    # ---- LLM plumbing -------------------------------------------------

    def _call_llm_with_retry(self, messages, max_retries: int = 3) -> LLMResponse:
        disabled = self.SUBAGENT_DISABLED_TOOLS if not self.can_spawn else None
        for attempt in range(max_retries):
            try:
                response = self.llm.chat(messages, disabled_tools=disabled)
                if response.content or response.tool_calls:
                    self._record_usage(response)
                    return response
                if attempt < max_retries - 1:
                    self.console.print(
                        f"[dim]Empty response, retrying ({attempt + 2}/{max_retries})...[/dim]"
                    )
                    time.sleep(2)
                    continue
                self._record_usage(response)
                return response
            except Exception as e:
                err = str(e)
                if attempt < max_retries - 1 and ("429" in err or "500" in err):
                    wait = 3 * (attempt + 1)
                    self.console.print(
                        f"[dim]API error, retrying in {wait}s ({attempt + 2}/{max_retries})...[/dim]"
                    )
                    time.sleep(wait)
                    continue
                raise
        final = self.llm.chat(messages, disabled_tools=disabled)
        self._record_usage(final)
        return final

    def _record_usage(self, response: LLMResponse) -> None:
        if response.input_tokens:
            self.last_input_tokens = response.input_tokens
        if response.output_tokens:
            self.last_output_tokens = response.output_tokens

    def _build_llm_messages(self) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = []
        system_prompt = build_system_prompt()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if self.plan_mode:
            messages.append({"role": "system", "content": self.PLAN_MODE_SYSTEM})
        messages.extend(self.messages)
        return messages

    def _append_message(
        self,
        role: str,
        content: str,
        tool_call_id: Optional[str] = None,
    ) -> None:
        msg: Dict[str, Any] = {"role": role, "content": content}
        if tool_call_id:
            msg["tool_call_id"] = tool_call_id
        self.messages.append(msg)
        self.history.add_message(role, content, tool_call_id=tool_call_id)

    def _maybe_auto_compact(self) -> None:
        window = get_context_window(self.config.model)
        if not window or not self.last_input_tokens:
            return
        if self.last_input_tokens / window >= self.AUTO_COMPACT_THRESHOLD:
            pct = self.last_input_tokens / window * 100
            self.console.print(
                f"[yellow]\u26a0 Context at {pct:.0f}% of window — auto-compacting...[/yellow]"
            )
            self.compact()

    # ---- @file expansion ----------------------------------------------

    def _expand_file_references(self, message: str) -> str:
        pattern = r"@([\w./\\-]+)"

        def replace(match):
            filename = match.group(1).lstrip("/\\")
            try:
                file_path = Path(filename)
                if not file_path.is_absolute():
                    file_path = Path.cwd() / file_path
                file_path = file_path.resolve()
                try:
                    file_path.relative_to(Path.cwd().resolve())
                except ValueError:
                    return f"@[{filename} - access denied]"

                if not (file_path.exists() and file_path.is_file()):
                    return f"@[{filename} - file not found]"

                content = file_path.read_text(encoding="utf-8")
                if len(content) > self.MAX_FILE_INLINE_CHARS:
                    overflow = len(content) - self.MAX_FILE_INLINE_CHARS
                    content = (
                        content[:self.MAX_FILE_INLINE_CHARS]
                        + f"\n... [truncated, {overflow} more chars]"
                    )
                return f"@file: {filename}\n```\n{content}\n```"
            except Exception as e:
                return f"@[{filename} - error: {e}]"

        return re.sub(pattern, replace, message)

    # ---- tool execution ----------------------------------------------

    def _execute_tools(self, leading_text: str, tool_calls: List[ToolCall]) -> bool:
        """Dispatch each tool call, report result. Returns True if user cancelled."""
        if leading_text:
            self._display_markdown(leading_text)

        cancelled = False
        for tool_call in tool_calls:
            self.console.print(
                f"[dim]\u2699 Calling tool: [cyan]{tool_call.name}[/cyan]...[/dim]"
            )

            pre = run_hooks(
                self.hooks.get("pre_tool_use"),
                tool_call.name,
                tool_call.arguments,
            )
            if pre.blocked:
                self.console.print(f"[red]\u2717 Hook blocked:[/red] {pre.reason}")
                self._append_message(
                    "tool",
                    f"Blocked by pre_tool_use hook: {pre.reason}",
                    tool_call_id=tool_call.id,
                )
                continue

            try:
                result = self._dispatch_tool(tool_call)
            except PathSecurityError as e:
                self.console.print(f"[red]\u2717 Security Error:[/red] {e}")
                self._append_message("tool", f"Error: {e}", tool_call_id=tool_call.id)
                continue
            except FileToolError as e:
                self.console.print(f"[red]\u2717 File Error:[/red] {e}")
                self._append_message("tool", f"Error: {e}", tool_call_id=tool_call.id)
                continue
            except Exception as e:
                self.console.print(f"[red]\u2717 Error:[/red] {e}")
                self._append_message("tool", f"Error: {e}", tool_call_id=tool_call.id)
                continue

            if self._handle_tool_result(tool_call.id, result):
                cancelled = True

            post = run_hooks(
                self.hooks.get("post_tool_use"),
                tool_call.name,
                tool_call.arguments,
            )
            if post.stdout:
                self.console.print(f"[dim]\u2192 hook: {post.stdout}[/dim]")

        return cancelled

    def _dispatch_tool(self, tool_call: ToolCall) -> Dict[str, Any]:
        name = tool_call.name
        args = tool_call.arguments

        if (self.plan_mode or self.readonly) and name in self.WRITE_TOOLS:
            reason = "plan mode" if self.plan_mode else "read-only sub-agent context"
            return {
                "success": False,
                "message": f"Tool '{name}' is disabled ({reason}).",
            }

        if name == "task":
            return self._run_subagent(args.get("description", ""))

        if name == "read_file":
            result = self.read_tool.read_file(args["file_path"])
            if "message" not in result:
                result["message"] = result.get("content", "")[:500]
            return result

        if name == "create_file":
            file_path = args.get("file_path", "")
            target = Path(file_path) if Path(file_path).is_absolute() else Path.cwd() / file_path
            # create_file on an existing file → translate to a full edit
            # so the write goes through the confirm/diff path.
            if target.exists() and args.get("content"):
                self.read_tool.read_file(file_path)
                return self.edit_tool.edit_file(
                    file_path,
                    old_string=self.read_tool.get_read_content(file_path),
                    new_string=args["content"],
                )
            return self.write_tool.create_file(file_path, args["content"])

        if name == "edit_file":
            return self.edit_tool.edit_file(
                args["file_path"],
                old_string=args.get("old_string"),
                new_string=args.get("new_string"),
                replace_all=args.get("replace_all", False),
            )

        if name == "grep":
            gr = self.grep_tool.search(
                pattern=args["pattern"],
                path=args.get("path"),
                glob=args.get("glob"),
                output_mode=args.get("output_mode", "files_with_matches"),
                case_insensitive=args.get("case_insensitive", False),
                head_limit=args.get("head_limit"),
            )
            if gr.content:
                message = gr.content
            else:
                head = ", ".join(gr.filenames[:10])
                message = f"Found {gr.num_files} file(s): {head}"
                if gr.num_files > 10:
                    message += f" ... and {gr.num_files - 10} more"
            return {
                "success": True,
                "message": message,
                "content": gr.content or "\n".join(gr.filenames),
                "num_files": gr.num_files,
                "filenames": gr.filenames,
            }

        return {"success": False, "message": f"Unknown tool: {name}"}

    def _run_subagent(self, description: str) -> Dict[str, Any]:
        """Spawn an isolated, read-only sub-agent. Return its final report.

        The sub-agent has its own messages list (fresh context), no jsonl
        persistence, no write tools, and no access to the ``task`` tool —
        preventing recursive spawning.
        """
        description = (description or "").strip()
        if not description:
            return {"success": False, "message": "task: description is required."}
        if not self.can_spawn:
            return {
                "success": False,
                "message": "task: sub-agents cannot spawn further sub-agents.",
            }

        preview = description if len(description) <= 80 else description[:77] + "..."
        self.console.print(f"[magenta]\u229b sub-agent:[/magenta] [dim]{preview}[/dim]")

        sub = Agent(
            llm=self.llm,
            config=self.config,
            console=self.console,
            confirm_callback=lambda _r: False,  # sub can't write anyway
            readonly=True,
            persist_history=False,
            can_spawn_subagents=False,
        )
        sub.process(description)

        for m in reversed(sub.messages):
            if m.get("role") == "assistant" and m.get("content"):
                return {
                    "success": True,
                    "message": m["content"],
                }
        return {"success": False, "message": "Sub-agent produced no final report."}

    def _handle_tool_result(self, tool_call_id: str, result: Dict[str, Any]) -> bool:
        """Print feedback, persist message, request confirmation if needed.
        Returns True if user cancelled a confirmation."""
        if result.get("needs_confirmation"):
            approved = self._confirm(result)
            if not approved:
                msg = "Operation cancelled."
                self.console.print(f"[yellow]\u2717[/yellow] {msg}")
                self._append_message("tool", msg, tool_call_id=tool_call_id)
                return True
            final = self._apply_confirmed(result)
            self._print_status(final)
            self._append_message("tool", final["message"], tool_call_id=tool_call_id)
            return False

        self._print_status(result)
        self._append_message("tool", result["message"], tool_call_id=tool_call_id)
        return False

    def _apply_confirmed(self, result: Dict[str, Any]) -> Dict[str, Any]:
        # A create result carries `content` but no `diff`; an edit carries `diff`.
        if "content" in result and "diff" not in result:
            return self.write_tool.confirm_create(
                result.get("file_path", ""),
                result.get("content", ""),
            )
        return self.edit_tool.confirm_edit()

    def _print_status(self, result: Dict[str, Any]) -> None:
        mark = "[green]\u2713[/green]" if result.get("success") else "[red]\u2717[/red]"
        self.console.print(f"{mark} {result['message']}")

    def _display_markdown(self, content: str) -> None:
        self.console.print(Markdown(content))
