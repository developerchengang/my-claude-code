"""Session history management for Claude CLI.

One JSONL file per session under .myai/sessions/. The current session writes
into its own file; `/resume` picks a previous file to continue from.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional


SESSIONS_DIR = ".myai/sessions"


class SessionHistory:
    """Append-only conversation log for the current session.

    When ``persist=False`` the instance is ephemeral — used by sub-agents so
    they don't leave transcript files for each invocation.
    """

    def __init__(self, history_file: Optional[Path] = None, persist: bool = True):
        self.persist = persist
        if not persist:
            self.history_file = None
            return
        if history_file is None:
            # Lazy: file is created on first add_message, so an empty session
            # leaves no artifact on disk.
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            history_file = Path.cwd() / SESSIONS_DIR / f"session-{timestamp}.jsonl"
        self.history_file = history_file
        self.history_file.parent.mkdir(parents=True, exist_ok=True)

    def add_message(
        self,
        role: str,
        content: str,
        tool_calls: Optional[List[Dict]] = None,
        tool_call_id: Optional[str] = None,
    ) -> None:
        if not self.persist:
            return
        message: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "role": role,
            "content": content,
        }
        if tool_calls:
            message["tool_calls"] = tool_calls
        if tool_call_id:
            message["tool_call_id"] = tool_call_id

        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")

    def resume_from(self, path: Path) -> List[Dict[str, Any]]:
        """Switch writes to an existing session file and return its messages."""
        messages = _load_all(path)
        self.history_file = path
        self.persist = True
        return messages

    def clear(self) -> None:
        if self.persist and self.history_file and self.history_file.exists():
            self.history_file.unlink()


def list_sessions(limit: int = 10, exclude: Optional[Path] = None) -> List[Path]:
    """Previous session files, newest first. Excludes the given path if provided."""
    sessions_dir = Path.cwd() / SESSIONS_DIR
    if not sessions_dir.exists():
        return []
    files = sorted(
        sessions_dir.glob("session-*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if exclude is not None and exclude.exists():
        exclude_resolved = exclude.resolve()
        files = [f for f in files if f.resolve() != exclude_resolved]
    return files[:limit]


def preview_session(path: Path) -> str:
    """One-line preview: message count + first user message."""
    messages = _load_all(path)
    first_user = next((m for m in messages if m.get("role") == "user"), None)
    preview = first_user["content"] if first_user else "(empty)"
    preview = preview.replace("\n", " ")
    if len(preview) > 60:
        preview = preview[:57] + "..."
    return f"{len(messages):>3} msgs | {preview}"


def _load_all(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    messages: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return messages
