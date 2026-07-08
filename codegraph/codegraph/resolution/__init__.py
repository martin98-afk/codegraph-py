"""
CodeGraph Resolution Module

Reference resolver for connecting symbol usages to their definitions.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass, field

from codegraph.types import Node, Edge, UnresolvedReference
from codegraph.db.queries import QueryBuilder


@dataclass
class ResolutionResult:
    """Result of a resolution pass."""
    resolved_count: int = 0
    remaining_count: int = 0
    edges_created: int = 0


class ReferenceResolver:
    """Resolves unresolved references by matching names to defined symbols."""

    def __init__(self, project_root: str, queries: QueryBuilder):
        self._project_root = project_root
        self._queries = queries
        self._initialized = False

    def initialize(self) -> None:
        """Initialize the resolver (load necessary data)."""
        self._initialized = True

    def resolve_all(self, on_progress=None) -> ResolutionResult:
        """Resolve all pending unresolved references."""
        refs = self._queries.get_unresolved_refs()
        if not refs:
            return ResolutionResult()

        resolved = 0
        edges_created = 0

        # Group refs by name for batch resolution
        refs_by_name: Dict[str, List[UnresolvedReference]] = {}
        for ref in refs:
            refs_by_name.setdefault(ref.reference_name, []).append(ref)

        total = len(refs_by_name)
        current = 0

        for name, name_refs in refs_by_name.items():
            # Try to find matching nodes
            candidates = self._queries.get_nodes_by_name(name)
            if not candidates:
                # Try qualified name
                for ref in name_refs:
                    if ref.candidates:
                        for candidate_name in ref.candidates:
                            matches = self._queries.get_nodes_by_qualified_name(candidate_name)
                            candidates.extend(matches)

            if candidates:
                for ref in name_refs:
                    for candidate in candidates:
                        # Create a reference edge
                        edge_kind = self._ref_kind_to_edge_kind(ref.reference_kind)
                        edge = Edge(
                            source=ref.from_node_id,
                            target=candidate.id,
                            kind=edge_kind,
                            line=ref.line,
                            column=ref.column,
                            provenance='heuristic',
                        )
                        self._queries.insert_edge(edge)
                        edges_created += 1

                # Remove resolved refs
                for ref in name_refs:
                    self._queries._db.execute(
                        'DELETE FROM unresolved_refs WHERE id = ?', (ref.id,)
                    )
                resolved += len(name_refs)

            current += 1
            if on_progress:
                on_progress(current, total)

        return ResolutionResult(
            resolved_count=resolved,
            remaining_count=self._queries.get_unresolved_refs_count(),
            edges_created=edges_created,
        )

    def resolve_chained_calls_via_conformance(self) -> None:
        """Second pass: chained calls whose method lives on a supertype."""
        # Stub for protocol-extension / inherited / default-interface resolution
        pass

    def resolve_deferred_this_member_refs(self) -> None:
        """Resolve 'this.<member>' callback registrations inherited from supertype."""
        pass

    def run_post_extract(self) -> None:
        """Run cross-file finalization after extraction."""
        pass

    def _ref_kind_to_edge_kind(self, ref_kind: str) -> str:
        """Convert a reference kind to an edge kind."""
        mapping = {
            'calls': 'calls',
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


def create_resolver(project_root: str, queries: QueryBuilder) -> ReferenceResolver:
    """Factory function to create a ReferenceResolver."""
    return ReferenceResolver(project_root, queries)
