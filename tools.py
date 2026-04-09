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
    operations: List[Dict[str, Any]]
    timestamp: float = field(default_factory=time.time)


class FileTools:
    """Handles file operations with security checks and snapshots."""

    SNAPSHOT_DIR = Path(".myai") / "file-history"

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = project_root or Path.cwd()
        self._pending_edit: Optional[PendingEdit] = None

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

    def edit_file(self, file_path: str, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Edit a file with the given operations.

        Operations are applied in order, but internally sorted by start_line
        descending to preserve line numbers.

        Args:
            file_path: Path to the file to edit
            operations: List of operations, each containing:
                - action: 'insert', 'delete', or 'replace'
                - start_line: Starting line number (1-indexed)
                - end_line: Optional end line for delete/replace
                - content: Content for insert/replace

        Returns:
            Dict with 'success', 'message', 'needs_confirmation', and 'diff'.
        """
        try:
            resolved_path = self._validate_path(file_path)
        except PathSecurityError:
            raise

        if not resolved_path.exists():
            raise FileToolError(f"File '{file_path}' does not exist.")

        try:
            with open(resolved_path, "r", encoding="utf-8") as f:
                original_content = f.read()
        except OSError as e:
            raise FileToolError(f"Failed to read file: {e}")

        # Store original for confirmation
        self._pending_edit = PendingEdit(
            file_path=resolved_path,
            original_content=original_content,
            new_content="",  # Will compute after applying operations
            diff="",
            operations=operations,
        )

        # Apply operations to get new content
        new_content = self._apply_operations(original_content, operations)
        self._pending_edit.new_content = new_content

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

    def _apply_operations(self, content: str, operations: List[Dict[str, Any]]) -> str:
        """Apply edit operations to content and return new content."""
        lines = content.splitlines(keepends=True)

        # Sort operations by start_line descending (bottom-up)
        sorted_ops = sorted(operations, key=lambda op: op["start_line"], reverse=True)

        for op in sorted_ops:
            action = op["action"]
            start_line = op["start_line"]  # 1-indexed
            end_line = op.get("end_line", start_line)
            insert_content = op.get("content", "")

            # Convert to 0-indexed
            start_idx = start_line - 1
            end_idx = end_line  # end_line is inclusive, so no -1 needed for slice

            if action == "insert":
                # Insert content at start_line (before line start_line)
                if start_idx <= len(lines):
                    lines.insert(start_idx, insert_content + "\n" if not insert_content.endswith("\n") else insert_content)

            elif action == "delete":
                # Delete lines from start_line to end_line (inclusive)
                del lines[start_idx:end_idx]

            elif action == "replace":
                # Replace lines from start_line to end_line with new content
                del lines[start_idx:end_idx]
                insert_text = insert_content + "\n" if insert_content and not insert_content.endswith("\n") else insert_content
                lines.insert(start_idx, insert_text)

        return "".join(lines)

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
