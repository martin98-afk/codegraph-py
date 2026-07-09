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
        self._db.execute("PRAGMA cache_size = -128000")    # 128 MB page cache (doubled)
        self._db.execute("PRAGMA temp_store = MEMORY")     # temp tables in memory
        self._db.execute("PRAGMA mmap_size = 536870912")   # 512 MB memory-mapped I/O
        self._db.execute("PRAGMA wal_autocheckpoint = 2000")  # checkpoint less often
        self._db.execute("PRAGMA page_size = 4096")        # 4KB pages for WAL perf

# =============================================================================
# Embedded schema — duplicates schema.sql so the DB can be initialized even
# when PyInstaller (or other freezer) prevents access to the .sql file.
# Both the file-based reader (schema.sql) and this constant MUST stay in sync.
# =============================================================================
_SCHEMA_SQL = """
-- CodeGraph SQLite Schema
-- Version 1

CREATE TABLE IF NOT EXISTS schema_versions (
    version INTEGER PRIMARY KEY,
    applied_at INTEGER NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    language TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    start_column INTEGER NOT NULL,
    end_column INTEGER NOT NULL,
    docstring TEXT,
    signature TEXT,
    visibility TEXT,
    is_exported INTEGER DEFAULT 0,
    is_async INTEGER DEFAULT 0,
    is_static INTEGER DEFAULT 0,
    is_abstract INTEGER DEFAULT 0,
    decorators TEXT,
    type_parameters TEXT,
    return_type TEXT,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    kind TEXT NOT NULL,
    metadata TEXT,
    line INTEGER,
    col INTEGER,
    provenance TEXT DEFAULT NULL,
    FOREIGN KEY (source) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (target) REFERENCES nodes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    language TEXT NOT NULL,
    size INTEGER NOT NULL,
    modified_at INTEGER NOT NULL,
    indexed_at INTEGER NOT NULL,
    node_count INTEGER DEFAULT 0,
    errors TEXT
);

CREATE TABLE IF NOT EXISTS unresolved_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node_id TEXT NOT NULL,
    reference_name TEXT NOT NULL,
    reference_kind TEXT NOT NULL,
    line INTEGER NOT NULL,
    col INTEGER NOT NULL,
    candidates TEXT,
    file_path TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'unknown',
    FOREIGN KEY (from_node_id) REFERENCES nodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_qualified_name ON nodes(qualified_name);
CREATE INDEX IF NOT EXISTS idx_nodes_file_path ON nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_language ON nodes(language);
CREATE INDEX IF NOT EXISTS idx_nodes_file_line ON nodes(file_path, start_line);

CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    id UNINDEXED, name, qualified_name, docstring, signature,
    content='nodes', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)
    VALUES (new.rowid, new.id, new.name, new.qualified_name, new.docstring, new.signature);
END;

CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)
    VALUES ('delete', old.rowid, old.id, old.name, old.qualified_name, old.docstring, old.signature);
END;

CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)
    VALUES ('delete', old.rowid, old.id, old.name, old.qualified_name, old.docstring, old.signature);
    INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)
    VALUES (new.rowid, new.id, new.name, new.qualified_name, new.docstring, new.signature);
END;

CREATE TABLE IF NOT EXISTS name_segment_vocab (
    segment TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (segment, name)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
CREATE INDEX IF NOT EXISTS idx_edges_source_kind ON edges(source, kind);
CREATE INDEX IF NOT EXISTS idx_edges_target_kind ON edges(target, kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_identity
  ON edges(source, target, kind, IFNULL(line, -1), IFNULL(col, -1));

CREATE INDEX IF NOT EXISTS idx_files_language ON files(language);
CREATE INDEX IF NOT EXISTS idx_files_modified_at ON files(modified_at);

CREATE INDEX IF NOT EXISTS idx_unresolved_from_node ON unresolved_refs(from_node_id);
CREATE INDEX IF NOT EXISTS idx_unresolved_name ON unresolved_refs(reference_name);
CREATE INDEX IF NOT EXISTS idx_unresolved_file_path ON unresolved_refs(file_path);
CREATE INDEX IF NOT EXISTS idx_unresolved_from_name ON unresolved_refs(from_node_id, reference_name);
CREATE INDEX IF NOT EXISTS idx_edges_provenance ON edges(provenance);

CREATE TABLE IF NOT EXISTS project_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


def _load_schema() -> str:
    """Load schema SQL: try file first (dev), fall back to embedded (PyInstaller)."""
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    if os.path.isfile(schema_path):
        with open(schema_path, 'r') as f:
            return f.read()
    return _SCHEMA_SQL


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
        self._db.execute("PRAGMA cache_size = -128000")    # 128 MB page cache (doubled)
        self._db.execute("PRAGMA temp_store = MEMORY")     # temp tables in memory
        self._db.execute("PRAGMA mmap_size = 536870912")   # 512 MB memory-mapped I/O
        self._db.execute("PRAGMA wal_autocheckpoint = 2000")  # checkpoint less often
        self._db.execute("PRAGMA page_size = 4096")        # 4KB pages for WAL perf

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

        # Apply schema (embedded constant works with PyInstaller)
        db.executescript(_load_schema())

        # Set initial schema version
        conn.set_metadata('schema_version', '1')

        return conn

    @staticmethod
    def open(db_path: str) -> 'DatabaseConnection':
        """Open an existing database.

        If the database file exists but lacks the expected schema
        (e.g. after a partial init failure), the schema is applied
        automatically to make the database usable.
        """
        if not os.path.isfile(db_path):
            raise DatabaseError(f'Database not found: {db_path}')
        db = sqlite3.connect(db_path, check_same_thread=False)
        conn = DatabaseConnection(db_path, db)

        # Self-repair: if the files table doesn't exist, the schema
        # wasn't properly applied during init. Run it now so that
        # the database is usable even after a partial init failure.
        try:
            cur = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='files'"
            )
            if cur.fetchone() is None:
                db.executescript(_load_schema())
                conn.set_metadata('schema_version', '1')
        except Exception:
            pass

        return conn

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
