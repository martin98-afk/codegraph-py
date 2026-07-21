"""
CodeGraph Main Class

Primary interface for interacting with the code knowledge graph.
"""

from __future__ import annotations

import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Callable

from loguru import logger

from codegraph.context import create_context_builder
from codegraph.db import DatabaseConnection, QueryBuilder, get_database_path
from codegraph.directory import (
    is_initialized, create_directory,
    remove_directory, validate_directory, derive_project_name_tokens,
)
from codegraph.errors import CodeGraphError
from codegraph.extraction import (
    ExtractionOrchestrator, )
from codegraph.graph import GraphTraverser, GraphQueryManager
from codegraph.resolution import create_resolver
from codegraph.sync import FileWatcher, WatchOptions, PendingFile
from codegraph.types import (
    Node, Edge, Subgraph, GraphStats, TaskContext,
    SearchResult, SearchOptions, SegmentMatch, BuildContextOptions, ExploreResult,
)

__version__ = "1.2.4"


@dataclass
class IndexResult:
    """Result of a full project index operation."""
    success: bool = False
    files_indexed: int = 0
    files_skipped: int = 0
    files_errored: int = 0
    nodes_created: int = 0
    edges_created: int = 0
    duration_ms: float = 0.0
    errors: List[Dict] = field(default_factory=list)


@dataclass
class SyncResult:
    """Result of a sync operation."""
    files_checked: int = 0
    files_added: int = 0
    files_modified: int = 0
    files_removed: int = 0
    nodes_updated: int = 0
    duration_ms: float = 0.0
    changed_file_paths: List[str] = field(default_factory=list)
    failed_files: List[str] = field(default_factory=list)


class CodeGraph:
    """
    Main CodeGraph class providing the primary interface for interacting
    with the code knowledge graph.
    """

    def __init__(self, db: DatabaseConnection, queries: QueryBuilder,
                 project_root: str):
        self._db = db
        self._queries = queries
        self._project_root = project_root
        self._watcher: Optional[FileWatcher] = None

        # Initialize layers
        self._wire_layers()

    def _wire_layers(self) -> None:
        """Build the extraction/graph/context layers."""
        try:
            tokens = derive_project_name_tokens(self._project_root)
            self._queries.set_project_name_tokens(tokens)
        except Exception:
            pass

        self._orchestrator = ExtractionOrchestrator(
            self._project_root, self._queries
        )
        self._resolver = create_resolver(self._project_root, self._queries)
        self._graph_manager = GraphQueryManager(self._db.get_db(), self._db.get_path())
        self._traverser = GraphTraverser(self._db.get_db(), self._db.get_path())
        self._context_builder = create_context_builder(
            self._project_root, self._queries, self._traverser
        )

    # =========================================================================
    # Lifecycle Methods
    # =========================================================================

    @staticmethod
    async def init(project_root: str,
                   options: Optional[Dict] = None) -> 'CodeGraph':
        """Initialize a new CodeGraph project."""
        resolved = os.path.abspath(project_root)

        if is_initialized(resolved):
            raise CodeGraphError(f'CodeGraph already initialized in {resolved}')

        create_directory(resolved)
        db_path = get_database_path(resolved)
        db = DatabaseConnection.initialize(db_path)
        queries = QueryBuilder(db.get_db(), db_path)

        instance = CodeGraph(db, queries, resolved)

        # Run initial indexing if requested
        if options and options.get('index', True):
            await instance.index_all(
                on_progress=options.get('on_progress')
            )

        return instance

    @staticmethod
    def init_sync(project_root: str) -> 'CodeGraph':
        """Initialize synchronously (without indexing)."""
        resolved = os.path.abspath(project_root)

        if is_initialized(resolved):
            raise CodeGraphError(f'CodeGraph already initialized in {resolved}')

        create_directory(resolved)
        db_path = get_database_path(resolved)
        db = DatabaseConnection.initialize(db_path)
        queries = QueryBuilder(db.get_db(), db_path)

        return CodeGraph(db, queries, resolved)

    @staticmethod
    async def open(project_root: str,
                   options: Optional[Dict] = None) -> 'CodeGraph':
        """Open an existing CodeGraph project."""
        resolved = os.path.abspath(project_root)

        if not is_initialized(resolved):
            raise CodeGraphError(
                f'CodeGraph not initialized in {resolved}. Run init() first.'
            )

        valid, errors = validate_directory(resolved)
        if not valid:
            raise CodeGraphError(
                f'Invalid CodeGraph directory: {", ".join(errors)}'
            )

        db_path = get_database_path(resolved)
        db = DatabaseConnection.open(db_path)
        queries = QueryBuilder(db.get_db(), db_path)

        instance = CodeGraph(db, queries, resolved)

        # Sync if requested
        if options and options.get('sync', False):
            await instance.sync()

        return instance

    @staticmethod
    def open_sync(project_root: str) -> 'CodeGraph':
        """Open synchronously (without sync)."""
        resolved = os.path.abspath(project_root)

        if not is_initialized(resolved):
            raise CodeGraphError(
                f'CodeGraph not initialized in {resolved}. Run init() first.'
            )

        valid, errors = validate_directory(resolved)
        if not valid:
            raise CodeGraphError(
                f'Invalid CodeGraph directory: {", ".join(errors)}'
            )

        db_path = get_database_path(resolved)
        db = DatabaseConnection.open(db_path)
        queries = QueryBuilder(db.get_db(), db_path)

        return CodeGraph(db, queries, resolved)

    @staticmethod
    def is_initialized(project_root: str) -> bool:
        """Check if a directory has been initialized."""
        return is_initialized(os.path.abspath(project_root))

    def close(self) -> None:
        """Close the CodeGraph instance and release resources."""
        self.unwatch()
        self._db.close()

    def destroy(self) -> None:
        """Alias for close()."""
        self.close()

    def get_project_root(self) -> str:
        """Get the project root directory."""
        return self._project_root

    # =========================================================================
    # Indexing
    # =========================================================================

    def index_all(self, on_progress=None, signal=None,
                   verbose: bool = False, force: bool = False) -> IndexResult:
        """Index all files in the project.

        Args:
            on_progress: Optional progress callback
            signal: Optional cancellation signal (callable returning bool)
            verbose: Enable verbose logging
            force: Force re-indexing even for unchanged files
        """
        # Use the orchestrator's sync method which does a full scan + index
        sync_result = self._orchestrator.sync(
            force=force,
            max_workers=None if force else None,
        )

        # Build the result
        errors = []
        if sync_result.total_errors > 0:
            errors = [{'message': str(e), 'severity': 'warning'}
                       for e in sync_result.progress.errors] if sync_result.progress else []

        result = IndexResult(
            success=True,
            files_indexed=len(sync_result.indexed_files),
            files_skipped=len(sync_result.skipped_files),
            files_errored=sync_result.total_errors,
            nodes_created=sync_result.total_nodes,
            edges_created=sync_result.total_edges,
            duration_ms=sync_result.duration_ms,
            errors=errors,
        )

        if result.files_indexed > 0:
            self._resolver.initialize()
            self._resolver.run_post_extract()

            # Resolve references
            ref_count = self._queries.get_unresolved_refs_count()
            if ref_count > 0:
                self._resolve_references()

        # Update metadata
        self._queries.set_metadata('last_indexed_at', str(int(time.time() * 1000)))
        self._queries.set_metadata('index_state', 'complete')
        self._queries.set_metadata('indexed_with_version', __version__)
        try:
            from codegraph.extraction import EXTRACTION_VERSION
            self._queries.set_metadata('indexed_with_extraction_version', str(EXTRACTION_VERSION))
        except ImportError:
            pass

        return result

    def index_files(self, file_paths: List[str]) -> IndexResult:
        """Index specific files."""
        start_time = time.time()
        total_nodes = 0
        total_edges = 0
        errors = []

        for fp in file_paths:
            file_result = self._orchestrator.index_file(fp)
            if file_result.success:
                total_nodes += file_result.nodes_count
                total_edges += file_result.edges_count
            else:
                for e in file_result.errors:
                    errors.append({'message': e.message, 'severity': 'error'})

        # Re-resolve references for the newly indexed files
        if total_nodes > 0:
            ref_count = self._queries.get_unresolved_refs_count_by_files(file_paths)
            if ref_count > 0:
                self._resolver.initialize()
                self._resolve_references(file_filter=file_paths)

        return IndexResult(
            success=len(errors) == 0,
            files_indexed=len(file_paths) - len(errors),
            files_errored=len(errors),
            nodes_created=total_nodes,
            edges_created=total_edges,
            duration_ms=(time.time() - start_time) * 1000,
            errors=errors,
        )

    # Track last sync time for debounce
    _last_sync_ms: float = 0.0

    def sync(self, on_progress=None, quick: bool = False) -> SyncResult:
        """Sync changes since last index.

        Uses detailed file scan to collect mtime during directory walk,
        eliminating per-file stat() calls for faster change detection.

        Args:
            on_progress: Optional progress callback
            quick: If True, skip full scan if last sync was recent (< 5s ago).
                   Use when sync is called speculatively before a query.
        """
        # ── Quick skip: if recently synced and caller only wants a quick check ──
        if quick and self._last_sync_ms > 0:
            elapsed = (time.time() * 1000) - self._last_sync_ms
            if elapsed < 5000:  # 5 second debounce
                return SyncResult(
                    files_checked=0, duration_ms=0.0,
                )

        start_time = time.time()

        # Get existing files from DB
        existing_paths = set(self._queries.get_all_file_paths())

        # Scan current files with mtime collected during walk
        scanned = self._orchestrator.scan_files_with_details()
        current_paths = {d.path for d in scanned}
        mtime_map = {d.path: d.mtime_s for d in scanned}

        added = list(current_paths - existing_paths)
        removed = list(existing_paths - current_paths)

        # Check for modified files — uses mtime from scan, no extra stat calls
        modified = []
        for fp in (current_paths & existing_paths):
            rec = self._queries.get_file(fp)
            if rec and fp in mtime_map:
                current_mtime_ms = int(mtime_map[fp] * 1000)
                if current_mtime_ms > rec.modified_at:
                    modified.append(fp)

        # Index changed files (added + modified)
        nodes_updated = 0
        failed_files: List[str] = []
        indexed_files: List[str] = []
        for fp in added + modified:
            try:
                file_result = self._orchestrator.index_file(fp)
                if file_result.success:
                    nodes_updated += file_result.nodes_count
                    if not file_result.skipped:
                        indexed_files.append(fp)
                else:
                    failed_files.append(fp)
            except Exception as e:
                failed_files.append(fp)
                logger.warning('sync: index_file failed for %s: %s', fp, str(e))

        # Remove deleted files
        for fp in removed:
            try:
                self._queries.delete_file(fp)
                self._queries.delete_nodes_by_file(fp)
            except Exception as e:
                logger.warning('sync: cleanup failed for %s: %s', fp, str(e))

        # Re-resolve references only for files that were actually re-indexed
        # (skip refs from unchanged files — they've been tried before and failed)
        if indexed_files:
            try:
                ref_count = self._queries.get_unresolved_refs_count_by_files(indexed_files)
                if ref_count > 0:
                    self._resolver.initialize()
                    self._resolve_references(file_filter=indexed_files)
            except Exception as e:
                logger.warning('sync: reference resolution failed: %s', str(e))

        self._last_sync_ms = time.time() * 1000

        result = SyncResult(
            files_checked=len(current_paths),
            files_added=len(added),
            files_modified=len(modified),
            files_removed=len(removed),
            nodes_updated=nodes_updated,
            duration_ms=(time.time() - start_time) * 1000,
            changed_file_paths=added + modified,
            failed_files=failed_files,
        )

        return result

    def uninitialize(self) -> None:
        """Remove CodeGraph from the project."""
        self.close()
        remove_directory(self._project_root)

    # =========================================================================
    # Query Methods
    # =========================================================================

    def search_nodes(self, query: str,
                     options: Optional[SearchOptions] = None) -> List[SearchResult]:
        """Search for symbols in the codebase."""
        return self._queries.search_nodes(query, options)

    def get_node(self, node_id: str) -> Optional[Node]:
        """Get a node by ID."""
        return self._queries.get_node_by_id(node_id)

    def get_nodes_by_name(self, name: str) -> List[Node]:
        """Get nodes by exact name."""
        return self._queries.get_nodes_by_name(name)

    def get_nodes_by_file(self, file_path: str) -> List[Node]:
        """Get all nodes in a file."""
        return self._queries.get_nodes_by_file(file_path)

    def get_nodes_by_files_batch(self, file_paths: List[str]) -> Dict[str, List[Node]]:
        """Get all nodes for multiple files in ONE query.

        Batch replacement for calling get_nodes_by_file() per file (N+1).
        Returns a dict mapping file_path -> List[Node].

        Example:
            nodes = cg.get_nodes_by_files_batch(['a.py', 'b.py'])
            for fp, file_nodes in nodes.items():
                ...
        """
        return self._queries.get_nodes_by_files_batch(file_paths)

    def get_files_summary(self) -> List[Dict]:
        """Get per-file summary (path, language, node counts, kind breakdown).

        Single SQL query — replaces the N+1 pattern of per-file node loading
        for file-listing UIs. Each dict has:
            path, language, node_count, size, modified_at, indexed_at,
            kinds (dict of kind->count, excluding 'file'), total_symbols

        Example:
            for f in cg.get_files_summary():
                print(f"{f['path']}: {f['total_symbols']} symbols {f['kinds']}")
        """
        return self._queries.get_files_summary()

    # Names too common for unresolved-ref fallback — require same-file evidence
    _COMMON_METHOD_NAMES = frozenset({
        '__init__', '__str__', '__repr__', '__new__', '__del__',
        '__call__', '__eq__', '__ne__', '__lt__', '__gt__',
        '__le__', '__ge__', '__hash__', '__len__', '__iter__',
        '__next__', '__enter__', '__exit__', '__getitem__',
        '__setitem__', '__delitem__', '__contains__', '__add__',
        '__sub__', '__mul__', '__truediv__', '__floordiv__',
        '__mod__', '__pow__', '__and__', '__or__', '__xor__',
        '__getattr__', '__setattr__', '__delattr__', '__format__',
    })

    def _resolve_to_definition(self, node_id: str) -> str:
        """If the given node is an import, route to the actual definition node.

        When a user queries callers for 'resolve_context_limit', the first
        node found might be the import node (from the calling file) rather
        than the function definition. This helper detects that and reroutes
        to the function/class/method node with the same name.
        """
        node = self._queries.get_node_by_id(node_id)
        if not node or node.kind != 'import':
            return node_id

        # Find definition nodes with the same name, prefer function/class/method
        candidates = self._queries.get_nodes_by_name(node.name)
        for c in candidates:
            if c.kind in ('function', 'class', 'method', 'constant', 'variable'):
                if c.file_path != node.file_path:
                    return c.id
        return node_id

    def get_callers(self, node_id: str, max_depth: int = 1) -> List[Tuple[Node, Edge]]:
        """Find what calls a function/method.

        Two-phase strategy:
          Phase 1 — edge-based (resolved calls/references edges) — reliable
          Phase 2 — unresolved reference name matching — fills cross-file gaps
        Both phases run and merge (dedup) for maximum coverage.
        """
        result: Dict[str, Tuple[Node, Edge]] = OrderedDict()

        # Auto-route: if node_id points to an import, redirect to definition
        node_id = self._resolve_to_definition(node_id)

        target = self._queries.get_node_by_id(node_id)
        if not target:
            return []

        target_name = target.name
        target_file = target.file_path
        is_common_name = target_name in self._COMMON_METHOD_NAMES

        # ── Phase 1: edge-based (resolved references) ──
        nodes = self._traverser.getCallers(node_id, max_depth)
        for n in nodes:
            edges = self._queries.get_outgoing_edges(n.id, ['calls', 'references'])
            for e in edges:
                if e.target == node_id and n.id not in result:
                    result[n.id] = (n, e)

        # ── Phase 2: supplement via UnresolvedReference name matching ──
        # Always runs as supplement — may add cross-file callers that
        # the resolution layer missed.
        refs = self._queries.get_unresolved_refs()
        seen: Set[str] = set(result.keys())
        for ref in refs:
            ref_name = ref.reference_name
            # Exact match, or last component of dotted name matches
            matched = ref_name == target_name or (
                '.' in ref_name and ref_name.rsplit('.', 1)[-1] == target_name
            )
            if not matched:
                continue

            # For very common names (__init__ etc.), require same-file
            # evidence to avoid false positives. Skip if file is unknown
            # or differs from the target's file.
            if is_common_name and (ref.file_path is None or ref.file_path != target_file):
                continue

            if ref.from_node_id in seen:
                continue

            caller = self._queries.get_node_by_id(ref.from_node_id)
            if caller and caller.id not in seen:
                seen.add(caller.id)
                result[caller.id] = (
                    caller,
                    Edge(
                        source=caller.id, target=node_id,
                        kind=ref.reference_kind or 'references',
                        line=ref.line, column=ref.column,
                        provenance='unresolved',
                    ),
                )

        return list(result.values())

    def get_callees(self, node_id: str, max_depth: int = 1) -> List[Tuple[Node, Edge]]:
        """Find what a function/method calls.

        Two-phase strategy (same as get_callers):
          Phase 1 — edge-based (resolved calls/references edges) — reliable
          Phase 2 — unresolved reference name matching — fills cross-file gaps
        Both phases run and merge (dedup) for maximum coverage.
        """
        result: Dict[str, Tuple[Node, Edge]] = OrderedDict()

        # Auto-route import→definition (same as get_callers)
        node_id = self._resolve_to_definition(node_id)

        source = self._queries.get_node_by_id(node_id)
        if not source:
            return []

        source_file = source.file_path

        # ── Phase 1: edge-based (resolved references) ──
        nodes = self._traverser.getCallees(node_id, max_depth)
        for n in nodes:
            edges = self._queries.get_incoming_edges(n.id, ['calls', 'references'])
            for e in edges:
                if e.source == node_id and n.id not in result:
                    result[n.id] = (n, e)

        # ── Phase 2: supplement via UnresolvedReference name matching ──
        refs = self._queries.get_unresolved_refs_by_from_node(node_id)
        seen: Set[str] = set(result.keys())
        for ref in refs:
            # Skip dotted/method references (e.g. "obj.get") — these are
            # method calls on objects, not function call references to
            # project-level symbols. Type inference is needed to resolve
            # them correctly, so don't produce noise by falling back to
            # matching just the method name.
            if '.' in ref.reference_name:
                continue

            # Try to resolve the referenced name to a real node
            callees_found = self._queries.get_nodes_by_name(ref.reference_name)

            for callee in callees_found:
                if callee.id not in seen:
                    seen.add(callee.id)
                    result[callee.id] = (
                        callee,
                        Edge(
                            source=node_id, target=callee.id,
                            kind=ref.reference_kind or 'references',
                            line=ref.line, column=ref.column,
                            provenance='unresolved',
                        ),
                    )

        return list(result.values())

    def get_callers_batch(
        self, node_ids: List[str], max_depth: int = 1
    ) -> Dict[str, List[Tuple[Node, Edge]]]:
        """Find callers for multiple nodes at once.

        Uses batched edge queries to find incoming edges for all target
        nodes in a single DB query, then loads unresolved references once
        for the entire batch as fallback.

        Returns:
            Dict mapping node_id -> List[(caller_node, edge)]
        """
        # Auto-route import nodes to their definitions
        resolved_ids = [self._resolve_to_definition(nid) for nid in node_ids]
        # Map original nid → resolved nid, and build deduped set of targets
        id_map: Dict[str, str] = {}
        for orig, resolved in zip(node_ids, resolved_ids):
            id_map[orig] = resolved

        result: Dict[str, List[Tuple[Node, Edge]]] = {nid: [] for nid in node_ids}
        seen_per_node: Dict[str, Set[str]] = {nid: set() for nid in node_ids}

        # ── Phase 1: batched incoming edges (resolved references) ──
        # Use unique resolved IDs to avoid duplicate queries
        unique_ids = list(set(resolved_ids))
        incoming = self._queries.get_incoming_edges_batch(
            unique_ids, kinds=['calls', 'references']
        )
        for nid in node_ids:
            rid = id_map.get(nid, nid)
            for e in incoming.get(rid, []):
                caller = self._queries.get_node_by_id(e.source)
                if caller and caller.id not in seen_per_node[nid]:
                    seen_per_node[nid].add(caller.id)
                    result[nid].append((caller, e))

        # ── Phase 2: supplement via unresolved references ──
        # Always runs to catch cross-file callers the resolver missed.
        # For common names (__init__ etc.) requires same-file evidence.
        target_info: Dict[str, Tuple[str, str]] = {}
        for nid in node_ids:
            rid = id_map.get(nid, nid)
            target = self._queries.get_node_by_id(rid)
            if target:
                target_info[nid] = (target.name, target.file_path)

        if target_info:
            refs = self._queries.get_unresolved_refs()
            # Build name→node_ids index for fast matching
            name_nid_map: Dict[str, List[Tuple[str, str, str]]] = {}
            for nid, (tname, tfile) in target_info.items():
                name_nid_map.setdefault(tname, []).append((nid, tname, tfile))

            for ref in refs:
                ref_name = ref.reference_name
                matched = name_nid_map.get(ref_name, [])
                if not matched and '.' in ref_name:
                    simple = ref_name.rsplit('.', 1)[-1]
                    matched = name_nid_map.get(simple, [])

                for matched_nid, matched_name, matched_file in matched:
                    if ref.from_node_id in seen_per_node[matched_nid]:
                        continue
                    # Common name check: require same-file evidence
                    if matched_name in self._COMMON_METHOD_NAMES:
                        if ref.file_path and ref.file_path != matched_file:
                            continue
                    caller = self._queries.get_node_by_id(ref.from_node_id)
                    if caller and caller.id not in seen_per_node[matched_nid]:
                        seen_per_node[matched_nid].add(caller.id)
                        fake_edge = Edge(
                            source=caller.id, target=matched_nid,
                            kind=ref.reference_kind or 'references',
                            line=ref.line, column=ref.column,
                            provenance='unresolved',
                        )
                        result[matched_nid].append((caller, fake_edge))

        return result

    def get_callees_batch(
        self, node_ids: List[str], max_depth: int = 1
    ) -> Dict[str, List[Tuple[Node, Edge]]]:
        """Find callees for multiple nodes at once.

        Uses batched edge queries to find outgoing edges for all source
        nodes in a single DB query. Fallback preloads name lookups to
        avoid N+1 queries.

        Returns:
            Dict mapping node_id -> List[(callee_node, edge)]
        """
        result: Dict[str, List[Tuple[Node, Edge]]] = {nid: [] for nid in node_ids}

        # ── Phase 1: batched outgoing edges (resolved references) ──
        outgoing = self._queries.get_outgoing_edges_batch(
            node_ids, kinds=['calls', 'references']
        )
        for node_id in node_ids:
            for e in outgoing.get(node_id, []):
                callee = self._queries.get_node_by_id(e.target)
                if callee:
                    result[node_id].append((callee, e))

        # ── Phase 2: unresolved reference fallback ──
        needs_fallback = [nid for nid in node_ids if not result[nid]]
        if needs_fallback:
            # Collect all refs for needed nodes
            from codegraph.types import UnresolvedReference
            all_refs: List[UnresolvedReference] = []
            for nid in needs_fallback:
                all_refs.extend(self._queries.get_unresolved_refs_by_from_node(nid))

            if all_refs:
                # Preload ALL referenced names in one pass to avoid N+1 queries
                all_names: Set[str] = set()
                for ref in all_refs:
                    # Skip dotted/method references — they are method calls
                    # on objects, not references to project-level symbols.
                    if '.' in ref.reference_name:
                        continue
                    all_names.add(ref.reference_name)

                name_cache: Dict[str, List[Node]] = {}
                for name in all_names:
                    name_cache[name] = self._queries.get_nodes_by_name(name)

                seen: Dict[str, Set[str]] = {nid: set() for nid in needs_fallback}
                for ref in all_refs:
                    if '.' in ref.reference_name:
                        continue
                    callees_found = name_cache.get(ref.reference_name, [])

                    for callee in callees_found:
                        if callee.id not in seen[ref.from_node_id]:
                            seen[ref.from_node_id].add(callee.id)
                            fake_edge = Edge(
                                source=ref.from_node_id, target=callee.id,
                                kind=ref.reference_kind or 'references',
                                line=ref.line, column=ref.column,
                                provenance='unresolved',
                            )
                            result[ref.from_node_id].append((callee, fake_edge))

        return result

    def explore_nodes(
        self,
        query: str,
        options: Optional[SearchOptions] = None,
        call_depth: int = 1,
    ) -> ExploreResult:
        """Search + batch caller/callee lookup in a single call.

        Combines symbol search with caller and callee analysis for all
        matched nodes. Uses batch queries internally for efficiency.

        This is the primary entry point for explore-mode tooling.
        Equivalent to calling search_nodes() then get_callers()/get_callees()
        for each result, but significantly faster due to batching.

        Args:
            query: Search query string
            options: Search options (limit, kind, etc.)
            call_depth: Depth for caller/callee traversal (default 1)

        Returns:
            ExploreResult with search_results + callers + callees
        """
        opts = options or SearchOptions()
        results = self._queries.search_nodes(query, opts)

        if not results:
            return ExploreResult()

        node_ids = [r.node.id for r in results]

        # Batch caller + callee lookup with graceful degradation
        callers: Dict[str, List[Tuple[Node, Edge]]] = {}
        callees: Dict[str, List[Tuple[Node, Edge]]] = {}

        try:
            callers = self.get_callers_batch(node_ids, max_depth=call_depth)
        except Exception as e:
            logger.warning(
                'explore_nodes: get_callers_batch failed (%s), '
                'falling back to search-only results', str(e)
            )

        try:
            callees = self.get_callees_batch(node_ids, max_depth=call_depth)
        except Exception as e:
            logger.warning(
                'explore_nodes: get_callees_batch failed (%s), '
                'falling back to search-only results', str(e)
            )

        return ExploreResult(
            search_results=results,
            callers=callers,
            callees=callees,
        )

    def get_impact_radius(self, node_id: str, max_depth: int = 3) -> Subgraph:
        """Analyze what code is affected by changing a symbol."""
        tr = self._traverser.getImpactRadius(node_id, radius=max_depth)
        sub = Subgraph()
        for n in tr.nodes:
            sub.nodes[n.id] = n
        sub.edges = tr.edges
        sub.roots = [node_id]
        sub.confidence = 'high'
        return sub

    def get_context(self, node_id: str, depth: int = 2) -> 'Context':
        """Get context for a node."""
        return self._context_builder.build_context(node_id, depth)

    def get_call_graph(self, node_id: str, depth: int = 2) -> Subgraph:
        """Get the call graph for a function."""
        tr = self._traverser.bfs_traverse(
            node_id, max_depth=depth,
            edge_kinds=['calls', 'references'],
            direction='both'
        )
        sub = Subgraph()
        for n in tr.nodes:
            sub.nodes[n.id] = n
        sub.edges = tr.edges
        sub.roots = [node_id]
        sub.confidence = 'high'
        return sub

    def find_path(self, from_id: str, to_id: str,
                  edge_kinds: Optional[List[str]] = None) -> List[Edge]:
        """Find a path between two nodes."""
        result = self._traverser.findPath(from_id, to_id, edge_kinds)
        return result.edges if hasattr(result, 'edges') else []

    def get_type_hierarchy(self, node_id: str) -> Tuple[List[Node], List[Node]]:
        """Get the type hierarchy (ancestors, descendants)."""
        hierarchy = self._traverser.getTypeHierarchy(node_id)
        return hierarchy.get('super', []), hierarchy.get('sub', [])

    def get_file_dependencies(self, file_path: str) -> List[Node]:
        """Get file dependencies (other files this file imports)."""
        deps = self._graph_manager.getFileDependencies(file_path)
        # Flatten the dependency dict
        result = []
        for dep_list in deps.values():
            for dep in dep_list:
                if hasattr(dep, 'file_path') and dep.file_path:
                    nodes = self._queries.get_nodes_by_file(dep.file_path)
                    result.extend(nodes)
        return result

    def get_file_dependents(self, file_path: str) -> List[Node]:
        """Get files that depend on this file."""
        nodes = self._queries.get_nodes_by_file(file_path)
        dependents = []
        for n in nodes:
            edges = self._queries.get_incoming_edges(n.id)
            for e in edges:
                source = self._queries.get_node_by_id(e.source)
                if source and source.file_path != file_path:
                    dependents.append(source)
        return dependents

    def find_circular_dependencies(self) -> List[List[str]]:
        """Find circular dependencies between files."""
        return self._graph_manager.findCircularDependencies()

    def find_dead_code(self, kinds: Optional[List[str]] = None) -> List[Node]:
        """Find potentially dead code (nodes with no incoming references)."""
        dead = self._graph_manager.findDeadCode()
        result = []
        for nodes in dead.values():
            if kinds:
                nodes = [n for n in nodes if n.kind in kinds]
            result.extend(nodes)
        return result

    def get_exported_symbols(self, file_path: str) -> List[Node]:
        """Get exported symbols from a file."""
        return self._graph_manager.getExportedSymbols(file_path)

    def build_task_context(self, query: str,
                            options: Optional[BuildContextOptions] = None) -> TaskContext:
        """Build context for a task."""
        return self._context_builder.build_task_context(query, options)

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> GraphStats:
        """Get graph statistics."""
        return self._queries.get_stats()

    def get_backend(self) -> str:
        """Get the database backend name."""
        return 'sqlite3'

    def get_journal_mode(self) -> Optional[str]:
        """Get the effective journal mode."""
        return self._db.get_journal_mode()

    def get_index_state(self) -> Optional[str]:
        """Get the index state metadata."""
        return self._queries.get_metadata('index_state')

    def get_last_indexed_at(self) -> Optional[int]:
        """Get timestamp of last successful index."""
        val = self._queries.get_metadata('last_indexed_at')
        return int(val) if val else None

    def get_index_build_info(self) -> Dict[str, Optional[str]]:
        """Get build info about the index."""
        return {
            'version': self._queries.get_metadata('indexed_with_version'),
            'extraction_version': self._queries.get_metadata('indexed_with_extraction_version'),
        }

    def is_index_stale(self) -> bool:
        """Check if the index was built by an older engine version."""
        build_ver = self._queries.get_metadata('indexed_with_extraction_version')
        if not build_ver:
            return True
        try:
            from codegraph.extraction import EXTRACTION_VERSION
            return int(build_ver) < EXTRACTION_VERSION
        except Exception:
            return False

    def get_pending_reference_count(self) -> int:
        """Get count of unresolved references."""
        return self._queries.get_unresolved_refs_count()

    def get_changed_files(self) -> Dict[str, List[str]]:
        """Get lists of changed files since last index.

        Uses detailed file scan to collect mtime during directory walk,
        eliminating per-file stat() calls.
        """
        existing = set(self._queries.get_all_file_paths())
        scanned = self._orchestrator.scan_files_with_details()
        current_files = {d.path for d in scanned}
        mtime_map = {d.path: d.mtime_s for d in scanned}

        added = list(current_files - existing)
        removed = list(existing - current_files)

        modified = []
        for fp in (current_files & existing):
            rec = self._queries.get_file(fp)
            if rec and fp in mtime_map:
                current_mtime_ms = int(mtime_map[fp] * 1000)
                if current_mtime_ms > rec.modified_at:
                    modified.append(fp)

        return {
            'added': added,
            'modified': modified,
            'removed': removed,
        }

    # =========================================================================
    # Segment Matching (for prompt hooks)
    # =========================================================================

    def get_segment_matches(self, prompt_words: List[str]) -> List[SegmentMatch]:
        """Find symbol names that match prose words from a prompt."""

        matches: List[SegmentMatch] = []
        seen: Set[str] = set()

        for word in prompt_words:
            word_lower = word.lower()
            if len(word_lower) < 2:
                continue

            # Look up in segment vocabulary
            names = self._queries.get_names_by_segment(word_lower)
            for name in names:
                if name in seen:
                    continue
                seen.add(name)

                # Verify the symbol exists in the graph
                nodes = self._queries.get_nodes_by_name(name)
                for node in nodes:
                    matches.append(SegmentMatch(
                        name=name,
                        kind=node.kind,
                        file_path=node.file_path,
                        start_line=node.start_line,
                        matched_words=[word_lower],
                    ))

        return matches

    # =========================================================================
    # File Watching
    # =========================================================================

    def watch(self, on_change: Optional[Callable[[List[PendingFile]], None]] = None,
              options: Optional[WatchOptions] = None) -> FileWatcher:
        """Start watching the project for file changes."""
        if self._watcher:
            self._watcher.stop()

        self._watcher = FileWatcher(
            self._project_root,
            on_change=on_change or self._on_watch_change,
            options=options,
        )
        self._watcher.start()
        return self._watcher

    def unwatch(self) -> None:
        """Stop watching for file changes."""
        if self._watcher:
            self._watcher.stop()
            self._watcher = None

    def _on_watch_change(self, pending: List[PendingFile]) -> None:
        """Default handler for file changes - triggers a sync."""
        try:
            import asyncio
            asyncio.run(self.sync())
        except Exception:
            pass

    # =========================================================================
    # Internal
    # =========================================================================

    def _resolve_references(self, file_filter: Optional[List[str]] = None) -> None:
        """Resolve all pending unresolved references.

        Args:
            file_filter: If provided, only resolve refs from these files.
                         Passed through to ReferenceResolver.resolve_all().
        """
        result = self._resolver.resolve_all(file_paths=file_filter)
        if result.resolved_count > 0:
            self._resolver.resolve_chained_calls_via_conformance()
            self._resolver.resolve_deferred_this_member_refs()
