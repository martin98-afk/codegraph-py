"""
CodeGraph Type Definitions

Core types for the semantic knowledge graph system.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# =============================================================================
# Enums & Constants
# =============================================================================

NODE_KINDS = [
    'file', 'module', 'class', 'struct', 'interface', 'trait', 'protocol',
    'function', 'method', 'property', 'field', 'variable', 'constant',
    'enum', 'enum_member', 'type_alias', 'namespace', 'parameter',
    'import', 'export', 'route', 'component',
]

EDGE_KINDS = [
    'contains', 'calls', 'imports', 'exports', 'extends', 'implements',
    'references', 'type_of', 'returns', 'instantiates', 'overrides', 'decorates',
]

LANGUAGES = [
    'typescript', 'javascript', 'tsx', 'jsx', 'arkts',
    'python', 'go', 'rust', 'java', 'c', 'cpp', 'csharp', 'razor',
    'php', 'ruby', 'swift', 'kotlin', 'dart', 'svelte', 'vue', 'astro',
    'liquid', 'pascal', 'scala', 'lua', 'luau', 'objc', 'r', 'solidity',
    'nix', 'yaml', 'twig', 'xml', 'properties', 'cfml', 'cfscript',
    'cfquery', 'cobol', 'vbnet', 'erlang', 'terraform', 'unknown',
]


class NodeKind(str, Enum):
    """Types of nodes in the knowledge graph."""
    FILE = 'file'
    MODULE = 'module'
    CLASS = 'class'
    STRUCT = 'struct'
    INTERFACE = 'interface'
    TRAIT = 'trait'
    PROTOCOL = 'protocol'
    FUNCTION = 'function'
    METHOD = 'method'
    PROPERTY = 'property'
    FIELD = 'field'
    VARIABLE = 'variable'
    CONSTANT = 'constant'
    ENUM = 'enum'
    ENUM_MEMBER = 'enum_member'
    TYPE_ALIAS = 'type_alias'
    NAMESPACE = 'namespace'
    PARAMETER = 'parameter'
    IMPORT = 'import'
    EXPORT = 'export'
    ROUTE = 'route'
    COMPONENT = 'component'


class EdgeKind(str, Enum):
    """Types of edges (relationships) between nodes."""
    CONTAINS = 'contains'
    CALLS = 'calls'
    IMPORTS = 'imports'
    EXPORTS = 'exports'
    EXTENDS = 'extends'
    IMPLEMENTS = 'implements'
    REFERENCES = 'references'
    TYPE_OF = 'type_of'
    RETURNS = 'returns'
    INSTANTIATES = 'instantiates'
    OVERRIDES = 'overrides'
    DECORATES = 'decorates'


class Language(str, Enum):
    """Supported programming languages."""
    TYPESCRIPT = 'typescript'
    JAVASCRIPT = 'javascript'
    TSX = 'tsx'
    JSX = 'jsx'
    ARKTS = 'arkts'
    PYTHON = 'python'
    GO = 'go'
    RUST = 'rust'
    JAVA = 'java'
    C = 'c'
    CPP = 'cpp'
    CSHARP = 'csharp'
    RAZOR = 'razor'
    PHP = 'php'
    RUBY = 'ruby'
    SWIFT = 'swift'
    KOTLIN = 'kotlin'
    DART = 'dart'
    SVELTE = 'svelte'
    VUE = 'vue'
    ASTRO = 'astro'
    LIQUID = 'liquid'
    PASCAL = 'pascal'
    SCALA = 'scala'
    LUA = 'lua'
    LUAU = 'luau'
    OBJC = 'objc'
    R = 'r'
    SOLIDITY = 'solidity'
    NIX = 'nix'
    TERRAFORM = 'terraform'
    UNKNOWN = 'unknown'


# =============================================================================
# Core Graph Types
# =============================================================================

@dataclass
class Node:
    """A node in the knowledge graph representing a code symbol."""
    id: str
    kind: str
    name: str
    qualified_name: str
    file_path: str
    language: str
    start_line: int
    end_line: int
    start_column: int
    end_column: int
    docstring: Optional[str] = None
    signature: Optional[str] = None
    visibility: Optional[str] = None
    is_exported: bool = False
    is_async: bool = False
    is_static: bool = False
    is_abstract: bool = False
    decorators: Optional[List[str]] = None
    type_parameters: Optional[List[str]] = None
    return_type: Optional[str] = None
    updated_at: int = 0


@dataclass
class Edge:
    """An edge representing a relationship between two nodes."""
    id: Optional[int] = None
    source: str = ''
    target: str = ''
    kind: str = ''
    metadata: Optional[Dict[str, Any]] = None
    line: Optional[int] = None
    column: Optional[int] = None
    provenance: Optional[str] = None


@dataclass
class FileRecord:
    """Metadata about a tracked file."""
    path: str
    content_hash: str
    language: str
    size: int
    modified_at: int
    indexed_at: int
    node_count: int = 0
    errors: Optional[List[ExtractionError]] = None


@dataclass
class ExtractionError:
    """Error during code extraction."""
    message: str
    file_path: Optional[str] = None
    line: Optional[int] = None
    column: Optional[int] = None
    severity: str = 'error'  # 'error' | 'warning'
    code: Optional[str] = None


@dataclass
class UnresolvedReference:
    """A reference that couldn't be resolved during extraction."""
    id: Optional[int] = None
    from_node_id: str = ''
    reference_name: str = ''
    reference_kind: str = ''
    line: int = 0
    column: int = 0
    file_path: Optional[str] = None
    language: Optional[str] = None
    candidates: Optional[List[str]] = None


@dataclass
class ExtractionResult:
    """Result from parsing a source file."""
    nodes: List[Node] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)
    unresolved_references: List[UnresolvedReference] = field(default_factory=list)
    errors: List[ExtractionError] = field(default_factory=list)
    duration_ms: float = 0.0


# =============================================================================
# Query Types
# =============================================================================

@dataclass
class Subgraph:
    """A subgraph containing a subset of the knowledge graph."""
    nodes: Dict[str, Node] = field(default_factory=dict)
    edges: List[Edge] = field(default_factory=list)
    roots: List[str] = field(default_factory=list)
    confidence: Optional[str] = None  # 'high' | 'low'


@dataclass
class TraversalOptions:
    """Options for graph traversal."""
    max_depth: Optional[int] = None
    edge_kinds: Optional[List[str]] = None
    node_kinds: Optional[List[str]] = None
    direction: str = 'outgoing'  # 'outgoing' | 'incoming' | 'both'
    limit: Optional[int] = None
    include_start: bool = True


@dataclass
class SearchOptions:
    """Options for searching the graph."""
    kinds: Optional[List[str]] = None
    languages: Optional[List[str]] = None
    include_patterns: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None
    limit: int = 20
    offset: int = 0
    case_sensitive: bool = False
    exact_match: bool = False


@dataclass
class SearchResult:
    """A search result with relevance scoring."""
    node: Node
    score: float
    highlights: Optional[List[str]] = None


@dataclass
class SegmentMatch:
    """A symbol whose name-segments match prose words from a prompt."""
    name: str
    kind: str
    file_path: str
    start_line: int
    matched_words: List[str] = field(default_factory=list)


# =============================================================================
# Context Types
# =============================================================================

@dataclass
class Context:
    """Context information for code understanding."""
    focal: Optional[Node] = None
    ancestors: List[Node] = field(default_factory=list)
    children: List[Node] = field(default_factory=list)
    incoming_refs: List[Dict[str, Any]] = field(default_factory=list)
    outgoing_refs: List[Dict[str, Any]] = field(default_factory=list)
    types: List[Node] = field(default_factory=list)
    imports: List[Node] = field(default_factory=list)


@dataclass
class CodeBlock:
    """A block of code with context."""
    content: str
    file_path: str
    start_line: int
    end_line: int
    language: str
    node: Optional[Node] = None


# =============================================================================
# Statistics
# =============================================================================

@dataclass
class GraphStats:
    """Statistics about the knowledge graph."""
    node_count: int = 0
    edge_count: int = 0
    file_count: int = 0
    nodes_by_kind: Dict[str, int] = field(default_factory=dict)
    edges_by_kind: Dict[str, int] = field(default_factory=dict)
    files_by_language: Dict[str, int] = field(default_factory=dict)
    db_size_bytes: int = 0
    last_updated: int = 0


# =============================================================================
# Task Context Types
# =============================================================================

@dataclass
class BuildContextOptions:
    """Options for building task context."""
    max_nodes: int = 50
    max_code_blocks: int = 10
    max_code_block_size: int = 2000
    include_code: bool = True
    format: str = 'markdown'  # 'markdown' | 'json'
    search_limit: int = 5
    traversal_depth: int = 2
    min_score: float = 0.3


@dataclass
class TaskContext:
    """Full context for a task, ready for AI agent consumption."""
    query: str
    subgraph: Optional[Subgraph] = None
    entry_points: List[Node] = field(default_factory=list)
    code_blocks: List[CodeBlock] = field(default_factory=list)
    related_files: List[str] = field(default_factory=list)
    summary: str = ''
    stats: Optional[Dict[str, int]] = None
