"""Session history management for Claude CLI."""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional


class SessionHistory:
    """Manages conversation history stored in .myai/session.jsonl."""

    def __init__(self, history_file: Optional[Path] = None):
        if history_file is None:
            history_file = Path.cwd() / ".myai" / "session.jsonl"
        self.history_file = history_file
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        """Ensure the history directory exists."""
        self.history_file.parent.mkdir(parents=True, exist_ok=True)

    def add_message(self, role: str, content: str, tool_calls: Optional[List[Dict]] = None, tool_call_id: Optional[str] = None) -> None:
        """Append a message to the history file in JSONL format."""
        message = {
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

    def load_recent(self, n: int = 10) -> List[Dict[str, Any]]:
        """Load the most recent n messages from history."""
        if not self.history_file.exists():
            return []

        messages = []
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # Get last n lines
            for line in lines[-n:]:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except (IOError, OSError):
            pass

        return messages

    def clear(self) -> None:
        """Clear all history."""
        if self.history_file.exists():
            self.history_file.unlink()

    def get_summary(self) -> str:
        """Return a summary string of the conversation history."""
        messages = self.load_recent(n=20)

        if not messages:
            return "No conversation history."

        summary_lines = ["Conversation Summary:"]
        for i, msg in enumerate(messages, 1):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            # Truncate long content
            if len(content) > 100:
                content = content[:97] + "..."

            tool_calls = msg.get("tool_calls")
            if tool_calls:
                tool_names = [tc.get("function", {}).get("name", "unknown") for tc in tool_calls]
                summary_lines.append(f"{i}. [tool] {role}: {', '.join(tool_names)}")
            else:
                summary_lines.append(f"{i}. {role}: {content}")

        return "\n".join(summary_lines)
