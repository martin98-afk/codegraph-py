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

__version__ = "1.0.0"


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
                   verbose: bool = False) -> IndexResult:
        """Index all files in the project."""
        start_time = time.time()

        # Use the orchestrator's sync method which does a full scan + index
        sync_result = self._orchestrator.sync(force=False)

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
        """Sync changes since last index."""
        start_time = time.time()

        # Get existing files
        existing = set(self._queries.get_all_file_paths())

        # Scan current files
        current_files = set(self._orchestrator.scan_files())

        added = list(current_files - existing)
        removed = list(existing - current_files)

        # Check for modified files
        modified = []
        for fp in (current_files & existing):
            rec = self._queries.get_file(fp)
            if rec:
                filepath = os.path.join(self._project_root, fp)
                if os.path.isfile(filepath):
                    current_mtime = int(os.path.getmtime(filepath) * 1000)
                    if current_mtime > rec.modified_at:
                        modified.append(fp)

        # Index changed files
        nodes_updated = 0
        for fp in added + modified:
            file_result = self._orchestrator.index_file(fp)
            if file_result.success:
                nodes_updated += file_result.nodes_count

        # Remove deleted files
        for fp in removed:
            self._queries.delete_file(fp)
            self._queries.delete_nodes_by_file(fp)

        result = SyncResult(
            files_checked=len(current_files),
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
        """Find what calls a function/method."""
        nodes = self._traverser.getCallers(node_id, max_depth > 1)
        result = []
        for n in nodes:
            edges = self._queries.get_incoming_edges(n.id, ['calls', 'references'])
            for e in edges:
                if e.source == n.id:
                    result.append((n, e))
        return result

    def get_callees(self, node_id: str, max_depth: int = 1) -> List[Tuple[Node, Edge]]:
        """Find what a function/method calls."""
        nodes = self._traverser.getCallees(node_id, max_depth > 1)
        result = []
        for n in nodes:
            edges = self._queries.get_outgoing_edges(n.id, ['calls', 'references'])
            for e in edges:
                if e.target == n.id:
                    result.append((n, e))
        return result

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
        """Get lists of changed files since last index."""
        added, modified, removed = self._orchestrator.detect_changes()
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

    async def _resolve_references_batched(self) -> None:
        """Resolve all pending references in batches."""
        result = self._resolver.resolve_all()
        if result.resolved_count > 0:
            self._resolver.resolve_chained_calls_via_conformance()
            self._resolver.resolve_deferred_this_member_refs()
