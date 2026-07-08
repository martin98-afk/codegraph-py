"""
CodeGraph Database Connection

Manages SQLite database connections and lifecycle.
"""

from __future__ import annotations

import os
import sqlite3
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from codegraph.errors import DatabaseError


def get_database_path(project_root: str) -> str:
    """Get the path to the SQLite database."""
    return os.path.join(project_root, '.codegraph', 'codegraph.db')


def remove_database_files(db_path: str) -> None:
    """Remove the database file and its WAL/SHM sidecars."""
    for suffix in ('', '-wal', '-shm'):
        p = db_path + suffix
        if os.path.exists(p):
            os.remove(p)


class DatabaseConnection:
    """Manages a SQLite database connection."""

    def __init__(self, db_path: str, db: sqlite3.Connection):
        self._path = db_path
        self._db = db
        self._db.row_factory = sqlite3.Row

        # ── Connection PRAGMAs (order matters) ──
        # busy_timeout MUST be first — if another process holds a write lock,
        # subsequent pragmas wait out the lock instead of throwing immediately.
        self._db.execute("PRAGMA busy_timeout = 5000")
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")      # Write-Ahead Logging
        self._db.execute("PRAGMA synchronous = NORMAL")    # safe with WAL mode
        self._db.execute("PRAGMA cache_size = -64000")     # 64 MB page cache
        self._db.execute("PRAGMA temp_store = MEMORY")     # temp tables in memory
        self._db.execute("PRAGMA mmap_size = 268435456")   # 256 MB memory-mapped I/O

    @staticmethod
    def initialize(db_path: str) -> 'DatabaseConnection':
        """Create a new database with schema."""
        from codegraph import directory as dir_mod
        cg_dir = os.path.dirname(db_path)
        os.makedirs(cg_dir, exist_ok=True)

        # Create .gitignore inside .codegraph/ to prevent index from being tracked
        gitignore_path = os.path.join(cg_dir, '.gitignore')
        if not os.path.isfile(gitignore_path):
            with open(gitignore_path, 'w') as f:
                f.write('# CodeGraph index — auto-generated, not for version control\n*\n')

        db = sqlite3.connect(db_path, check_same_thread=False)
        conn = DatabaseConnection(db_path, db)

        # Run schema
        schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
        if os.path.isfile(schema_path):
            with open(schema_path, 'r') as f:
                schema = f.read()
            db.executescript(schema)

        # Set initial schema version
        conn.set_metadata('schema_version', '1')

        return conn

    @staticmethod
    def open(db_path: str) -> 'DatabaseConnection':
        """Open an existing database."""
        if not os.path.isfile(db_path):
            raise DatabaseError(f'Database not found: {db_path}')
        db = sqlite3.connect(db_path, check_same_thread=False)
        return DatabaseConnection(db_path, db)

    def get_db(self) -> sqlite3.Connection:
        """Get the raw SQLite connection."""
        return self._db

    def get_path(self) -> str:
        """Get the database file path."""
        return self._path

    def close(self) -> None:
        """Close the database connection."""
        try:
            self._db.close()
        except Exception:
            pass

    def run_maintenance(self) -> None:
        """Run maintenance operations after bulk writes."""
        try:
            self._db.execute("PRAGMA optimize")
        except Exception:
            pass
        try:
            # Fold pending WAL pages back into the main DB file
            self._db.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            pass

    # =========================================================================
    # Metadata
    # =========================================================================

    def get_metadata(self, key: str) -> Optional[str]:
        """Get a metadata value."""
        try:
            cur = self._db.execute(
                'SELECT value FROM project_metadata WHERE key = ?', (key,)
            )
            row = cur.fetchone()
            return row['value'] if row else None
        except Exception:
            return None

    def set_metadata(self, key: str, value: str) -> None:
        """Set a metadata value."""
        try:
            self._db.execute(
                '''INSERT OR REPLACE INTO project_metadata (key, value, updated_at)
                   VALUES (?, ?, ?)''',
                (key, value, int(time.time() * 1000))
            )
        except Exception:
            pass

    def get_journal_mode(self) -> Optional[str]:
        """Get the effective journal mode."""
        try:
            cur = self._db.execute("PRAGMA journal_mode")
            row = cur.fetchone()
            return row[0] if row else None
        except Exception:
            return None

    def run_migrations(self) -> None:
        """Run any pending database migrations."""
        # Check current version
        current = self.get_metadata('schema_version')
        if current is None:
            current = '1'

        version = int(current)

        # Apply migrations as needed
        # Version 1 is the initial schema
        # Future migrations will be added here

        if version > 1:
            self.set_metadata('schema_version', str(version))
