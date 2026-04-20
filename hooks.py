"""User-defined shell hooks for tool boundaries.

Hooks are loaded from .myai/hooks.json at agent startup. Each hook declares:
- event: "pre_tool_use" or "post_tool_use"
- match: regex on the tool name (e.g. "edit_file|create_file" or ".*")
- command: shell command to run

The agent runs all matching hooks serially at each boundary. Context
(tool name + tool arguments) is piped to the hook as a JSON object on
stdin — this avoids shell-escaping the LLM's arbitrary arguments.

A non-zero exit from a pre_tool_use hook BLOCKS the tool and its stderr
becomes the tool result reported back to the LLM. post_tool_use hooks
cannot block; their stdout is surfaced to the user only.

Example .myai/hooks.json:
    {
      "pre_tool_use": [
        {"match": "edit_file|create_file",
         "command": "python scripts/forbid_env.py"}
      ],
      "post_tool_use": [
        {"match": "edit_file",
         "command": "python -c 'import json,sys; d=json.load(sys.stdin); print(\"edited\", d.get(\"file_path\"))'"}
      ]
    }
"""

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


HOOKS_FILE = ".myai/hooks.json"
DEFAULT_TIMEOUT_SEC = 10
EVENTS = ("pre_tool_use", "post_tool_use")


@dataclass
class Hook:
    event: str
    match: re.Pattern
    command: str


@dataclass
class HookOutcome:
    blocked: bool = False
    reason: str = ""
    stdout: str = ""


def load_hooks() -> Dict[str, List[Hook]]:
    """Load hooks from .myai/hooks.json. Returns {} when absent or invalid.

    Invalid files surface via the `_error` key so the caller can warn.
    """
    path = Path.cwd() / HOOKS_FILE
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {"_error": [f"hooks.json invalid: {e}"]}  # type: ignore[dict-item]

    hooks: Dict[str, List[Hook]] = {}
    for event in EVENTS:
        items = raw.get(event, [])
        if not isinstance(items, list):
            continue
        collected: List[Hook] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            command = item.get("command", "")
            if not command:
                continue
            try:
                matcher = re.compile(item.get("match", ".*"))
            except re.error:
                continue
            collected.append(Hook(event=event, match=matcher, command=command))
        if collected:
            hooks[event] = collected
    return hooks


def run_hooks(
    hooks_list: Optional[List[Hook]],
    tool_name: str,
    context: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> HookOutcome:
    """Run each matching hook serially. Return the first blocking outcome.

    - pre_tool_use: a non-zero exit marks `blocked=True`; tool is skipped.
    - post_tool_use: never blocks; collects stdout for display.
    """
    if not hooks_list:
        return HookOutcome()

    combined_stdout: List[str] = []
    payload = json.dumps({"tool_name": tool_name, **{
        k: v for k, v in context.items() if isinstance(v, (str, int, float, bool))
    }})

    for hook in hooks_list:
        if not hook.match.search(tool_name):
            continue
        try:
            result = subprocess.run(
                hook.command,
                shell=True,
                input=payload,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            if hook.event == "pre_tool_use":
                return HookOutcome(
                    blocked=True,
                    reason=f"pre-tool hook timed out after {timeout}s: {hook.command[:60]}",
                )
            continue
        except Exception as e:
            if hook.event == "pre_tool_use":
                return HookOutcome(blocked=True, reason=f"pre-tool hook errored: {e}")
            continue

        if result.stdout:
            combined_stdout.append(result.stdout.rstrip())

        if hook.event == "pre_tool_use" and result.returncode != 0:
            stderr = (result.stderr or result.stdout or "").strip()
            return HookOutcome(
                blocked=True,
                reason=stderr[:300] or f"pre-tool hook exited {result.returncode}",
            )

    return HookOutcome(stdout="\n".join(combined_stdout))
