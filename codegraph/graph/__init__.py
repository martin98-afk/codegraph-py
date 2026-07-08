"""
CodeGraph Graph Traversal Module

Provides graph traversal, path finding, and dependency analysis capabilities.
"""

from __future__ import annotations

import sqlite3
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from codegraph.db.queries import QueryBuilder
from codegraph.types import Node, Edge, EdgeKind, NodeKind


@dataclass
class TraversalResult:
    """Result of a graph traversal."""
    nodes: List[Node] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)
    depths: Dict[str, int] = field(default_factory=dict)
    paths: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class PathResult:
    """Result of a path finding operation."""
    found: bool
    path: List[str] = field(default_factory=list)
    nodes: List[Node] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)
    length: int = 0


@dataclass
class DependencyInfo:
    """Information about a dependency relationship."""
    source: str
    target: str
    kind: str
    file_path: Optional[str] = None
    line: Optional[int] = None


@dataclass
class NodeMetrics:
    """Metrics for a node."""
    node_id: str
    incoming_edges: int = 0
    outgoing_edges: int = 0
    call_depth: int = 0
    fan_out: int = 0
    fan_in: int = 0


class GraphTraverser:
    """Graph traversal and path finding capabilities."""

    def __init__(self, db: sqlite3.Connection, db_path: str = ''):
        self._db = db
        self._qb = QueryBuilder(db, db_path)

    def bfs_traverse(
        self,
        start_id: str,
        max_depth: Optional[int] = None,
        edge_kinds: Optional[List[str]] = None,
        direction: str = 'outgoing',
    ) -> TraversalResult:
        """Breadth-first traversal from a starting node.
        
        Args:
            start_id: Starting node ID
            max_depth: Maximum traversal depth (None for unlimited)
            edge_kinds: Filter by edge kinds
            direction: 'outgoing', 'incoming', or 'both'
            
        Returns:
            TraversalResult with visited nodes and edges
        """
        result = TraversalResult()
        visited: Set[str] = set()
        queue: deque[Tuple[str, int]] = deque([(start_id, 0)])
        visited.add(start_id)
        result.depths[start_id] = 0

        # Get starting node
        start_node = self._qb.get_node_by_id(start_id)
        if start_node:
            result.nodes.append(start_node)

        while queue:
            current_id, depth = queue.popleft()

            if max_depth is not None and depth >= max_depth:
                continue

            # Get edges based on direction
            if direction in ('outgoing', 'both'):
                outgoing = self._qb.get_outgoing_edges(current_id, edge_kinds)
                for edge in outgoing:
                    result.edges.append(edge)
                    if edge.target not in visited:
                        visited.add(edge.target)
                        result.depths[edge.target] = depth + 1
                        queue.append((edge.target, depth + 1))
                        target_node = self._qb.get_node_by_id(edge.target)
                        if target_node:
                            result.nodes.append(target_node)

            if direction in ('incoming', 'both'):
                incoming = self._qb.get_incoming_edges(current_id, edge_kinds)
                for edge in incoming:
                    result.edges.append(edge)
                    if edge.source not in visited:
                        visited.add(edge.source)
                        result.depths[edge.source] = depth + 1
                        queue.append((edge.source, depth + 1))
                        source_node = self._qb.get_node_by_id(edge.source)
                        if source_node:
                            result.nodes.append(source_node)

        return result

    def dfs_traverse(
        self,
        start_id: str,
        max_depth: Optional[int] = None,
        edge_kinds: Optional[List[str]] = None,
        direction: str = 'outgoing',
    ) -> TraversalResult:
        """Depth-first traversal from a starting node.
        
        Args:
            start_id: Starting node ID
            max_depth: Maximum traversal depth (None for unlimited)
            edge_kinds: Filter by edge kinds
            direction: 'outgoing', 'incoming', or 'both'
            
        Returns:
            TraversalResult with visited nodes and edges
        """
        result = TraversalResult()
        visited: Set[str] = set()

        def dfs(current_id: str, depth: int) -> None:
            if max_depth is not None and depth >= max_depth:
                return
            if current_id in visited:
                return

            visited.add(current_id)
            result.depths[current_id] = depth

            node = self._qb.get_node_by_id(current_id)
            if node:
                result.nodes.append(node)

            # Get edges based on direction
            edges = []
            if direction in ('outgoing', 'both'):
                edges.extend(self._qb.get_outgoing_edges(current_id, edge_kinds))
            if direction in ('incoming', 'both'):
                edges.extend(self._qb.get_incoming_edges(current_id, edge_kinds))

            for edge in edges:
                result.edges.append(edge)
                neighbor_id = edge.target if direction in ('outgoing', 'both') and edge.target != current_id else edge.source
                if neighbor_id != current_id:
                    dfs(neighbor_id, depth + 1)

        dfs(start_id, 0)
        return result

    def getCallers(self, node_id: str, max_depth: int = 1) -> List[Node]:
        """Get nodes that call/ reference the given node.

        Args:
            node_id: Target node ID
            max_depth: How many levels up to traverse.
                        1 = direct callers only,
                        2 = callers + their callers, etc.

        Returns:
            List of caller nodes
        """
        callers: List[Node] = []
        seen: Set[str] = set()

        def collect_callers(nid: str, depth: int) -> None:
            if nid in seen:
                return
            seen.add(nid)

            if depth >= max_depth:
                return

            edges = self._qb.get_incoming_edges(nid, [EdgeKind.CALLS, EdgeKind.REFERENCES])
            for edge in edges:
                caller_node = self._qb.get_node_by_id(edge.source)
                if caller_node and edge.source not in seen:
                    callers.append(caller_node)
                    collect_callers(edge.source, depth + 1)

        collect_callers(node_id, 0)
        return callers

    def getCallees(self, node_id: str, max_depth: int = 1) -> List[Node]:
        """Get nodes that the given node calls/ references.

        Args:
            node_id: Source node ID
            max_depth: How many levels down to traverse.
                        1 = direct callees only,
                        2 = callees + their callees, etc.

        Returns:
            List of callee nodes
        """
        callees: List[Node] = []
        seen: Set[str] = set()

        def collect_callees(nid: str, depth: int) -> None:
            if nid in seen:
                return
            seen.add(nid)

            if depth >= max_depth:
                return

            edges = self._qb.get_outgoing_edges(nid, [EdgeKind.CALLS, EdgeKind.REFERENCES])
            for edge in edges:
                callee_node = self._qb.get_node_by_id(edge.target)
                if callee_node and edge.target not in seen:
                    callees.append(callee_node)
                    collect_callees(edge.target, depth + 1)

        collect_callees(node_id, 0)
        return callees

    def getImpactRadius(
        self,
        node_id: str,
        radius: int = 3,
        edge_kinds: Optional[List[str]] = None,
    ) -> TraversalResult:
        """Get all nodes within N hops of the given node.
        
        Args:
            node_id: Starting node ID
            radius: Number of hops to traverse
            edge_kinds: Filter by edge kinds
            
        Returns:
            TraversalResult with all nodes within radius
        """
        return self.bfs_traverse(
            start_id=node_id,
            max_depth=radius,
            edge_kinds=edge_kinds,
            direction='both',
        )

    def findPath(
        self,
        start_id: str,
        end_id: str,
        edge_kinds: Optional[List[str]] = None,
        max_length: int = 20,
    ) -> PathResult:
        """Find a path between two nodes using BFS.
        
        Args:
            start_id: Starting node ID
            end_id: Target node ID
            edge_kinds: Filter by edge kinds
            max_length: Maximum path length
            
        Returns:
            PathResult with found path or empty result
        """
        if start_id == end_id:
            node = self._qb.get_node_by_id(start_id)
            return PathResult(
                found=True,
                path=[start_id],
                nodes=[node] if node else [],
                length=0,
            )

        queue: deque[Tuple[str, List[str]]] = deque([(start_id, [start_id])])
        visited: Set[str] = {start_id}

        while queue:
            current_id, path = queue.popleft()

            if len(path) >= max_length:
                continue

            # Check outgoing edges
            edges = self._qb.get_outgoing_edges(current_id, edge_kinds)
            for edge in edges:
                if edge.target == end_id:
                    full_path = path + [edge.target]
                    nodes = self._qb.get_nodes_by_ids(full_path)
                    return PathResult(
                        found=True,
                        path=full_path,
                        nodes=nodes,
                        edges=edges,
                        length=len(full_path) - 1,
                    )

                if edge.target not in visited:
                    visited.add(edge.target)
                    queue.append((edge.target, path + [edge.target]))

        return PathResult(found=False)

    def getAncestors(
        self,
        node_id: str,
        edge_kinds: Optional[List[str]] = None,
        max_depth: Optional[int] = None,
    ) -> List[Node]:
        """Get ancestor nodes (parent, grandparent, etc.) via incoming edges.
        
        Args:
            node_id: Starting node ID
            edge_kinds: Filter by edge kinds (e.g., 'extends', 'imports')
            max_depth: Maximum depth to traverse
            
        Returns:
            List of ancestor nodes
        """
        result = self.bfs_traverse(
            start_id=node_id,
            max_depth=max_depth,
            edge_kinds=edge_kinds,
            direction='incoming',
        )
        return result.nodes

    def getChildren(
        self,
        node_id: str,
        edge_kinds: Optional[List[str]] = None,
        max_depth: Optional[int] = None,
    ) -> List[Node]:
        """Get child nodes via outgoing edges.
        
        Args:
            node_id: Starting node ID
            edge_kinds: Filter by edge kinds (e.g., 'contains', 'calls')
            max_depth: Maximum depth to traverse
            
        Returns:
            List of child nodes
        """
        result = self.bfs_traverse(
            start_id=node_id,
            max_depth=max_depth,
            edge_kinds=edge_kinds,
            direction='outgoing',
        )
        return result.nodes

    def getTypeHierarchy(
        self,
        node_id: str,
    ) -> Dict[str, List[Node]]:
        """Get type hierarchy for a node (super types and sub types).
        
        Args:
            node_id: Node ID to get hierarchy for
            
        Returns:
            Dict with 'super' and 'sub' keys containing type lists
        """
        hierarchy: Dict[str, List[Node]] = {
            'super': [],  # Parent types
            'sub': [],    # Child types
        }

        # Get super types (extends, implements)
        super_edges = self._qb.get_outgoing_edges(
            node_id,
            [EdgeKind.EXTENDS, EdgeKind.IMPLEMENTS]
        )
        for edge in super_edges:
            super_node = self._qb.get_node_by_id(edge.target)
            if super_node:
                hierarchy['super'].append(super_node)

        # Get sub types
        sub_edges = self._qb.get_incoming_edges(
            node_id,
            [EdgeKind.EXTENDS, EdgeKind.IMPLEMENTS]
        )
        for edge in sub_edges:
            sub_node = self._qb.get_node_by_id(edge.source)
            if sub_node:
                hierarchy['sub'].append(sub_node)

        return hierarchy


class GraphQueryManager:
    """High-level graph queries and analysis."""

    def __init__(self, db: sqlite3.Connection, db_path: str = ''):
        self._db = db
        self._qb = QueryBuilder(db, db_path)
        self._traverser = GraphTraverser(db, db_path)

    def getContext(
        self,
        node_id: str,
        depth: int = 2,
        include_code: bool = False,
    ) -> Dict[str, Any]:
        """Get comprehensive context around a node.
        
        Args:
            node_id: Focal node ID
            depth: Traversal depth
            include_code: Include source code snippets
            
        Returns:
            Dict with context information
        """
        focal = self._qb.get_node_by_id(node_id)
        if not focal:
            return {}

        # Get traversal results
        outgoing = self._traverser.bfs_traverse(node_id, max_depth=depth, direction='outgoing')
        incoming = self._traverser.bfs_traverse(node_id, max_depth=depth, direction='incoming')

        # Get direct callers and callees
        callers = self._traverser.getCallers(node_id)
        callees = self._traverser.getCallees(node_id)

        # Get type hierarchy
        type_hierarchy = self._traverser.getTypeHierarchy(node_id)

        return {
            'focal': focal,
            'ancestors': incoming.nodes,
            'children': outgoing.nodes,
            'callers': callers,
            'callees': callees,
            'super_types': type_hierarchy.get('super', []),
            'sub_types': type_hierarchy.get('sub', []),
            'outgoing_edges': outgoing.edges,
            'incoming_edges': incoming.edges,
        }

    def getFileDependencies(
        self,
        file_path: str,
        transitive: bool = False,
    ) -> Dict[str, List[DependencyInfo]]:
        """Get dependencies for a file.
        
        Args:
            file_path: Path to the file
            transitive: If True, include transitive dependencies
            
        Returns:
            Dict with 'imports' and 'exports' keys
        """
        nodes = self._qb.get_nodes_by_file(file_path)

        dependencies: Dict[str, List[DependencyInfo]] = {
            'imports': [],
            'exports': [],
        }

        # Get import edges from nodes in this file
        for node in nodes:
            outgoing = self._qb.get_outgoing_edges(
                node.id,
                [EdgeKind.IMPORTS, EdgeKind.REFERENCES]
            )
            for edge in outgoing:
                target_node = self._qb.get_node_by_id(edge.target)
                if target_node:
                    dependencies['imports'].append(DependencyInfo(
                        source=node.id,
                        target=edge.target,
                        kind=edge.kind,
                        file_path=target_node.file_path,
                        line=edge.line,
                    ))

            # Get export edges
            incoming = self._qb.get_incoming_edges(node.id, [EdgeKind.EXPORTS])
            for edge in incoming:
                source_node = self._qb.get_node_by_id(edge.source)
                if source_node:
                    dependencies['exports'].append(DependencyInfo(
                        source=edge.source,
                        target=node.id,
                        kind=edge.kind,
                        file_path=source_node.file_path,
                        line=edge.line,
                    ))

        # Add transitive dependencies if requested
        if transitive:
            visited_files: Set[str] = {file_path}
            queue = list(dependencies['imports'])

            while queue:
                dep = queue.pop(0)
                if dep.file_path and dep.file_path in visited_files:
                    continue
                if dep.file_path:
                    visited_files.add(dep.file_path)
                    sub_deps = self.getFileDependencies(dep.file_path, transitive=False)
                    dependencies['imports'].extend(sub_deps.get('imports', []))

        return dependencies

    def getExportedSymbols(self, file_path: str) -> List[Node]:
        """Get exported symbols from a file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            List of exported nodes
        """
        nodes = self._qb.get_nodes_by_file(file_path)
        exported = []

        for node in nodes:
            # Check if node is exported
            if node.is_exported:
                exported.append(node)
                continue

            # Check for export edges
            edges = self._qb.get_incoming_edges(node.id, [EdgeKind.EXPORTS])
            if edges:
                exported.append(node)

        return exported

    def findCircularDependencies(
        self,
        start_id: Optional[str] = None,
    ) -> List[List[str]]:
        """Find circular dependencies in the graph.
        
        Args:
            start_id: Optional starting node ID
            
        Returns:
            List of cycles, each as a list of node IDs
        """
        cycles: List[List[str]] = []

        if start_id:
            nodes_to_check = [start_id]
        else:
            # Check all nodes
            all_files = self._qb.get_all_files()
            nodes_to_check = []
            for f in all_files:
                file_nodes = self._qb.get_nodes_by_file(f.path)
                for n in file_nodes:
                    if n.kind in (NodeKind.MODULE, NodeKind.CLASS, NodeKind.FUNCTION):
                        nodes_to_check.append(n.id)

        for node_id in nodes_to_check:
            visited: Set[str] = set()
            path: List[str] = []

            def dfs(current: str) -> bool:
                if current in visited:
                    # Found a cycle
                    if current in path:
                        idx = path.index(current)
                        cycle = path[idx:] + [current]
                        if cycle not in cycles:
                            cycles.append(cycle)
                        return True
                    return False

                visited.add(current)
                path.append(current)

                edges = self._qb.get_outgoing_edges(current, [EdgeKind.IMPORTS, EdgeKind.CALLS])
                for edge in edges:
                    if dfs(edge.target):
                        return True

                path.pop()
                return False

            dfs(node_id)

        return cycles

    def getNodeMetrics(self, node_id: str) -> NodeMetrics:
        """Get metrics for a node.
        
        Args:
            node_id: Node ID
            
        Returns:
            NodeMetrics with various measurements
        """
        metrics = NodeMetrics(node_id=node_id)

        # Get outgoing edges
        outgoing = self._qb.get_outgoing_edges(node_id)
        metrics.outgoing_edges = len(outgoing)
        metrics.fan_out = len([e for e in outgoing if e.kind in (EdgeKind.CALLS, EdgeKind.REFERENCES)])

        # Get incoming edges
        incoming = self._qb.get_incoming_edges(node_id)
        metrics.incoming_edges = len(incoming)
        metrics.fan_in = len([e for e in incoming if e.kind in (EdgeKind.CALLS, EdgeKind.REFERENCES)])

        # Calculate call depth (longest path from any root)
        result = self._traverser.bfs_traverse(node_id, direction='incoming', edge_kinds=[EdgeKind.CALLS])
        metrics.call_depth = max(result.depths.values()) if result.depths else 0

        return metrics

    def findDeadCode(
        self,
        exclude_exports: bool = True,
        min_fan_in: int = 0,
    ) -> Dict[str, List[Node]]:
        """Find potentially dead/unused code.
        
        Args:
            exclude_exports: Exclude exported symbols from results
            min_fan_in: Minimum fan-in (incoming references) to consider alive
            
        Returns:
            Dict with categories of dead code
        """
        all_nodes = []
        files = self._qb.get_all_files()

        for f in files:
            nodes = self._qb.get_nodes_by_file(f.path)
            all_nodes.extend(nodes)

        dead_code: Dict[str, List[Node]] = {
            'unexported_functions': [],
            'unused_imports': [],
            'unreferenced': [],
        }

        for node in all_nodes:
            # Skip certain kinds
            if node.kind not in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS, NodeKind.VARIABLE):
                continue

            # Get incoming references
            incoming = self._qb.get_incoming_edges(node.id, [EdgeKind.CALLS, EdgeKind.REFERENCES])

            # Check if exported
            is_exported = node.is_exported
            export_edges = self._qb.get_incoming_edges(node.id, [EdgeKind.EXPORTS])
            if export_edges:
                is_exported = True

            if not is_exported and exclude_exports:
                if node.kind in (NodeKind.FUNCTION, NodeKind.METHOD):
                    dead_code['unexported_functions'].append(node)

            # Check for unreferenced
            if len(incoming) <= min_fan_in and not is_exported:
                if node.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS):
                    dead_code['unreferenced'].append(node)

        # Find unused imports
        all_import_nodes = self._qb.get_nodes_by_kind(NodeKind.IMPORT)
        for imp in all_import_nodes:
            incoming = self._qb.get_incoming_edges(imp.id, [EdgeKind.REFERENCES])
            if not incoming:
                dead_code['unused_imports'].append(imp)

        return dead_code
