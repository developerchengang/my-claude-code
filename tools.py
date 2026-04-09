"""File operation tools for Claude CLI."""

import difflib
import hashlib
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Optional, Literal


class PathSecurityError(Exception):
    """Raised when a path traversal attack is detected."""
    pass


class FileToolError(Exception):
    """Raised when a file operation fails."""
    pass


@dataclass
class PendingEdit:
    """Represents a pending edit that requires user confirmation."""
    file_path: Path
    original_content: str
    new_content: str
    diff: str
    old_string: str
    new_string: str
    replace_all: bool
    timestamp: float = field(default_factory=time.time)


class FileReadTool:
    """Tool for reading file contents. Must be called before edit."""

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path.cwd()
        # Track read files for edit validation
        self._read_files: Dict[str, Dict[str, Any]] = {}

    def _validate_path(self, file_path: str) -> Path:
        """Resolve and validate that a path is within the project directory."""
        if not Path(file_path).is_absolute():
            resolved = (self.project_root / file_path).resolve()
        else:
            resolved = Path(file_path).resolve()

        try:
            resolved.relative_to(self.project_root)
        except ValueError:
            raise PathSecurityError(
                f"Access denied: '{file_path}' is outside the project directory."
            )
        return resolved

    def read_file(
        self,
        file_path: str,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Read a file and cache its state for later editing.

        Args:
            file_path: Path to the file to read
            offset: Optional line offset (0-indexed)
            limit: Optional line limit

        Returns:
            Dict with 'success', 'content', 'file_path', etc.
        """
        try:
            resolved_path = self._validate_path(file_path)
        except PathSecurityError:
            raise

        if not resolved_path.exists():
            return {
                "success": False,
                "message": f"File not found: {file_path}",
            }

        try:
            with open(resolved_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            return {
                "success": False,
                "message": f"Failed to read file: {e}",
            }

        # Store file state for edit validation
        str_path = str(resolved_path)
        self._read_files[str_path] = {
            "content": content,
            "timestamp": resolved_path.stat().st_mtime,
        }

        # Apply offset/limit if specified
        lines = content.splitlines(keepends=True)
        if offset is not None:
            lines = lines[offset:]
        if limit is not None:
            lines = lines[:limit]

        return {
            "success": True,
            "content": "".join(lines),
            "file_path": str_path,
            "num_lines": len(content.splitlines()),
            "full_content": content,  # Include full content for reference
        }

    def was_read(self, file_path: str) -> bool:
        """Check if a file has been read."""
        try:
            resolved = self._validate_path(file_path)
        except PathSecurityError:
            return False
        return str(resolved) in self._read_files

    def get_read_content(self, file_path: str) -> Optional[str]:
        """Get the cached content of a read file."""
        try:
            resolved = self._validate_path(file_path)
        except PathSecurityError:
            return None
        entry = self._read_files.get(str(resolved))
        return entry["content"] if entry else None


class FileTools:
    """Handles file operations with security checks and snapshots."""

    SNAPSHOT_DIR = Path(".myai") / "file-history"

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path.cwd()
        self._pending_edit: Optional[PendingEdit] = None
        # Reference to FileReadTool for validating reads (set externally)
        self._read_tool: Optional[FileReadTool] = None

    def _validate_path(self, file_path: str) -> Path:
        """Resolve and validate that a path is within the project directory."""
        # Handle relative paths
        if not Path(file_path).is_absolute():
            resolved = (self.project_root / file_path).resolve()
        else:
            resolved = Path(file_path).resolve()

        # Security check: ensure path is within project root
        try:
            resolved.relative_to(self.project_root)
        except ValueError:
            raise PathSecurityError(
                f"Access denied: '{file_path}' is outside the project directory. "
                f"Only files within '{self.project_root}' are accessible."
            )

        return resolved

    def _get_snapshot_dir(self, file_path: Path) -> Path:
        """Get the snapshot directory for a given file path."""
        # Create a unique identifier from the file path
        path_hash = hashlib.md5(str(file_path).encode()).hexdigest()
        return self.SNAPSHOT_DIR / path_hash

    def _create_snapshot(self, file_path: Path) -> Path:
        """Create a snapshot backup of a file."""
        snapshot_dir = self._get_snapshot_dir(file_path)
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        # Create timestamped filename
        timestamp = int(time.time() * 1000)
        filename = f"{timestamp}_{file_path.name}"
        snapshot_path = snapshot_dir / filename

        # Copy file to snapshot
        shutil.copy2(file_path, snapshot_path)
        return snapshot_path

    def create_file(self, file_path: str, content: str) -> Dict[str, Any]:
        """
        Create a new file with the given content.

        Returns:
            Dict with 'success', 'message', and optionally 'needs_confirmation'.
        """
        try:
            resolved_path = self._validate_path(file_path)
        except PathSecurityError:
            raise

        # Check if file exists
        if resolved_path.exists():
            return {
                "success": False,
                "message": f"File '{file_path}' already exists.",
                "needs_confirmation": True,
                "file_path": str(resolved_path),
            }

        # Create parent directories if needed
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(resolved_path, "w", encoding="utf-8") as f:
                f.write(content)

            return {
                "success": True,
                "message": f"File '{file_path}' created successfully.",
                "file_path": str(resolved_path),
            }
        except OSError as e:
            raise FileToolError(f"Failed to create file: {e}")

    def confirm_create(self, file_path: str, content: str) -> Dict[str, Any]:
        """Execute the file creation after user confirmation."""
        resolved_path = self._validate_path(file_path)

        # Double-check file doesn't exist (might have been created since check)
        if resolved_path.exists():
            return {
                "success": False,
                "message": f"File '{file_path}' already exists. Cannot overwrite.",
            }

        resolved_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with open(resolved_path, "w", encoding="utf-8") as f:
                f.write(content)

            return {
                "success": True,
                "message": f"File '{file_path}' created successfully.",
                "file_path": str(resolved_path),
            }
        except OSError as e:
            raise FileToolError(f"Failed to create file: {e}")

    def edit_file(
        self,
        file_path: str,
        old_string: str = None,
        new_string: str = None,
        replace_all: bool = False,
    ) -> Dict[str, Any]:
        """
        Edit a file by replacing old_string with new_string.

        IMPORTANT: File must be read first using FileReadTool.read_file() before editing.
        This ensures LLM sees the content before making changes.

        Args:
            file_path: Path to the file to edit
            old_string: The exact string to find and replace (REQUIRED)
            new_string: The replacement string (REQUIRED)
            replace_all: If True, replace all occurrences. If False, replace only the first.

        Returns:
            Dict with 'success', 'message', 'needs_confirmation', and 'diff'.
        """
        # Check required parameters
        if old_string is None or new_string is None:
            raise FileToolError(
                f"edit_file requires 'old_string' and 'new_string' arguments. "
                f"You must provide both: edit_file(file_path, old_string='text to replace', new_string='replacement text')"
            )

        try:
            resolved_path = self._validate_path(file_path)
        except PathSecurityError:
            raise

        if not resolved_path.exists():
            raise FileToolError(f"File '{file_path}' does not exist.")

        # Check if file was read first via FileReadTool
        original_content: str
        if self._read_tool and self._read_tool.was_read(file_path):
            cached = self._read_tool.get_read_content(file_path)
            if cached is not None:
                original_content = cached
            else:
                # Fallback to reading file
                with open(resolved_path, "r", encoding="utf-8") as f:
                    original_content = f.read()
        else:
            # File was not read first - require it for Claude Code workflow
            raise FileToolError(
                f"File '{file_path}' has not been read yet. "
                f"Please read the file first using read_file() before editing."
            )

        # Special case: empty old_string means create/replace content in empty file
        if old_string == "":
            if original_content.strip() != "":
                raise FileToolError(
                    f"Cannot use empty old_string - file has content. "
                    f"Use create_file() to create new files."
                )
            new_content = new_string
        else:
            # Find the old_string in content
            if old_string not in original_content:
                raise FileToolError(
                    f"String to replace not found in file: {repr(old_string[:100])}"
                )

            # Check if there are multiple matches
            count = original_content.count(old_string)
            if count > 1 and not replace_all:
                raise FileToolError(
                    f"Found {count} occurrences of the string to replace. "
                    f"Use replace_all=True to replace all occurrences."
                )

            # Perform replacement
            if replace_all:
                new_content = original_content.replace(old_string, new_string)
            else:
                new_content = original_content.replace(old_string, new_string, 1)

        # Store original for confirmation
        self._pending_edit = PendingEdit(
            file_path=resolved_path,
            original_content=original_content,
            new_content=new_content,
            diff="",
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )

        # Generate diff
        diff = self.generate_unified_diff(
            original_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            str(resolved_path)
        )
        self._pending_edit.diff = diff

        return {
            "success": True,
            "message": f"Edit prepared for '{file_path}'. Review the diff below.",
            "needs_confirmation": True,
            "diff": diff,
            "file_path": str(resolved_path),
        }

    def confirm_edit(self) -> Dict[str, Any]:
        """Execute the pending edit after user confirmation."""
        if self._pending_edit is None:
            return {
                "success": False,
                "message": "No pending edit to confirm.",
            }

        pending = self._pending_edit

        # Create snapshot of current file
        if pending.file_path.exists():
            self._create_snapshot(pending.file_path)

        try:
            with open(pending.file_path, "w", encoding="utf-8") as f:
                f.write(pending.new_content)

            result = {
                "success": True,
                "message": f"Edit applied to '{pending.file_path.name}'.",
                "file_path": str(pending.file_path),
            }
            self._pending_edit = None
            return result

        except OSError as e:
            raise FileToolError(f"Failed to write file: {e}")

    def undo_last(self) -> Dict[str, Any]:
        """
        Undo the most recent edit by restoring from the latest snapshot.

        Returns:
            Dict with 'success' and 'message'.
        """
        # Find the latest snapshot directory
        if not self.SNAPSHOT_DIR.exists():
            return {
                "success": False,
                "message": "No snapshots found. Nothing to undo.",
            }

        # Find all snapshot directories
        snapshot_dirs = []
        for item in self.SNAPSHOT_DIR.iterdir():
            if item.is_dir():
                snapshot_dirs.append(item)

        if not snapshot_dirs:
            return {
                "success": False,
                "message": "No snapshots found. Nothing to undo.",
            }

        # Find the most recent snapshot across all directories
        latest_snapshot = None
        latest_time = 0

        for snap_dir in snapshot_dirs:
            for snapshot_file in snap_dir.iterdir():
                if snapshot_file.is_file():
                    file_time = snapshot_file.stat().st_mtime
                    if file_time > latest_time:
                        latest_time = file_time
                        latest_snapshot = snapshot_file

        if not latest_snapshot:
            return {
                "success": False,
                "message": "No snapshots found. Nothing to undo.",
            }

        # Get the original file path from the snapshot filename
        # Format: {timestamp}_{original_filename}
        parts = latest_snapshot.stem.split("_", 1)
        if len(parts) > 1:
            original_filename = parts[1]
        else:
            original_filename = latest_snapshot.name

        # Try to find the original file in project root
        original_path = self.project_root / original_filename

        # If the file still exists, create a snapshot of it first
        if original_path.exists():
            self._create_snapshot(original_path)

        try:
            # Restore from snapshot
            if original_path.parent != latest_snapshot.parent:
                # Copy to original location
                shutil.copy2(latest_snapshot, original_path)
            else:
                # Already in same directory, just restore content
                shutil.copy2(latest_snapshot, original_path)

            return {
                "success": True,
                "message": f"Restored '{original_filename}' from snapshot.",
                "file_path": str(original_path),
            }
        except OSError as e:
            raise FileToolError(f"Failed to restore file: {e}")

    def generate_unified_diff(
        self,
        original_lines: List[str],
        new_lines: List[str],
        file_path: str
    ) -> str:
        """Generate a unified diff string."""
        diff = difflib.unified_diff(
            original_lines,
            new_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm=""
        )
        return "\n".join(diff)

    def get_pending_diff(self) -> Optional[str]:
        """Get the diff for the pending edit, if any."""
        if self._pending_edit is None:
            return None
        return self._pending_edit.diff

    def get_pending_file_path(self) -> Optional[str]:
        """Get the file path for the pending edit, if any."""
        if self._pending_edit is None:
            return None
        return str(self._pending_edit.file_path)


# VCS directories to exclude from searches
VCS_DIRECTORIES_TO_EXCLUDE = {'.git', '.svn', '.hg', '.bzr', '.jj', '.sl'}

# Default limit on grep results
DEFAULT_HEAD_LIMIT = 250


@dataclass
class GrepResult:
    """Result from a grep search."""
    mode: str  # 'content', 'files_with_matches', 'count'
    num_files: int
    filenames: List[str]
    content: Optional[str] = None
    num_matches: Optional[int] = None
    applied_limit: Optional[int] = None
    applied_offset: Optional[int] = None


class GrepTool:
    """Tool for searching file contents using regex patterns."""

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path.cwd()

    def _validate_path(self, path: str) -> Path:
        """Resolve and validate that a path is within the project directory."""
        if not Path(path).is_absolute():
            resolved = (self.project_root / path).resolve()
        else:
            resolved = Path(path).resolve()

        try:
            resolved.relative_to(self.project_root)
        except ValueError:
            raise PathSecurityError(
                f"Access denied: '{path}' is outside the project directory."
            )
        return resolved

    def _should_exclude_dir(self, dir_name: str) -> bool:
        """Check if a directory should be excluded from searches."""
        return dir_name in VCS_DIRECTORIES_TO_EXCLUDE

    def _match_glob(self, filename: str, glob_pattern: str) -> bool:
        """Simple glob pattern matching (supports * and ?)."""
        import fnmatch
        return fnmatch.fnmatch(filename, glob_pattern)

    def search(
        self,
        pattern: str,
        path: Optional[str] = None,
        glob: Optional[str] = None,
        output_mode: str = "files_with_matches",
        context_before: Optional[int] = None,
        context_after: Optional[int] = None,
        context: Optional[int] = None,
        show_line_numbers: bool = True,
        case_insensitive: bool = False,
        head_limit: Optional[int] = None,
        offset: int = 0,
    ) -> GrepResult:
        """
        Search for a pattern in file contents.

        Args:
            pattern: Regular expression pattern to search for
            path: File or directory to search in (defaults to project root)
            glob: Glob pattern to filter files (e.g., "*.py", "*.{ts,tsx}")
            output_mode: 'content', 'files_with_matches', or 'count'
            context_before: Number of lines to show before each match
            context_after: Number of lines to show after each match
            context: Number of lines to show before and after each match
            show_line_numbers: Whether to show line numbers in content mode
            case_insensitive: Case insensitive search
            head_limit: Limit on results (default 250)
            offset: Skip first N results

        Returns:
            GrepResult with matches and metadata
        """
        import re

        search_path = self._validate_path(path or ".")

        if not search_path.exists():
            return GrepResult(
                mode=output_mode,
                num_files=0,
                filenames=[],
                content="No matches found"
            )

        # Compile regex pattern
        try:
            flags = re.IGNORECASE if case_insensitive else 0
            regex = re.compile(pattern, flags)
        except re.error as e:
            raise FileToolError(f"Invalid regex pattern: {e}")

        # Determine effective context
        if context is not None:
            context_before = context
            context_after = context

        # Collect matching lines and files
        matches: List[Dict[str, Any]] = []
        matching_files: set = set()

        effective_limit = head_limit if head_limit is not None else DEFAULT_HEAD_LIMIT

        for file_path in search_path.rglob("*"):
            # Skip directories
            if file_path.is_dir():
                continue

            # Skip VCS directories
            if any(self._should_exclude_dir(p) for p in file_path.parts):
                continue

            # Skip hidden files/directories
            if any(part.startswith('.') for part in file_path.parts):
                continue

            # Apply glob filter
            if glob:
                if not self._match_glob(file_path.name, glob):
                    continue

            # Skip binary files
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except (UnicodeDecodeError, OSError):
                continue

            # Search in file content
            file_matches: List[Dict[str, Any]] = []
            for line_num, line in enumerate(lines, start=1):
                if regex.search(line):
                    file_matches.append({
                        "file": file_path.relative_to(self.project_root),
                        "line_num": line_num,
                        "line": line.rstrip('\n\r')
                    })
                    matching_files.add(str(file_path.relative_to(self.project_root)))

            # Add context lines
            if file_matches and output_mode == "content":
                # Expand to include context
                for match in file_matches:
                    start = max(0, match["line_num"] - 1 - (context_before or 0))
                    end = min(len(lines), match["line_num"] + (context_after or 0))
                    for i in range(start, end):
                        if i + 1 not in [m["line_num"] for m in matches]:
                            matches.append({
                                "file": match["file"],
                                "line_num": i + 1,
                                "line": lines[i].rstrip('\n\r'),
                                "is_context": i + 1 not in [m["line_num"] for m in file_matches]
                            })
                # Sort by file and line number
                matches.sort(key=lambda x: (str(x["file"]), x["line_num"]))
            else:
                matches.extend(file_matches)

        # Apply offset and limit
        total_matches = len(matches)
        matches = matches[offset:offset + effective_limit] if matches else []
        applied_limit = effective_limit if total_matches > offset + effective_limit else None
        applied_offset = offset if offset > 0 else None

        # Format output based on mode
        if output_mode == "content":
            lines_output = []
            for m in matches:
                prefix = f"{m['file']}:{m['line_num']}" if show_line_numbers else str(m['file'])
                lines_output.append(f"{prefix}:{m['line']}")

            return GrepResult(
                mode="content",
                num_files=len(matching_files),
                filenames=list(matching_files),
                content="\n".join(lines_output) if lines_output else "No matches found",
                applied_limit=applied_limit,
                applied_offset=applied_offset,
            )

        elif output_mode == "count":
            # Count matches per file
            file_counts: Dict[str, int] = {}
            for m in matches:
                fname = str(m["file"])
                file_counts[fname] = file_counts.get(fname, 0) + 1

            count_lines = [f"{f}:{c}" for f, c in sorted(file_counts.items())]
            total_count = sum(file_counts.values())

            return GrepResult(
                mode="count",
                num_files=len(file_counts),
                filenames=list(file_counts.keys()),
                content="\n".join(count_lines) if count_lines else "No matches found",
                num_matches=total_count,
                applied_limit=applied_limit,
                applied_offset=applied_offset,
            )

        else:  # files_with_matches
            return GrepResult(
                mode="files_with_matches",
                num_files=len(matching_files),
                filenames=sorted(matching_files),
                applied_limit=applied_limit,
                applied_offset=applied_offset,
            )
