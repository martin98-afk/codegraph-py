"""
CodeGraph Main Class

Primary interface for interacting with the code knowledge graph.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Callable, Any

from codegraph.types import (
    Node, Edge, Subgraph, GraphStats, TaskContext,
    SearchResult, SearchOptions, TraversalOptions,
    SegmentMatch, BuildContextOptions, FileRecord,
    ExploreResult,
)
from codegraph.db import DatabaseConnection, QueryBuilder, get_database_path
from codegraph.directory import (
    get_codegraph_dir, is_initialized, create_directory,
    remove_directory, validate_directory, find_nearest_codegraph_root,
    derive_project_name_tokens,
)
from codegraph.errors import CodeGraphError
from codegraph.extraction import (
    ExtractionOrchestrator, IndexProgress,
)
from codegraph.resolution import ReferenceResolver, create_resolver
from codegraph.graph import GraphTraverser, GraphQueryManager
from codegraph.context import ContextBuilder, create_context_builder
from codegraph.sync import FileWatcher, WatchOptions, PendingFile

__version__ = "1.1.1"


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

        # Re-resolve references after partial re-index
        if total_nodes > 0:
            ref_count = self._queries.get_unresolved_refs_count()
            if ref_count > 0:
                self._resolver.initialize()
                self._resolve_references()

        return IndexResult(
            success=len(errors) == 0,
            files_indexed=len(file_paths) - len(errors),
            files_errored=len(errors),
            nodes_created=total_nodes,
            edges_created=total_edges,
            duration_ms=(time.time() - start_time) * 1000,
            errors=errors,
        )

    def sync(self, on_progress=None) -> SyncResult:
        """Sync changes since last index.

        Uses detailed file scan to collect mtime during directory walk,
        eliminating per-file stat() calls for faster change detection.
        """
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
        failed_files = []
        for fp in added + modified:
            try:
                file_result = self._orchestrator.index_file(fp)
                if file_result.success:
                    nodes_updated += file_result.nodes_count
                else:
                    failed_files.append(fp)
            except Exception as e:
                failed_files.append(fp)
                # Log but don't crash — sync should be resilient
                import logging
                logging.getLogger(__name__).warning(
                    'sync: index_file failed for %s: %s', fp, str(e)
                )

        # Remove deleted files
        for fp in removed:
            try:
                self._queries.delete_file(fp)
                self._queries.delete_nodes_by_file(fp)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    'sync: cleanup failed for %s: %s', fp, str(e)
                )

        # Re-resolve references if nodes were updated
        if nodes_updated > 0:
            try:
                ref_count = self._queries.get_unresolved_refs_count()
                if ref_count > 0:
                    self._resolver.initialize()
                    self._resolve_references()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    'sync: reference resolution failed: %s', str(e)
                )

        result = SyncResult(
            files_checked=len(current_paths),
            files_added=len(added),
            files_modified=len(modified),
            files_removed=len(removed),
            nodes_updated=nodes_updated,
            duration_ms=(time.time() - start_time) * 1000,
            changed_file_paths=added + modified,
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

    def get_callers(self, node_id: str, max_depth: int = 1) -> List[Tuple[Node, Edge]]:
        """Find what calls a function/method.

        First tries edge-based lookup (resolved references), then falls back
        to name-based matching on unresolved references for maximum coverage.
        """
        result: List[Tuple[Node, Edge]] = []

        # ── Phase 1: edge-based (resolved references) ──
        nodes = self._traverser.getCallers(node_id, max_depth)
        for n in nodes:
            edges = self._queries.get_outgoing_edges(n.id, ['calls', 'references'])
            for e in edges:
                if e.target == node_id:
                    result.append((n, e))

        if result:
            return result

        # ── Phase 2: fallback via UnresolvedReference name matching ──
        target = self._queries.get_node_by_id(node_id)
        if target:
            target_name = target.name
            # Collect all refs whose name matches the target name (or any dotted suffix)
            refs = self._queries.get_unresolved_refs()
            seen: Set[str] = set()
            for ref in refs:
                ref_name = ref.reference_name
                # Exact match, or last component of dotted name matches
                if ref_name == target_name or (
                    '.' in ref_name and ref_name.rsplit('.', 1)[-1] == target_name
                ):
                    caller = self._queries.get_node_by_id(ref.from_node_id)
                    if caller and caller.id not in seen:
                        seen.add(caller.id)
                        fake_edge = Edge(
                            source=caller.id, target=node_id,
                            kind=ref.reference_kind or 'references',
                            line=ref.line, column=ref.column,
                            provenance='unresolved',
                        )
                        result.append((caller, fake_edge))

        return result

    def get_callees(self, node_id: str, max_depth: int = 1) -> List[Tuple[Node, Edge]]:
        """Find what a function/method calls.

        First tries edge-based lookup (resolved references), then falls back
        to name-based matching on unresolved references for maximum coverage.
        """
        result: List[Tuple[Node, Edge]] = []

        # ── Phase 1: edge-based (resolved references) ──
        nodes = self._traverser.getCallees(node_id, max_depth)
        for n in nodes:
            edges = self._queries.get_incoming_edges(n.id, ['calls', 'references'])
            for e in edges:
                if e.source == node_id:
                    result.append((n, e))

        if result:
            return result

        # ── Phase 2: fallback via UnresolvedReference name matching ──
        source = self._queries.get_node_by_id(node_id)
        if source:
            refs = self._queries.get_unresolved_refs_by_from_node(node_id)
            seen: Set[str] = set()
            for ref in refs:
                # Try to resolve the referenced name to a real node
                callees_found = self._queries.get_nodes_by_name(ref.reference_name)
                # Also try last component of dotted names
                if not callees_found and '.' in ref.reference_name:
                    simple = ref.reference_name.rsplit('.', 1)[-1]
                    callees_found = self._queries.get_nodes_by_name(simple)

                for callee in callees_found:
                    if callee.id not in seen:
                        seen.add(callee.id)
                        fake_edge = Edge(
                            source=node_id, target=callee.id,
                            kind=ref.reference_kind or 'references',
                            line=ref.line, column=ref.column,
                            provenance='unresolved',
                        )
                        result.append((callee, fake_edge))

        return result

    def get_callers_batch(
        self, node_ids: List[str], max_depth: int = 1
    ) -> Dict[str, List[Tuple[Node, Edge]]]:
        """Find callers for multiple nodes at once.

        More efficient than calling get_callers() individually when many
        nodes need caller analysis. Loads unresolved references once
        for the entire batch.

        Returns:
            Dict mapping node_id -> List[(caller_node, edge)]
        """
        result: Dict[str, List[Tuple[Node, Edge]]] = {nid: [] for nid in node_ids}
        needs_fallback: Set[str] = set()
        target_names: Dict[str, str] = {}  # node_id -> node name

        # ── Phase 1: edge-based (resolved references) for each node ──
        for node_id in node_ids:
            nodes = self._traverser.getCallers(node_id, max_depth)
            for n in nodes:
                edges = self._queries.get_outgoing_edges(n.id, ['calls', 'references'])
                for e in edges:
                    if e.target == node_id:
                        result[node_id].append((n, e))

            if not result[node_id]:
                needs_fallback.add(node_id)
                target = self._queries.get_node_by_id(node_id)
                if target:
                    target_names[node_id] = target.name

        # ── Phase 2: unresolved reference fallback (once for all nodes) ──
        if needs_fallback and target_names:
            refs = self._queries.get_unresolved_refs()
            # Build name -> node_ids mapping for quick lookup
            name_to_ids: Dict[str, List[str]] = {}
            for nid, name in target_names.items():
                name_to_ids.setdefault(name, []).append(nid)

            seen: Dict[str, Set[str]] = {nid: set() for nid in needs_fallback}
            for ref in refs:
                ref_name = ref.reference_name
                # Check exact match
                matched_ids = name_to_ids.get(ref_name, [])
                # Check dotted suffix match
                if not matched_ids and '.' in ref_name:
                    simple = ref_name.rsplit('.', 1)[-1]
                    matched_ids = name_to_ids.get(simple, [])

                for matched_nid in matched_ids:
                    if matched_nid not in needs_fallback:
                        continue
                    caller = self._queries.get_node_by_id(ref.from_node_id)
                    if caller and caller.id not in seen[matched_nid]:
                        seen[matched_nid].add(caller.id)
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

        More efficient than calling get_callees() individually when many
        nodes need callee analysis.

        Returns:
            Dict mapping node_id -> List[(callee_node, edge)]
        """
        result: Dict[str, List[Tuple[Node, Edge]]] = {nid: [] for nid in node_ids}
        needs_fallback: Set[str] = set()

        # ── Phase 1: edge-based (resolved references) per node ──
        for node_id in node_ids:
            nodes = self._traverser.getCallees(node_id, max_depth)
            for n in nodes:
                edges = self._queries.get_incoming_edges(n.id, ['calls', 'references'])
                for e in edges:
                    if e.source == node_id:
                        result[node_id].append((n, e))

            if not result[node_id]:
                needs_fallback.add(node_id)

        # ── Phase 2: unresolved reference fallback (once for all nodes) ──
        if needs_fallback:
            # Load refs for all needed nodes in one batch query
            from codegraph.types import UnresolvedReference

            all_refs: List[UnresolvedReference] = []
            for nid in needs_fallback:
                all_refs.extend(self._queries.get_unresolved_refs_by_from_node(nid))

            seen: Dict[str, Set[str]] = {nid: set() for nid in needs_fallback}
            for ref in all_refs:
                ref_name = ref.reference_name
                # Try exact name match
                callees_found = self._queries.get_nodes_by_name(ref_name)
                # Try dotted suffix
                if not callees_found and '.' in ref_name:
                    simple = ref_name.rsplit('.', 1)[-1]
                    callees_found = self._queries.get_nodes_by_name(simple)

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

        # Batch caller + callee lookup
        callers = self.get_callers_batch(node_ids, max_depth=call_depth)
        callees = self.get_callees_batch(node_ids, max_depth=call_depth)

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
        from codegraph.types import Context
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
        from codegraph.search import extract_prose_candidates, split_identifier_segments

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

    def _resolve_references(self) -> None:
        """Resolve all pending unresolved references."""
        result = self._resolver.resolve_all()
        if result.resolved_count > 0:
            self._resolver.resolve_chained_calls_via_conformance()
            self._resolver.resolve_deferred_this_member_refs()
