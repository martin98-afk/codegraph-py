"""
CodeGraph Resolution Module

Reference resolver for connecting symbol usages to their definitions.
Strategy:
  Phase 1 — Build import map (module name → project files)
  Phase 2 — Resolve import edges (module → file)
  Phase 3 — Resolve call references via import-aware name matching
  Phase 4 — Fallback to global name matching
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any, Callable
from dataclasses import dataclass, field

from codegraph.types import Node, Edge, UnresolvedReference
from codegraph.db.queries import QueryBuilder


@dataclass
class ResolutionResult:
    """Result of a resolution pass."""
    resolved_count: int = 0
    remaining_count: int = 0
    edges_created: int = 0


@dataclass
class ImportEntry:
    """A single import entry for a file."""
    name: str                    # imported name (e.g. 'join')
    qualified_name: str          # full qualified (e.g. 'os.path.join')
    module: str                  # module part (e.g. 'os.path')
    node_id: str                 # import node ID in graph


class ReferenceResolver:
    """Resolves unresolved references by matching names to defined symbols."""

    def __init__(self, project_root: str, queries: QueryBuilder):
        self._project_root = project_root
        self._queries = queries
        self._initialized = False

        # Import map: {file_path: {imported_name: [ImportEntry, ...]}}
        self._import_map: Dict[str, Dict[str, List[ImportEntry]]] = {}
        # Module registry: {module_name: [file_path, ...]}
        self._module_registry: Dict[str, List[str]] = {}
        # Node name cache: {name: [Node, ...]} for fast lookup
        self._name_cache: Dict[str, List[Node]] = {}

    # =========================================================================
    # Initialization — build import maps & registries
    # =========================================================================

    def initialize(self) -> None:
        """Build import maps, module registry, and name cache.

        清空已有状态再重建，避免多次调用时 import_map / module_registry
        不断累积重复条目导致内存泄漏。
        """
        self._import_map.clear()
        self._module_registry.clear()
        self._build_import_map()
        self._build_module_registry()
        self._build_name_cache()
        self._initialized = True

    def _build_import_map(self) -> None:
        """Scan all import nodes to build per-file import maps."""
        import_nodes = self._queries.get_nodes_by_kind('import')
        for node in import_nodes:
            fp = node.file_path
            if fp not in self._import_map:
                self._import_map[fp] = {}

            name = node.name
            qualified = node.qualified_name

            # Determine the module part from qualified_name
            if '.' in qualified:
                module = qualified.rsplit('.', 1)[0]
            else:
                module = qualified

            entry = ImportEntry(
                name=name,
                qualified_name=qualified,
                module=module,
                node_id=node.id,
            )
            self._import_map[fp].setdefault(name, []).append(entry)

    def _build_module_registry(self) -> None:
        """Build a mapping from module names to project files."""
        all_files = self._queries.get_all_files()

        for f in all_files:
            path = f.path
            # Convert file path to Python module name
            if path.endswith('.py'):
                if path.endswith('__init__.py'):
                    module = path[:-len('__init__.py')].rstrip('/').replace('/', '.')
                else:
                    module = path[:-3].replace('/', '.')
            elif path.endswith('.ts') or path.endswith('.tsx'):
                module = path.rsplit('.', 1)[0].replace('/', '.')
            elif path.endswith('.js') or path.endswith('.jsx'):
                module = path.rsplit('.', 1)[0].replace('/', '.')
            else:
                module = path.rsplit('.', 1)[0].replace('/', '.') if '.' in path else path

            self._module_registry.setdefault(module, []).append(path)

            # Also register parent modules
            parts = module.split('.')
            for i in range(1, len(parts)):
                parent = '.'.join(parts[:i])
                if parent not in self._module_registry:
                    self._module_registry[parent] = []

    def _build_name_cache(self) -> None:
        """Build a cache of all nodes by name for fast lookup."""
        # This is too expensive to load all nodes — use DB queries instead
        pass

    # =========================================================================
    # Main resolution pipeline
    # =========================================================================

    def resolve_all(self, on_progress: Optional[Callable] = None,
                    file_paths: Optional[List[str]] = None) -> ResolutionResult:
        """Resolve pending unresolved references.

        Args:
            on_progress: Optional progress callback
            file_paths: If provided, only resolve refs from these files.
                        Otherwise resolve ALL refs from all files.
        """
        if not self._initialized:
            self.initialize()

        if file_paths:
            refs = self._queries.get_unresolved_refs_by_files(file_paths)
        else:
            refs = self._queries.get_unresolved_refs()

        if not refs:
            return ResolutionResult()

        resolved = 0
        edges_created = 0
        total = len(refs)

        for i, ref in enumerate(refs):
            target_node = self._resolve_single(ref)
            if target_node:
                edge_kind = self._ref_kind_to_edge_kind(ref.reference_kind)
                edge = Edge(
                    source=ref.from_node_id,
                    target=target_node.id,
                    kind=edge_kind,
                    line=ref.line,
                    column=ref.column,
                    provenance='resolved',
                )
                self._queries.insert_edge(edge)
                self._queries._db.execute(
                    'DELETE FROM unresolved_refs WHERE id = ?', (ref.id,)
                )
                resolved += 1
                edges_created += 1

            if on_progress and (i % 50 == 0 or i == total - 1):
                on_progress(i + 1, total)

        return ResolutionResult(
            resolved_count=resolved,
            remaining_count=self._queries.get_unresolved_refs_count(),
            edges_created=edges_created,
        )

    def _resolve_single(self, ref: UnresolvedReference) -> Optional[Node]:
        """
        Resolve a single reference using multi-strategy approach.

        Strategies (in order):
          1. Import-aware: ref's source file imported this name
          2. Same-file: definition exists in same file as ref
          3. Global: name matches a node anywhere in project
          4. Qualified: candidates list has a match
          5. Module-path: reference_name is a module that maps to a file
        """
        name = ref.reference_name
        source_node = self._queries.get_node_by_id(ref.from_node_id)
        source_file = source_node.file_path if source_node else None

        # ── Strategy 1: Import-aware resolution ──
        if source_file and source_file in self._import_map:
            target = self._resolve_via_imports(name, source_file)
            if target:
                return target

        # ── Strategy 2: Same-file resolution ──
        if source_file:
            target = self._resolve_in_file(name, source_file)
            if target:
                return target

        # ── Strategy 3: Module name → file node ──
        # Try to resolve the reference as a module name (before global
        # name matching, since module refs like 'utils' are common)
        file_paths = self._module_registry.get(name, [])
        if file_paths:
            for fp in file_paths:
                file_nodes = self._queries.get_nodes_by_file(fp)
                for n in file_nodes:
                    if n.kind == 'file':
                        return n
            # If no file node found, the module exists but wasn't indexed
            # as a file node — still try to use its path
            return None

        # ── Strategy 4: Global name matching ──
        target = self._resolve_global(name, source_file)
        if target:
            return target

        # ── Strategy 5: Qualified name candidates ──
        if ref.candidates:
            for cname in ref.candidates:
                matches = self._queries.get_nodes_by_qualified_name(cname)
                if matches:
                    return matches[0]

        return None

    # =========================================================================
    # Resolution strategies
    # =========================================================================

    def _resolve_via_imports(self, name: str, source_file: str) -> Optional[Node]:
        """Check if the source file imports 'name' from somewhere, and resolve it."""
        file_imports = self._import_map.get(source_file, {})
        entries = file_imports.get(name, [])

        for entry in entries:
            # Try to find the symbol in the imported module's files
            mod_paths = self._module_registry.get(entry.module, [])
            for fp in mod_paths:
                nodes = self._queries.get_nodes_by_file(fp)
                for n in nodes:
                    if n.name == name:
                        return n

            # Also check the import node's qualified name
            qname_candidates = self._queries.get_nodes_by_qualified_name(entry.qualified_name)
            if qname_candidates:
                return qname_candidates[0]

            # Last resort: look for the module as a file
            for fp in mod_paths:
                file_nodes = self._queries.get_nodes_by_file(fp)
                for n in file_nodes:
                    if n.kind == 'file':
                        return n

        return None

    def _resolve_in_file(self, name: str, file_path: str) -> Optional[Node]:
        """Look for a definition of 'name' in the same file."""
        nodes = self._queries.get_nodes_by_file(file_path)
        for n in nodes:
            if n.name == name and n.kind not in ('file', 'import', 'module'):
                return n
        return None

    def _resolve_global(self, name: str, source_file: Optional[str]) -> Optional[Node]:
        """Global name matching across all indexed nodes."""
        candidates = self._queries.get_nodes_by_name(name)

        # Filter out file and import nodes
        real_candidates = [
            c for c in candidates
            if c.kind not in ('file', 'import', 'module')
        ]

        if not real_candidates:
            return None

        # Prefer same file results
        if source_file:
            same_file = [c for c in real_candidates if c.file_path == source_file]
            if same_file:
                return same_file[0]

        # Prefer exported/non-private symbols
        exported = [c for c in real_candidates if c.is_exported or not c.name.startswith('_')]
        if exported:
            return exported[0]

        return real_candidates[0]

    # =========================================================================
    # Post-processing passes (stubs for future enhancements)
    # =========================================================================

    def resolve_chained_calls_via_conformance(self) -> None:
        """Second pass: chained calls whose method lives on a supertype."""
        pass

    def resolve_deferred_this_member_refs(self) -> None:
        """Resolve 'this.<member>' callback registrations inherited from supertype."""
        pass

    def run_post_extract(self) -> None:
        """Run cross-file finalization after extraction."""
        pass

    # =========================================================================
    # Helpers
    # =========================================================================

    def _ref_kind_to_edge_kind(self, ref_kind: str) -> str:
        """Convert a reference kind to an edge kind."""
        mapping = {
            'calls': 'calls',
            'call': 'calls',
            'imports': 'imports',
            'exports': 'exports',
            'extends': 'extends',
            'implements': 'implements',
            'type_of': 'type_of',
            'returns': 'returns',
            'instantiates': 'instantiates',
            'overrides': 'overrides',
            'decorates': 'decorates',
            'function_ref': 'references',
        }
        return mapping.get(ref_kind, 'references')

    def get_import_map(self) -> Dict[str, Dict[str, List[ImportEntry]]]:
        """Get the import map (for debugging/inspection)."""
        return dict(self._import_map)

    def get_module_registry(self) -> Dict[str, List[str]]:
        """Get the module registry (for debugging/inspection)."""
        return dict(self._module_registry)


def create_resolver(project_root: str, queries: QueryBuilder) -> ReferenceResolver:
    """Factory function to create a ReferenceResolver."""
    return ReferenceResolver(project_root, queries)
