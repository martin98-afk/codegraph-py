"""
CodeGraph Utilities

Shared utility functions and classes.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Callable, Iterator, List, Optional, TypeVar

T = TypeVar('T')


# =============================================================================
# Hashing
# =============================================================================

def sha256_hash(content: str) -> str:
    """Calculate SHA256 hash of string content."""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def sha256_file(filepath: str) -> str:
    """Calculate SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


# =============================================================================
# Path Utilities
# =============================================================================

def normalize_path(p: str) -> str:
    """Normalize path separators to forward slashes."""
    return p.replace('\\', '/')


def is_subpath(child: str, parent: str) -> bool:
    """Check if child path is under parent path."""
    child = os.path.normpath(os.path.abspath(child))
    parent = os.path.normpath(os.path.abspath(parent))
    if child == parent:
        return True
    # Add trailing separator to parent to avoid partial matches
    parent = parent.rstrip(os.sep) + os.sep
    return child.startswith(parent)


def validate_path_within_root(path: str, root: str) -> bool:
    """Validate that a path is within the project root (no traversal)."""
    abs_path = os.path.normpath(os.path.abspath(path))
    abs_root = os.path.normpath(os.path.abspath(root))
    return abs_path.startswith(abs_root + os.sep) or abs_path == abs_root


# =============================================================================
# Mutex (Async-friendly via threading)
# =============================================================================

class Mutex:
    """A simple mutex for preventing concurrent operations within a process."""

    def __init__(self):
        self._lock = threading.Lock()
        self._owner = None

    def acquire(self, blocking: bool = True) -> bool:
        """Acquire the mutex."""
        return self._lock.acquire(blocking=blocking)

    def release(self) -> None:
        """Release the mutex."""
        if self._lock.locked():
            self._lock.release()

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, *args):
        self._lock.release()

    async def with_lock(self, func: Callable[..., T]) -> T:
        """Execute a function while holding the lock."""
        with self._lock:
            return func()


# =============================================================================
# File Lock (cross-process)
# =============================================================================

class FileLock:
    """Cross-process file lock using a lock file."""

    def __init__(self, lock_path: str, timeout: float = 10.0):
        self.lock_path = lock_path
        self.timeout = timeout
        self._fd = None

    def acquire(self) -> bool:
        """Acquire the file lock. Returns True if successful."""
        import fcntl
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                self._fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except (IOError, OSError):
                if self._fd:
                    os.close(self._fd)
                    self._fd = None
                time.sleep(0.1)
        return False

    def release(self) -> None:
        """Release the file lock."""
        if self._fd is not None:
            try:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except (IOError, OSError):
                pass
            finally:
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


# =============================================================================
# Batch Processing
# =============================================================================

def process_in_batches(items: List[T], batch_size: int) -> Iterator[List[T]]:
    """Yield successive batches from items."""
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]


# =============================================================================
# Debounce / Throttle
# =============================================================================

class Debouncer:
    """Debounce calls to a function."""

    def __init__(self, delay_ms: float = 2000):
        self.delay = delay_ms / 1000.0
        self._timer: Optional[threading.Timer] = None
        self._last_call: float = 0

    def call(self, func: Callable, *args, **kwargs) -> None:
        """Call function after debounce delay."""
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(self.delay, func, args, kwargs)
        self._timer.daemon = True
        self._timer.start()

    def cancel(self) -> None:
        """Cancel pending call."""
        if self._timer:
            self._timer.cancel()
            self._timer = None


# =============================================================================
# Generated File Detection
# =============================================================================

_GENERATED_PATTERNS = [
    '.pb.', '_pb2.py', '_pb2_grpc.py',
    '.grpc.', '.gen.', '_gen.',
    '.generated.', '_generated.',
    'grpc_', 'proto_',
]


def is_generated_file(filepath: str) -> bool:
    """Check if a file appears to be generated."""
    name = os.path.basename(filepath).lower()
    for pattern in _GENERATED_PATTERNS:
        if pattern in name:
            return True
    return False


# =============================================================================
# Config Leaf Languages
# =============================================================================

CONFIG_LEAF_LANGUAGES = frozenset({
    'terraform', 'yaml', 'properties', 'nix', 'solidity',
})


def is_config_leaf_node(node_kind: str, node_language: str) -> bool:
    """Check if a node is a config-leaf (no meaningful children to recurse)."""
    return node_language in CONFIG_LEAF_LANGUAGES
