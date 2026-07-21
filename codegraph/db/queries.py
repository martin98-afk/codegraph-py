"""
CodeGraph Query Builder

All database CRUD operations and query methods.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import sqlite3
import time
from typing import Dict, List, Optional, Tuple
from collections import OrderedDict

from codegraph.types import (
    Node, Edge, FileRecord, UnresolvedReference,
    SearchResult, SearchOptions, GraphStats,
)

logger = logging.getLogger(__name__)


class QueryBuilder:
    """Build and execute database queries."""

    def __init__(self, db: sqlite3.Connection, db_path: str = ''):
        self._db = db
        self._db_path_value = db_path
        self._node_cache: Dict[str, Node] = OrderedDict()
        self._cache_max = 2000
        self._file_paths_cache: Optional[List[str]] = None
        self._file_cache_by_language: Dict[str, List[str]] = {}
        self._project_name_tokens: List[str] = []

    def set_project_name_tokens(self, tokens: List[str]) -> None:
        """Set project name tokens for search ranking."""
        self._project_name_tokens = tokens

    # =========================================================================
    # Node Operations
    # =========================================================================

    def insert_node(self, node: Node) -> None:
        """Insert or replace a node."""
        self._db.execute(
            '''INSERT OR REPLACE INTO nodes
               (id, kind, name, qualified_name, file_path, language,
                start_line, end_line, start_column, end_column,
                docstring, signature, visibility,
                is_exported, is_async, is_static, is_abstract,
                decorators, type_parameters, return_type, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                node.id, node.kind, node.name, node.qualified_name,
                node.file_path, node.language,
                node.start_line, node.end_line,
                node.start_column, node.end_column,
                node.docstring, node.signature, node.visibility,
                1 if node.is_exported else 0,
                1 if node.is_async else 0,
                1 if node.is_static else 0,
                1 if node.is_abstract else 0,
                json.dumps(node.decorators) if node.decorators else None,
                json.dumps(node.type_parameters) if node.type_parameters else None,
                node.return_type,
                node.updated_at or int(time.time() * 1000),
            )
        )

    def insert_nodes(self, nodes: List[Node]) -> None:
        """Insert multiple nodes in a transaction (batched executemany)."""
        if not nodes:
            return
        data = []
        now = int(time.time() * 1000)
        for node in nodes:
            data.append((
                node.id, node.kind, node.name, node.qualified_name,
                node.file_path, node.language,
                node.start_line, node.end_line,
                node.start_column, node.end_column,
                node.docstring, node.signature, node.visibility,
                1 if node.is_exported else 0,
                1 if node.is_async else 0,
                1 if node.is_static else 0,
                1 if node.is_abstract else 0,
                json.dumps(node.decorators) if node.decorators else None,
                json.dumps(node.type_parameters) if node.type_parameters else None,
                node.return_type,
                node.updated_at or now,
            ))
        try:
            with self._db:
                self._db.executemany(
                    '''INSERT OR REPLACE INTO nodes
                       (id, kind, name, qualified_name, file_path, language,
                        start_line, end_line, start_column, end_column,
                        docstring, signature, visibility,
                        is_exported, is_async, is_static, is_abstract,
                        decorators, type_parameters, return_type, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    data
                )
        except Exception as e:
            logger.warning(f"Batch node insert failed ({e}), falling back to individual inserts")
            with self._db:
                for node in nodes:
                    try:
                        self._db.execute(
                            '''INSERT OR REPLACE INTO nodes
                               (id, kind, name, qualified_name, file_path, language,
                                start_line, end_line, start_column, end_column,
                                docstring, signature, visibility,
                                is_exported, is_async, is_static, is_abstract,
                                decorators, type_parameters, return_type, updated_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                            (node.id, node.kind, node.name, node.qualified_name,
                             node.file_path, node.language,
                             node.start_line, node.end_line,
                             node.start_column, node.end_column,
                             node.docstring, node.signature, node.visibility,
                             1 if node.is_exported else 0,
                             1 if node.is_async else 0,
                             1 if node.is_static else 0,
                             1 if node.is_abstract else 0,
                             json.dumps(node.decorators) if node.decorators else None,
                             json.dumps(node.type_parameters) if node.type_parameters else None,
                             node.return_type,
                             node.updated_at or now,
                            )
                        )
                    except Exception as e2:
                        logger.error(f"Node insert failed: {node.id}: {e2}")

    def update_node(self, node: Node) -> None:
        """Update an existing node."""
        self._db.execute(
            '''UPDATE nodes SET
               kind=?, name=?, qualified_name=?, file_path=?, language=?,
               start_line=?, end_line=?, start_column=?, end_column=?,
               docstring=?, signature=?, visibility=?,
               is_exported=?, is_async=?, is_static=?, is_abstract=?,
               decorators=?, type_parameters=?, return_type=?, updated_at=?
               WHERE id=?''',
            (
                node.kind, node.name, node.qualified_name,
                node.file_path, node.language,
                node.start_line, node.end_line,
                node.start_column, node.end_column,
                node.docstring, node.signature, node.visibility,
                1 if node.is_exported else 0,
                1 if node.is_async else 0,
                1 if node.is_static else 0,
                1 if node.is_abstract else 0,
                json.dumps(node.decorators) if node.decorators else None,
                json.dumps(node.type_parameters) if node.type_parameters else None,
                node.return_type,
                node.updated_at or int(time.time() * 1000),
                node.id,
            )
        )

    def delete_node(self, node_id: str) -> None:
        """Delete a node by ID."""
        self._db.execute('DELETE FROM nodes WHERE id = ?', (node_id,))
        self._node_cache.pop(node_id, None)

    def delete_nodes_by_file(self, file_path: str) -> None:
        """Delete all nodes in a file."""
        self._invalidate_caches()
        self._db.execute('DELETE FROM nodes WHERE file_path = ?', (file_path,))

    def get_node_by_id(self, node_id: str) -> Optional[Node]:
        """Get a node by its ID (with LRU cache)."""
        # Check cache
        if node_id in self._node_cache:
            node = self._node_cache.pop(node_id)
            self._node_cache[node_id] = node
            return node

        cur = self._db.execute('SELECT * FROM nodes WHERE id = ?', (node_id,))
        row = cur.fetchone()
        if not row:
            return None

        node = self._row_to_node(row)

        # Update cache
        self._node_cache[node_id] = node
        if len(self._node_cache) > self._cache_max:
            self._node_cache.pop(next(iter(self._node_cache)), None)

        return node

    def get_nodes_by_ids(self, node_ids: List[str]) -> List[Node]:
        """Get multiple nodes by their IDs."""
        if not node_ids:
            return []

        # Check cache first
        result: List[Node] = []
        uncached: List[str] = []
        for nid in node_ids:
            if nid in self._node_cache:
                result.append(self._node_cache[nid])
            else:
                uncached.append(nid)

        if uncached:
            placeholders = ','.join('?' * len(uncached))
            cur = self._db.execute(
                f'SELECT * FROM nodes WHERE id IN ({placeholders})', uncached
            )
            for row in cur.fetchall():
                node = self._row_to_node(row)
                result.append(node)
                self._node_cache[node.id] = node

        return result

    def get_nodes_by_file(self, file_path: str) -> List[Node]:
        """Get all nodes in a file, ordered by start line."""
        cur = self._db.execute(
            'SELECT * FROM nodes WHERE file_path = ? ORDER BY start_line',
            (file_path,)
        )
        return [self._row_to_node(row) for row in cur.fetchall()]

    def get_nodes_by_kind(self, kind: str) -> List[Node]:
        """Get all nodes of a specific kind."""
        cur = self._db.execute(
            'SELECT * FROM nodes WHERE kind = ? ORDER BY name', (kind,)
        )
        return [self._row_to_node(row) for row in cur.fetchall()]

    def get_nodes_by_name(self, name: str) -> List[Node]:
        """Get nodes by exact name match."""
        cur = self._db.execute(
            'SELECT * FROM nodes WHERE name = ? ORDER BY file_path, start_line',
            (name,)
        )
        return [self._row_to_node(row) for row in cur.fetchall()]

    def get_nodes_by_name_prefix(self, prefix: str) -> List[Node]:
        """Get nodes by name prefix (range scan)."""
        end = prefix + '\uffff'
        cur = self._db.execute(
            'SELECT * FROM nodes WHERE name >= ? AND name < ? ORDER BY name',
            (prefix, end)
        )
        return [self._row_to_node(row) for row in cur.fetchall()]

    def get_nodes_by_qualified_name(self, qname: str) -> List[Node]:
        """Get nodes by exact qualified name."""
        cur = self._db.execute(
            'SELECT * FROM nodes WHERE qualified_name = ?', (qname,)
        )
        return [self._row_to_node(row) for row in cur.fetchall()]

    # =========================================================================
    # Edge Operations
    # =========================================================================

    def insert_edge(self, edge: Edge) -> None:
        """Insert an edge (with conflict ignore and FK error handling)."""
        try:
            self._db.execute(
                '''INSERT OR IGNORE INTO edges
                   (source, target, kind, metadata, line, col, provenance)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (
                    edge.source, edge.target, edge.kind,
                    json.dumps(edge.metadata) if edge.metadata else None,
                    edge.line, edge.column, edge.provenance,
                )
            )
        except Exception:
            # Gracefully handle FK violations (e.g., import edges to external modules)
            pass

    def insert_edges(self, edges: List[Edge]) -> None:
        """Insert multiple edges in a transaction (batched executemany)."""
        if not edges:
            return
        data = []
        for edge in edges:
            data.append((
                edge.source, edge.target, edge.kind,
                json.dumps(edge.metadata) if edge.metadata else None,
                edge.line, edge.column, edge.provenance,
            ))
        try:
            with self._db:
                self._db.executemany(
                    '''INSERT OR IGNORE INTO edges
                       (source, target, kind, metadata, line, col, provenance)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    data
                )
        except Exception as e:
            logger.warning(f"Batch edge insert failed ({e}), falling back to individual inserts")
            with self._db:
                for edge in edges:
                    try:
                        self._db.execute(
                            '''INSERT OR IGNORE INTO edges
                               (source, target, kind, metadata, line, col, provenance)
                               VALUES (?, ?, ?, ?, ?, ?, ?)''',
                            (edge.source, edge.target, edge.kind,
                             json.dumps(edge.metadata) if edge.metadata else None,
                             edge.line, edge.column, edge.provenance,)
                        )
                    except Exception as e2:
                        logger.error(f"Edge insert failed: {edge.source}->{edge.target}: {e2}")

    def delete_edges_for_file(self, file_path: str) -> None:
        """Delete all edges referencing nodes in a file."""
        self._db.execute(
            '''DELETE FROM edges WHERE source IN
               (SELECT id FROM nodes WHERE file_path = ?)
               OR target IN (SELECT id FROM nodes WHERE file_path = ?)''',
            (file_path, file_path)
        )

    def get_edges_by_source(self, source_id: str) -> List[Edge]:
        """Get all edges from a source node."""
        cur = self._db.execute(
            'SELECT * FROM edges WHERE source = ?', (source_id,)
        )
        return [self._row_to_edge(row) for row in cur.fetchall()]

    def get_edges_by_target(self, target_id: str) -> List[Edge]:
        """Get all edges targeting a node."""
        cur = self._db.execute(
            'SELECT * FROM edges WHERE target = ?', (target_id,)
        )
        return [self._row_to_edge(row) for row in cur.fetchall()]

    def get_edges(self, source: str, target: str, kind: str) -> List[Edge]:
        """Get edges matching source, target, and kind."""
        cur = self._db.execute(
            'SELECT * FROM edges WHERE source = ? AND target = ? AND kind = ?',
            (source, target, kind)
        )
        return [self._row_to_edge(row) for row in cur.fetchall()]

    def get_outgoing_edges(self, source_id: str,
                           kinds: Optional[List[str]] = None) -> List[Edge]:
        """Get outgoing edges, optionally filtered by kind."""
        if kinds:
            placeholders = ','.join('?' * len(kinds))
            cur = self._db.execute(
                f'SELECT * FROM edges WHERE source = ? AND kind IN ({placeholders})',
                [source_id] + kinds
            )
        else:
            cur = self._db.execute(
                'SELECT * FROM edges WHERE source = ?', (source_id,)
            )
        return [self._row_to_edge(row) for row in cur.fetchall()]

    def get_incoming_edges(self, target_id: str,
                           kinds: Optional[List[str]] = None) -> List[Edge]:
        """Get incoming edges, optionally filtered by kind."""
        if kinds:
            placeholders = ','.join('?' * len(kinds))
            cur = self._db.execute(
                f'SELECT * FROM edges WHERE target = ? AND kind IN ({placeholders})',
                [target_id] + kinds
            )
        else:
            cur = self._db.execute(
                'SELECT * FROM edges WHERE target = ?', (target_id,)
            )
        return [self._row_to_edge(row) for row in cur.fetchall()]

    def get_outgoing_edges_batch(self, source_ids: List[str],
                                  kinds: Optional[List[str]] = None) -> Dict[str, List[Edge]]:
        """Get outgoing edges for multiple source nodes (batched)."""
        if not source_ids:
            return {}

        result: Dict[str, List[Edge]] = {sid: [] for sid in source_ids}
        placeholders = ','.join('?' * len(source_ids))

        if kinds:
            kind_placeholders = ','.join('?' * len(kinds))
            cur = self._db.execute(
                f'SELECT * FROM edges WHERE source IN ({placeholders}) AND kind IN ({kind_placeholders})',
                source_ids + kinds
            )
        else:
            cur = self._db.execute(
                f'SELECT * FROM edges WHERE source IN ({placeholders})', source_ids
            )

        for row in cur.fetchall():
            edge = self._row_to_edge(row)
            if edge.source in result:
                result[edge.source].append(edge)
        return result

    def get_incoming_edges_batch(self, target_ids: List[str],
                                  kinds: Optional[List[str]] = None) -> Dict[str, List[Edge]]:
        """Get incoming edges for multiple target nodes (batched)."""
        if not target_ids:
            return {}

        result: Dict[str, List[Edge]] = {tid: [] for tid in target_ids}
        placeholders = ','.join('?' * len(target_ids))

        if kinds:
            kind_placeholders = ','.join('?' * len(kinds))
            cur = self._db.execute(
                f'SELECT * FROM edges WHERE target IN ({placeholders}) AND kind IN ({kind_placeholders})',
                target_ids + kinds
            )
        else:
            cur = self._db.execute(
                f'SELECT * FROM edges WHERE target IN ({placeholders})', target_ids
            )

        for row in cur.fetchall():
            edge = self._row_to_edge(row)
            if edge.target in result:
                result[edge.target].append(edge)
        return result

    # =========================================================================
    # File Operations
    # =========================================================================

    def upsert_file(self, record: FileRecord) -> None:
        """Insert or update a file record."""
        self._invalidate_caches()
        errors_json = None
        if record.errors:
            errors_json = json.dumps(
                [dataclasses.asdict(e) for e in record.errors]
            )
        self._db.execute(
            '''INSERT OR REPLACE INTO files
               (path, content_hash, language, size, modified_at, indexed_at, node_count, errors)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                record.path, record.content_hash, record.language,
                record.size, record.modified_at, record.indexed_at,
                record.node_count,
                errors_json,
            )
        )

    def delete_file(self, file_path: str) -> None:
        """Delete a file record."""
        self._invalidate_caches()
        self._db.execute('DELETE FROM files WHERE path = ?', (file_path,))

    def get_file(self, file_path: str) -> Optional[FileRecord]:
        """Get a file record by path."""
        cur = self._db.execute('SELECT * FROM files WHERE path = ?', (file_path,))
        row = cur.fetchone()
        if not row:
            return None
        return FileRecord(
            path=row['path'],
            content_hash=row['content_hash'],
            language=row['language'],
            size=row['size'],
            modified_at=row['modified_at'],
            indexed_at=row['indexed_at'],
            node_count=row['node_count'],
            errors=json.loads(row['errors']) if row['errors'] else None,
        )

    def get_all_files(self) -> List[FileRecord]:
        """Get all file records."""
        cur = self._db.execute('SELECT * FROM files')
        return [
            FileRecord(
                path=row['path'],
                content_hash=row['content_hash'],
                language=row['language'],
                size=row['size'],
                modified_at=row['modified_at'],
                indexed_at=row['indexed_at'],
                node_count=row['node_count'],
                errors=json.loads(row['errors']) if row['errors'] else None,
            )
            for row in cur.fetchall()
        ]

    def get_files_summary(self) -> List[Dict]:
        """Get per-file summary (path, language, node count, kind breakdown) in one query.

        Replaces the N+1 pattern of calling get_nodes_by_file() per file.
        """
        cur = self._db.execute(
            '''SELECT
                 f.path,
                 f.language,
                 f.node_count,
                 f.size,
                 f.modified_at,
                 f.indexed_at
               FROM files f
               ORDER BY f.path'''
        )
        files = []
        for row in cur.fetchall():
            files.append({
                'path': row['path'],
                'language': row['language'],
                'node_count': row['node_count'],
                'size': row['size'],
                'modified_at': row['modified_at'],
                'indexed_at': row['indexed_at'],
            })

        # Batch-load kind breakdown per file
        kind_map: Dict[str, Dict[str, int]] = {}
        cur = self._db.execute(
            '''SELECT file_path, kind, COUNT(*) as cnt
               FROM nodes
               WHERE kind != 'file'
               GROUP BY file_path, kind
               ORDER BY file_path'''
        )
        for row in cur.fetchall():
            kind_map.setdefault(row['file_path'], {})[row['kind']] = row['cnt']

        for f in files:
            f['kinds'] = kind_map.get(f['path'], {})
            f['total_symbols'] = sum(f['kinds'].values())

        return files

    def get_nodes_by_files_batch(self, file_paths: List[str]) -> Dict[str, List[Node]]:
        """Get all nodes for multiple files in ONE query.

        Returns a dict mapping file_path -> List[Node].
        """
        if not file_paths:
            return {}
        placeholders = ','.join('?' * len(file_paths))
        cur = self._db.execute(
            f'SELECT * FROM nodes WHERE file_path IN ({placeholders}) ORDER BY file_path, start_line',
            file_paths
        )
        result: Dict[str, List[Node]] = {}
        for row in cur.fetchall():
            node = self._row_to_node(row)
            result.setdefault(node.file_path, []).append(node)
        return result

    def get_all_file_paths(self) -> List[str]:
        """Get all tracked file paths (validated cache).

        Cache is self-validating: if the cached count doesn't match
        SELECT COUNT(*), the cache is refreshed. This prevents silent
        divergence between get_stats().file_count and get_all_file_paths().
        """
        if self._file_paths_cache is not None:
            # Validate cache: check if count matches
            try:
                count = self._db.execute('SELECT COUNT(*) FROM files').fetchone()[0]
                if len(self._file_paths_cache) != count:
                    self._file_paths_cache = None
            except Exception:
                pass

        if self._file_paths_cache is None:
            cur = self._db.execute('SELECT path FROM files')
            self._file_paths_cache = [row['path'] for row in cur.fetchall()]

        return self._file_paths_cache

    # =========================================================================
    # Unresolved Reference Operations
    # =========================================================================

    def insert_unresolved_ref(self, ref: UnresolvedReference) -> None:
        """Insert an unresolved reference."""
        self._db.execute(
            '''INSERT INTO unresolved_refs
               (from_node_id, reference_name, reference_kind, line, col,
                candidates, file_path, language)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                ref.from_node_id, ref.reference_name, ref.reference_kind,
                ref.line, ref.column,
                json.dumps(ref.candidates) if ref.candidates else None,
                ref.file_path or '', ref.language or 'unknown',
            )
        )

    def insert_unresolved_refs_batch(self, refs: List[UnresolvedReference]) -> None:
        """Insert multiple unresolved references (batched)."""
        if not refs:
            return
        data = []
        for ref in refs:
            data.append((
                ref.from_node_id, ref.reference_name, ref.reference_kind,
                ref.line, ref.column,
                json.dumps(ref.candidates) if ref.candidates else None,
                ref.file_path or '', ref.language or 'unknown',
            ))
        with self._db:
            self._db.executemany(
                '''INSERT INTO unresolved_refs
                   (from_node_id, reference_name, reference_kind, line, col,
                    candidates, file_path, language)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                data
            )

    def delete_unresolved_refs_for_file(self, file_path: str) -> None:
        """Delete unresolved refs for a file."""
        self._db.execute(
            'DELETE FROM unresolved_refs WHERE file_path = ?', (file_path,)
        )

    def get_unresolved_refs(self) -> List[UnresolvedReference]:
        """Get all unresolved references."""
        cur = self._db.execute('SELECT * FROM unresolved_refs')
        return [self._row_to_unresolved(row) for row in cur.fetchall()]

    def get_unresolved_refs_count(self) -> int:
        """Get count of unresolved references."""
        cur = self._db.execute('SELECT COUNT(*) FROM unresolved_refs')
        return cur.fetchone()[0]

    def get_unresolved_refs_count_by_files(self, file_paths: List[str]) -> int:
        """Get count of unresolved references from specific file paths."""
        if not file_paths:
            return 0
        placeholders = ','.join('?' * len(file_paths))
        cur = self._db.execute(
            f'SELECT COUNT(*) FROM unresolved_refs WHERE file_path IN ({placeholders})',
            file_paths
        )
        return cur.fetchone()[0]

    def get_unresolved_refs_by_name(self, name: str) -> List[UnresolvedReference]:
        """Get unresolved references by reference name."""
        cur = self._db.execute(
            'SELECT * FROM unresolved_refs WHERE reference_name = ?', (name,)
        )
        return [self._row_to_unresolved(row) for row in cur.fetchall()]

    def get_unresolved_refs_by_from_node(self, from_node_id: str) -> List[UnresolvedReference]:
        """Get unresolved references by originating node ID."""
        cur = self._db.execute(
            'SELECT * FROM unresolved_refs WHERE from_node_id = ?', (from_node_id,)
        )
        return [self._row_to_unresolved(row) for row in cur.fetchall()]

    def get_unresolved_refs_by_files(self, file_paths: List[str]) -> List[UnresolvedReference]:
        """Get unresolved references from specific file paths."""
        if not file_paths:
            return []
        placeholders = ','.join('?' * len(file_paths))
        cur = self._db.execute(
            f'SELECT * FROM unresolved_refs WHERE file_path IN ({placeholders})',
            file_paths
        )
        return [self._row_to_unresolved(row) for row in cur.fetchall()]

    def clear_unresolved_refs(self) -> None:
        """Clear all unresolved references."""
        self._db.execute('DELETE FROM unresolved_refs')

    # =========================================================================
    # Search (FTS5)
    # =========================================================================

    def search_nodes(self, query: str,
                     options: Optional[SearchOptions] = None) -> List[SearchResult]:
        """Search nodes in the codebase.

        Supports three modes:
        - Exact match (opts.exact_match=True): precise name lookup via SQL
        - Substring mode (opts.substring=True): LIKE %word% matching (catches
          SessionManager when searching "Manager")
        - FTS5 full-text: prefix-based ranking search (default)
        - LIKE fallback: when FTS returns nothing

        Additional filters:
        - case_sensitive: use COLLATE BINARY for exact/LIKE matching
        - visibility: 'public' (no _ prefix), 'private' (_ prefix), or None (all)
        """
        opts = options or SearchOptions()
        results: List[SearchResult] = []

        words = [w for w in query.split() if w.strip()]

        # ── Collation helper: COLLATE BINARY when case_sensitive ──
        coll = ' COLLATE BINARY' if opts.case_sensitive else ''

        # ── Substring mode (LIKE %word%, no FTS) ──
        # Escapes SQL LIKE wildcards (_ → \_, % → \%) to avoid false matches
        if opts.substring and words:
            for word in words:
                escaped = self._escape_like(word)
                like_pattern = f'%{escaped}%'
                if opts.case_sensitive:
                    cur = self._db.execute(
                        f'''SELECT * FROM nodes WHERE
                           name LIKE ? COLLATE BINARY ESCAPE '\\'
                           OR qualified_name LIKE ? COLLATE BINARY ESCAPE '\\'
                           ORDER BY
                             CASE WHEN name = ? COLLATE BINARY THEN 0
                                  WHEN name LIKE ? COLLATE BINARY ESCAPE '\\' THEN 1
                                  ELSE 2 END
                           LIMIT ?''',
                        (like_pattern, like_pattern, word, word + '%', opts.limit)
                    )
                else:
                    cur = self._db.execute(
                        '''SELECT * FROM nodes WHERE
                           name LIKE ? ESCAPE '\\'
                           OR qualified_name LIKE ? ESCAPE '\\'
                           ORDER BY
                             CASE WHEN name = ? THEN 0
                                  WHEN name LIKE ? ESCAPE '\\' THEN 1
                                  ELSE 2 END
                           LIMIT ?''',
                        (like_pattern, like_pattern, word, f'{escaped}%', opts.limit)
                    )
                for row in cur.fetchall():
                    node = self._row_to_node(row)
                    if self._matches_filters(node, opts):
                        results.append(SearchResult(node=node, score=0.6))
            results.sort(key=lambda r: -r.score)
            results = results[:opts.limit]
            results = self._apply_visibility(results, opts)
            return results

        # ── Exact match mode ──
        if opts.exact_match and query.strip():
            sql = f'''SELECT * FROM nodes WHERE name = ?{coll}
                      ORDER BY file_path, start_line
                      LIMIT ?'''
            cur = self._db.execute(sql, (query.strip(), opts.limit))
            for row in cur.fetchall():
                node = self._row_to_node(row)
                if self._matches_filters(node, opts):
                    results.append(SearchResult(node=node, score=1.0))
            results = self._apply_visibility(results, opts)
            return results

        # ── FTS5 full-text search ──
        # Skip FTS5 for very short queries (1-2 chars) — prefix matching is
        # slow and unselective; LIKE handles these better.
        use_fts = not any(len(w) <= 2 for w in words)

        if use_fts and words:
            try:
                # Strategy: AND-first, OR-fallback
                and_query = ' '.join(
                    f'"{word}"*' if len(word) > 1 else word
                    for word in words
                )

                and_results: List[SearchResult] = []
                if and_query:
                    cur = self._db.execute(
                        '''SELECT n.*, rank FROM nodes_fts
                           JOIN nodes n ON nodes_fts.id = n.id
                           WHERE nodes_fts MATCH ?
                           ORDER BY rank
                           LIMIT ? OFFSET ?''',
                        (and_query, opts.limit, opts.offset)
                    )
                    for row in cur.fetchall():
                        node = self._row_to_node(row)
                        if self._matches_filters(node, opts):
                            and_results.append(SearchResult(
                                node=node,
                                score=1.0 - float(row['rank']) / 100.0 if row['rank'] else 0.0,
                            ))

                # Use AND results if they fill at least half the limit
                if len(and_results) >= max(opts.limit // 2, 2):
                    results = and_results
                else:
                    # Supplement with OR to catch partial matches
                    results = list(and_results)
                    seen_ids = {r.node.id for r in results}
                    or_query = ' OR '.join(
                        f'"{word}"*' if len(word) > 1 else word
                        for word in words
                    )
                    if or_query:
                        cur = self._db.execute(
                            '''SELECT n.*, rank FROM nodes_fts
                               JOIN nodes n ON nodes_fts.id = n.id
                               WHERE nodes_fts MATCH ?
                               ORDER BY rank
                               LIMIT ? OFFSET ?''',
                            (or_query, opts.limit, opts.offset)
                        )
                        for row in cur.fetchall():
                            node = self._row_to_node(row)
                            if node.id not in seen_ids and self._matches_filters(node, opts):
                                seen_ids.add(node.id)
                                results.append(SearchResult(
                                    node=node,
                                    score=(1.0 - float(row['rank']) / 100.0) * 0.8 if row['rank'] else 0.4,
                                ))

                # ── Symbol-name boosting ──
                query_lower = query.lower()
                query_words = set(query_lower.split())
                for r in results:
                    name_lower = r.node.name.lower()
                    qname_lower = r.node.qualified_name.lower()
                    if name_lower == query_lower or qname_lower == query_lower:
                        r.score = min(r.score + 0.5, 1.0)
                    elif name_lower.startswith(query_lower):
                        r.score = min(r.score + 0.3, 1.0)
                    elif query_words and all(w in name_lower for w in query_words):
                        r.score = min(r.score + 0.2, 1.0)
                    elif query_words and any(w in name_lower for w in query_words):
                        r.score = min(r.score + 0.1, 1.0)
            except Exception:
                pass

        # ── LIKE fallback: only when FTS returned nothing ──
        if not results and words:
            for word in words:
                escaped = self._escape_like(word)
                like_pattern = f'%{escaped}%'
                cur = self._db.execute(
                    f'''SELECT * FROM nodes WHERE
                       name LIKE ?{coll} ESCAPE '\\'
                       OR qualified_name LIKE ?{coll} ESCAPE '\\'
                       ORDER BY
                         CASE WHEN name = ?{coll} THEN 0
                              WHEN name LIKE ?{coll} ESCAPE '\\' THEN 1
                              ELSE 2 END
                       LIMIT ?''',
                    (like_pattern, like_pattern, word, f'{escaped}%', opts.limit)
                )
                for row in cur.fetchall():
                    node = self._row_to_node(row)
                    if self._matches_filters(node, opts):
                        results.append(SearchResult(node=node, score=0.5))

        # ── Visibility post-filter ──
        results = self._apply_visibility(results, opts)

        return results

    @staticmethod
    def _apply_visibility(results: List[SearchResult], opts: SearchOptions) -> List[SearchResult]:
        """Post-filter search results by visibility convention (_ prefix = private)."""
        if not opts.visibility or not results:
            return results
        filtered = []
        for r in results:
            name = r.node.name
            is_private = name.startswith('_')
            if opts.visibility == 'private' and is_private:
                filtered.append(r)
            elif opts.visibility == 'public' and not is_private:
                filtered.append(r)
            elif opts.visibility in (None, 'all'):
                filtered.append(r)
        return filtered

    @staticmethod
    def _escape_like(word: str) -> str:
        """Escape SQL LIKE wildcards in a search term.

        In SQL LIKE patterns:
        - ``%`` matches any sequence of characters
        - ``_`` matches any single character

        This method escapes them with backslash so they are treated literally.
        Example: '_execute' → '\\_execute' so LIKE does not interpret '_' as wildcard.
        """
        return word.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')

    def _matches_filters(self, node: Node, opts: SearchOptions) -> bool:
        """Check if a node matches search filters."""
        # Kinds and languages are always exact (case_sensitive irrelevant)
        if opts.kinds and node.kind not in opts.kinds:
            return False
        if opts.languages and node.language not in opts.languages:
            return False
        if opts.include_patterns:
            import fnmatch
            if not any(fnmatch.fnmatch(node.file_path, p) for p in opts.include_patterns):
                return False
        if opts.exclude_patterns:
            import fnmatch
            if any(fnmatch.fnmatch(node.file_path, p) for p in opts.exclude_patterns):
                return False
        return True

    # =========================================================================
    # Name Segment Vocabulary
    # =========================================================================

    def insert_name_segment(self, segment: str, name: str) -> None:
        """Insert a name segment mapping."""
        try:
            self._db.execute(
                'INSERT OR IGNORE INTO name_segment_vocab (segment, name) VALUES (?, ?)',
                (segment, name)
            )
        except Exception:
            pass

    def clear_name_segment_vocab(self) -> None:
        """Clear all name segment entries."""
        try:
            self._db.execute('DELETE FROM name_segment_vocab')
        except Exception:
            pass

    def get_names_by_segment(self, segment: str) -> List[str]:
        """Get symbol names matching a prose segment."""
        cur = self._db.execute(
            'SELECT name FROM name_segment_vocab WHERE segment = ?', (segment,)
        )
        return [row['name'] for row in cur.fetchall()]

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> GraphStats:
        """Get graph statistics."""
        stats = GraphStats()

        try:
            cur = self._db.execute('SELECT COUNT(*) FROM nodes')
            stats.node_count = cur.fetchone()[0]
        except Exception:
            pass

        try:
            cur = self._db.execute('SELECT COUNT(*) FROM edges')
            stats.edge_count = cur.fetchone()[0]
        except Exception:
            pass

        try:
            cur = self._db.execute('SELECT COUNT(*) FROM files')
            stats.file_count = cur.fetchone()[0]
        except Exception:
            pass

        try:
            cur = self._db.execute(
                'SELECT kind, COUNT(*) as cnt FROM nodes GROUP BY kind'
            )
            for row in cur.fetchall():
                stats.nodes_by_kind[row['kind']] = row['cnt']
        except Exception:
            pass

        try:
            cur = self._db.execute(
                'SELECT kind, COUNT(*) as cnt FROM edges GROUP BY kind'
            )
            for row in cur.fetchall():
                stats.edges_by_kind[row['kind']] = row['cnt']
        except Exception:
            pass

        try:
            cur = self._db.execute(
                'SELECT language, COUNT(*) as cnt FROM files GROUP BY language'
            )
            for row in cur.fetchall():
                stats.files_by_language[row['language']] = row['cnt']
        except Exception:
            pass

        try:
            stats.db_size_bytes = os.path.getsize(self._db_path)
        except Exception:
            pass

        try:
            stats.last_updated = int(time.time() * 1000)
        except Exception:
            pass

        return stats

    def get_node_and_edge_count(self) -> Tuple[int, int]:
        """Get total node and edge counts."""
        try:
            cur = self._db.execute('SELECT COUNT(*) FROM nodes')
            nodes = cur.fetchone()[0]
        except Exception:
            nodes = 0
        try:
            cur = self._db.execute('SELECT COUNT(*) FROM edges')
            edges = cur.fetchone()[0]
        except Exception:
            edges = 0
        return (nodes, edges)

    def get_changed_files(self, file_records: Dict[str, FileRecord]) -> Tuple[
        List[str], List[str], List[str]]:
        """Compare files table with current records to find changes.
        
        Returns (added, modified, removed) file paths.
        """
        indexed_paths = set(self.get_all_file_paths())
        current_paths = set(file_records.keys())

        added = list(current_paths - indexed_paths)
        removed = list(indexed_paths - current_paths)

        modified = []
        for path in current_paths & indexed_paths:
            cur = self._db.execute(
                'SELECT content_hash FROM files WHERE path = ?', (path,)
            )
            row = cur.fetchone()
            if row and row['content_hash'] != file_records[path].content_hash:
                modified.append(path)

        return (added, modified, removed)

    # =========================================================================
    # Metadata
    # =========================================================================

    def set_metadata(self, key: str, value: str) -> None:
        """Set a metadata key-value pair."""
        try:
            self._db.execute(
                '''INSERT OR REPLACE INTO project_metadata (key, value, updated_at)
                   VALUES (?, ?, ?)''',
                (key, value, int(time.time() * 1000))
            )
        except Exception:
            pass

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

    # =========================================================================
    # Cache helpers
    # =========================================================================

    def _invalidate_caches(self) -> None:
        """Invalidate all caches when data changes."""
        self._file_paths_cache = None
        self._file_cache_by_language.clear()

    def _invalidate_node_cache(self) -> None:
        """Invalidate node cache."""
        self._node_cache.clear()

    # =========================================================================
    # Helper methods
    # =========================================================================


    @property
    def _db_path(self) -> str:
        """Get the database file path."""
        if self._db_path_value:
            return self._db_path_value
        try:
            cur = self._db.execute("PRAGMA database_list")
            row = cur.fetchone()
            if row and row[2]:
                return row[2]
        except Exception:
            pass
        return ''

    def _row_to_node(self, row: sqlite3.Row) -> Node:
        """Convert a database row to a Node object."""
        return Node(
            id=row['id'],
            kind=row['kind'],
            name=row['name'],
            qualified_name=row['qualified_name'],
            file_path=row['file_path'],
            language=row['language'],
            start_line=row['start_line'],
            end_line=row['end_line'],
            start_column=row['start_column'],
            end_column=row['end_column'],
            docstring=row['docstring'],
            signature=row['signature'],
            visibility=row['visibility'],
            is_exported=bool(row['is_exported']),
            is_async=bool(row['is_async']),
            is_static=bool(row['is_static']),
            is_abstract=bool(row['is_abstract']),
            decorators=json.loads(row['decorators']) if row['decorators'] else None,
            type_parameters=json.loads(row['type_parameters']) if row['type_parameters'] else None,
            return_type=row['return_type'],
            updated_at=row['updated_at'],
        )

    def _row_to_edge(self, row: sqlite3.Row) -> Edge:
        """Convert a database row to an Edge object."""
        return Edge(
            id=row['id'],
            source=row['source'],
            target=row['target'],
            kind=row['kind'],
            metadata=json.loads(row['metadata']) if row['metadata'] else None,
            line=row['line'],
            column=row['col'],
            provenance=row['provenance'],
        )

    def _row_to_unresolved(self, row: sqlite3.Row) -> UnresolvedReference:
        """Convert a database row to an UnresolvedReference object."""
        return UnresolvedReference(
            id=row['id'],
            from_node_id=row['from_node_id'],
            reference_name=row['reference_name'],
            reference_kind=row['reference_kind'],
            line=row['line'],
            column=row['col'],
            candidates=json.loads(row['candidates']) if row['candidates'] else None,
            file_path=row['file_path'],
            language=row['language'],
        )
