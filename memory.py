"""Minimal memory system for the Claude CLI.

Loads plain Markdown files from two layers and returns a system-prompt string:
  1. User layer:    ~/.claude/CLAUDE.md   (cross-project, global preferences)
  2. Project layer: ./CLAUDE.md           (repo-specific rules)

Project layer is appended last so the model weights it higher (matches Claude
Code's convention: last-loaded wins).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

MAX_TOTAL_CHARS = 16000


@dataclass
class MemorySource:
    label: str
    path: Path
    chars: int


def _candidate_paths() -> List[tuple[str, Path]]:
    return [
        ("User", Path.home() / ".claude" / "CLAUDE.md"),
        ("Project", Path.cwd() / "CLAUDE.md"),
    ]


def get_memory_sources() -> List[MemorySource]:
    """Return info about memory files that would be loaded (for /memory display)."""
    sources: List[MemorySource] = []
    for label, path in _candidate_paths():
        if path.exists() and path.is_file():
            try:
                size = len(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            sources.append(MemorySource(label=label, path=path, chars=size))
    return sources


def load_memory() -> Optional[str]:
    """Load all memory files and return a single system-prompt string.

    Returns None when no memory files exist so callers can skip injection.
    """
    parts: List[str] = []
    for label, path in _candidate_paths():
        if not (path.exists() and path.is_file()):
            continue
        try:
            body = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if not body:
            continue
        parts.append(f"# {label} memory ({path})\n\n{body}")

    if not parts:
        return None

    merged = "\n\n---\n\n".join(parts)
    if len(merged) > MAX_TOTAL_CHARS:
        merged = merged[:MAX_TOTAL_CHARS] + "\n\n... [truncated — memory exceeded 16KB limit]"
    return merged


def build_system_prompt() -> Optional[str]:
    """Wrap memory content with instructions for the model. None if no memory."""
    memory = load_memory()
    if not memory:
        return None
    return (
        "The following are persistent memories loaded from Markdown files on disk. "
        "Treat them as durable instructions about the user and this project — "
        "they override default behavior unless the current request conflicts with them. "
        "Project memory takes precedence over user memory when they disagree.\n\n"
        f"{memory}"
    )
