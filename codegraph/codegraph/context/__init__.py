"""
CodeGraph Context Builder

Builds rich context for AI agents to understand code.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Any, Tuple

from codegraph.types import (
    Node, Edge, Subgraph, Context, CodeBlock, TaskContext,
    BuildContextOptions, SearchOptions,
)
from codegraph.db.queries import QueryBuilder
from codegraph.graph import GraphTraverser


class ContextBuilder:
    """Builds rich code context for AI agents."""

    def __init__(self, project_root: str, queries: QueryBuilder,
                 traverser: GraphTraverser):
        self._project_root = project_root
        self._queries = queries
        self._traverser = traverser

    def build_context(self, node_id: str, depth: int = 2) -> Context:
        """Build full context around a focal node."""
        focal = self._queries.get_node_by_id(node_id)
        if not focal:
            return Context()

        ctx = Context(focal=focal)

        # Get ancestors
        ancestors = self._traverser.get_ancestors(node_id)
        ctx.ancestors = ancestors

        # Get children
        children = self._traverser.get_children(node_id)
        ctx.children = children

        # Get incoming references
        incoming_edges = self._queries.get_incoming_edges(node_id)
        ctx.incoming_refs = []
        for edge in incoming_edges:
            source = self._queries.get_node_by_id(edge.source)
            if source:
                ctx.incoming_refs.append({'node': source, 'edge': edge})

        # Get outgoing references
        outgoing_edges = self._queries.get_outgoing_edges(node_id)
        ctx.outgoing_refs = []
        for edge in outgoing_edges:
            target = self._queries.get_node_by_id(edge.target)
            if target:
                ctx.outgoing_refs.append({'node': target, 'edge': edge})

        # Collect types (extends/implements targets)
        for edge in outgoing_edges:
            if edge.kind in ('extends', 'implements', 'type_of', 'returns'):
                target = self._queries.get_node_by_id(edge.target)
                if target and target not in ctx.types:
                    ctx.types.append(target)

        # Collect imports
        for edge in outgoing_edges:
            if edge.kind == 'imports':
                target = self._queries.get_node_by_id(edge.target)
                if target:
                    ctx.imports.append(target)

        return ctx

    def build_task_context(self, query: str,
                           options: Optional[BuildContextOptions] = None) -> TaskContext:
        """Build context for a task query."""
        opts = options or BuildContextOptions()
        task_ctx = TaskContext(query=query)

        # Search for relevant nodes
        results = self._queries.search_nodes(query, SearchOptions(limit=opts.search_limit))
        entry_points = [r.node for r in results]
        task_ctx.entry_points = entry_points

        if not entry_points:
            return task_ctx

        # Build subgraph by traversing from entry points
        subgraph = Subgraph()
        visited_nodes: Set[str] = set()
        all_edges: List[Edge] = []

        for ep in entry_points:
            if ep.id not in visited_nodes:
                subgraph.nodes[ep.id] = ep
                visited_nodes.add(ep.id)

                # Traverse outgoing edges
                if opts.traversal_depth > 0:
                    edges = self._queries.get_outgoing_edges(ep.id)
                    for edge in edges:
                        all_edges.append(edge)
                        if edge.target not in visited_nodes:
                            target = self._queries.get_node_by_id(edge.target)
                            if target:
                                subgraph.nodes[edge.target] = target
                                visited_nodes.add(edge.target)

                # Traverse incoming edges
                edges_in = self._queries.get_incoming_edges(ep.id)
                for edge in edges_in:
                    all_edges.append(edge)

        # Deduplicate edges
        seen_edges: Set[Tuple[str, str, str]] = set()
        for edge in all_edges:
            key = (edge.source, edge.target, edge.kind)
            if key not in seen_edges:
                seen_edges.add(key)
                subgraph.edges.append(edge)

        subgraph.roots = [ep.id for ep in entry_points]
        subgraph.confidence = 'high' if entry_points else 'low'
        task_ctx.subgraph = subgraph

        # Generate code blocks from nodes
        if opts.include_code:
            code_blocks = []
            nodes_to_include = list(subgraph.nodes.values())[:opts.max_code_blocks]
            for node in nodes_to_include:
                block = self._read_code_block(node, opts.max_code_block_size)
                if block:
                    code_blocks.append(block)
            task_ctx.code_blocks = code_blocks

        # Collect related files
        files: Set[str] = set()
        for node in subgraph.nodes.values():
            files.add(node.file_path)
        task_ctx.related_files = list(files)[:50]

        # Generate summary
        task_ctx.summary = self._generate_summary(query, entry_points, subgraph)

        # Stats
        task_ctx.stats = {
            'nodeCount': len(subgraph.nodes),
            'edgeCount': len(subgraph.edges),
            'fileCount': len(task_ctx.related_files),
            'codeBlockCount': len(task_ctx.code_blocks),
            'totalCodeSize': sum(len(b.content) for b in task_ctx.code_blocks),
        }

        return task_ctx

    def _read_code_block(self, node: Node, max_size: int) -> Optional[CodeBlock]:
        """Read the source code for a node."""
        try:
            filepath = os.path.join(self._project_root, node.file_path)
            if not os.path.isfile(filepath):
                return None

            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()

            # Extract the range
            start = max(0, node.start_line - 1)
            end = min(len(lines), node.end_line)
            code_lines = lines[start:end]

            content = ''.join(code_lines).strip()
            if not content:
                return None

            # Truncate if too long
            if len(content) > max_size:
                content = content[:max_size] + '\n... (truncated)'

            return CodeBlock(
                content=content,
                file_path=node.file_path,
                start_line=node.start_line,
                end_line=node.end_line,
                language=node.language,
                node=node,
            )
        except Exception:
            return None

    def _generate_summary(self, query: str, entry_points: List[Node],
                           subgraph: Subgraph) -> str:
        """Generate a text summary of the context."""
        if not entry_points:
            return f'No relevant symbols found for: {query}'

        ep_names = ', '.join(
            f'{n.name} ({n.kind})' for n in entry_points[:5]
        )

        files = set(n.file_path for n in subgraph.nodes.values())

        return (
            f'Found {len(subgraph.nodes)} symbols across {len(files)} files '
            f'related to "{query}". '
            f'Entry points: {ep_names}.'
        )


import os

def create_context_builder(project_root: str, queries: QueryBuilder,
                            traverser: GraphTraverser) -> ContextBuilder:
    """Factory function to create a ContextBuilder."""
    return ContextBuilder(project_root, queries, traverser)
