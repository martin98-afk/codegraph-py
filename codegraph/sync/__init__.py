"""
CodeGraph File Synchronization

File watcher for auto-syncing the graph on file changes.
Uses native OS events (watchfiles) with polling fallback.
"""

from __future__ import annotations

import os
import time
import threading
from typing import Callable, List, Optional, Set, Dict, Any, Tuple
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PendingFile:
    """A file with pending changes."""
    path: str
    modified_at: float
    change_type: str  # 'added', 'modified', 'removed'


@dataclass
class WatchOptions:
    """Options for the file watcher."""
    debounce_ms: int = 2000
    ignore_patterns: Optional[List[str]] = None


class LockUnavailableError(Exception):
    """Raised when a lock cannot be acquired."""
    pass


# ── Source file extensions to watch ──
SOURCE_EXTENSIONS = {
    '.py', '.pyi', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.mts', '.cts',
    '.go', '.rs', '.java', '.kt', '.kts', '.c', '.h', '.cpp', '.cc', '.cxx',
    '.hpp', '.hxx', '.cs', '.php', '.rb', '.swift', '.dart', '.svelte', '.vue',
    '.astro', '.lua', '.luau', '.scala', '.r', '.R', '.sol', '.nix', '.tf',
    '.tfvars', '.yaml', '.yml', '.xml', '.properties',
    '.cbl', '.cob', '.vb', '.erl', '.cshtml', '.razor', '.pas', '.pp',
    '.liquid', '.m', '.mm',
}

# Directories to never watch
IGNORE_DIRS = {
    '.codegraph', '.git', '.svn', '.hg', '__pycache__', 'node_modules',
    'venv', '.venv', 'vendor', 'dist', 'build', 'target', '.next',
    '.nuxt', '.output', '.cache', 'Pods', '.build', 'out', 'bin', 'obj',
    'env', '.env', '.tox', '.eggs', 'eggs', '.mypy_cache', '.pytest_cache',
}


def _is_source_file(path_str: str) -> bool:
    """Check if a path is a source file we should watch."""
    ext = Path(path_str).suffix.lower()
    return ext in SOURCE_EXTENSIONS


def _should_ignore_dir(dirname: str) -> bool:
    """Check if a directory should be ignored."""
    return (dirname.startswith('.') or dirname in IGNORE_DIRS)


class FileWatcher:
    """Watches a project directory for file changes.

    Uses native OS events via watchfiles (optional dependency).
    Falls back to polling if watchfiles is not available.
    """

    def __init__(self, project_root: str,
                 on_change: Optional[Callable[[List[PendingFile]], None]] = None,
                 options: Optional[WatchOptions] = None):
        self._project_root = project_root
        self._on_change = on_change
        self._options = options or WatchOptions()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._known_files: Dict[str, float] = {}
        self._timer: Optional[threading.Timer] = None
        self._pending: List[PendingFile] = []
        self._use_native = False

        # Try to use native watcher
        try:
            import watchfiles
            self._watchfiles = watchfiles
            self._use_native = True
        except ImportError:
            self._use_native = False

    def start(self) -> None:
        """Start watching for file changes."""
        if self._running:
            return
        self._running = True

        # Snapshot current state
        self._scan_files()

        if self._use_native:
            self._thread = threading.Thread(
                target=self._native_watch_loop, daemon=True
            )
        else:
            self._thread = threading.Thread(
                target=self._polling_watch_loop, daemon=True
            )
        self._thread.start()

    def stop(self) -> None:
        """Stop watching."""
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _scan_files(self) -> None:
        """Scan project files and record their mtimes."""
        self._known_files.clear()
        for root, dirs, files in os.walk(self._project_root):
            # Skip hidden and common ignore directories
            dirs[:] = [d for d in dirs if not _should_ignore_dir(d)]

            for f in files:
                if f.startswith('.'):
                    continue
                if not _is_source_file(f):
                    continue
                filepath = os.path.join(root, f)
                try:
                    mtime = os.path.getmtime(filepath)
                    relpath = os.path.relpath(filepath, self._project_root)
                    self._known_files[relpath.replace('\\', '/')] = mtime
                except OSError:
                    pass

    # ── Native OS event watching (watchfiles) ──

    def _native_watch_loop(self) -> None:
        """Watch loop using native OS events (watchfiles)."""
        import watchfiles

        # Build watch filter: only source files, exclude ignore dirs
        def _filter(change: watchfiles.Change, path_str: str) -> bool:
            rel = os.path.relpath(path_str, self._project_root).replace('\\', '/')
            # Skip hidden files/dirs and ignored dirs
            parts = rel.split('/')
            for part in parts[:-1]:  # check directory parts
                if _should_ignore_dir(part):
                    return False
            return _is_source_file(path_str) and not os.path.basename(path_str).startswith('.')

        try:
            for changes in watchfiles.watch(
                self._project_root,
                watch_filter=_filter,
                debounce=self._options.debounce_ms,
                recursive=True,
                raise_interrupt=False,
            ):
                if not self._running:
                    break

                pending: List[PendingFile] = []
                now = time.time()

                for change, path_str in changes:
                    rel = os.path.relpath(path_str, self._project_root).replace('\\', '/')
                    change_type_map = {
                        watchfiles.Change.added: 'added',
                        watchfiles.Change.modified: 'modified',
                        watchfiles.Change.deleted: 'removed',
                    }
                    ctype = change_type_map.get(change, 'modified')
                    pending.append(PendingFile(rel, now, ctype))

                if pending:
                    self._fire_callback(pending)

        except Exception:
            # Fall back to polling on error
            self._use_native = False
            if self._running:
                self._polling_watch_loop()

    # ── Polling fallback ──

    def _polling_watch_loop(self) -> None:
        """Main watch loop - polls for file changes."""
        while self._running:
            try:
                self._check_changes()
            except Exception:
                pass
            time.sleep(1.0)  # Poll every second

    def _check_changes(self) -> None:
        """Check for file changes and trigger debounced callback."""
        current: Dict[str, float] = {}
        pending: List[PendingFile] = []

        for root, dirs, files in os.walk(self._project_root):
            dirs[:] = [d for d in dirs if not _should_ignore_dir(d)]

            for f in files:
                if f.startswith('.'):
                    continue
                if not _is_source_file(f):
                    continue
                filepath = os.path.join(root, f)
                try:
                    mtime = os.path.getmtime(filepath)
                    relpath = os.path.relpath(filepath, self._project_root)
                    normalized = relpath.replace('\\', '/')
                    current[normalized] = mtime
                except OSError:
                    pass

        # Check for added/modified
        for path, mtime in current.items():
            if path not in self._known_files:
                pending.append(PendingFile(path, mtime, 'added'))
            elif self._known_files[path] != mtime:
                pending.append(PendingFile(path, mtime, 'modified'))

        # Check for removed
        for path in self._known_files:
            if path not in current:
                pending.append(PendingFile(path, time.time(), 'removed'))

        if pending:
            self._known_files = current
            self._debounce_callback(pending)

    def _debounce_callback(self, pending: List[PendingFile]) -> None:
        """Debounce and fire change callback."""
        if self._timer:
            self._timer.cancel()

        self._pending.extend(pending)

        self._timer = threading.Timer(
            self._options.debounce_ms / 1000.0,
            self._fire_callback
        )
        self._timer.daemon = True
        self._timer.start()

    def _fire_callback(self, pending: Optional[List[PendingFile]] = None) -> None:
        """Fire the change callback with pending files."""
        files = pending if pending is not None else self._pending
        if files and self._on_change:
            try:
                self._on_change(files)
            except Exception:
                pass
        if pending is None:
            self._pending = []


class LockFile:
    """Simple file-based lock for cross-process synchronization."""

    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self._fd = None

    def acquire(self, timeout: float = 5.0) -> bool:
        """Acquire the lock."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self._fd = os.open(
                    self.lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_RDWR,
                    0o644
                )
                os.write(self._fd, str(os.getpid()).encode())
                return True
            except OSError:
                time.sleep(0.1)
        return False

    def release(self) -> None:
        """Release the lock."""
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        try:
            os.unlink(self.lock_path)
        except OSError:
            pass

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()
