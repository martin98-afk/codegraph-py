"""
Tree-sitter-based code extraction engine.

Parses source files using tree-sitter and extracts structural information:
functions, classes, methods, imports, calls, and their relationships.
Replaces/enhances the regex-based parsers with full AST parsing.
"""

import os
import time
import logging
from pathlib import Path
from typing import Optional

from tree_sitter import Language, Parser, Node as TSNode, Tree

from ..types import (
    Language as LangEnum,
    Node, Edge, NodeKind, EdgeKind,
    ExtractionResult, ExtractionError, UnresolvedReference,
    FileRecord,
)
from .languages.base import LanguageConfig, ImportInfo
from .languages import get_config

logger = logging.getLogger(__name__)


def _normalize_path(file_path: str) -> str:
    """Normalize file path to use forward slashes."""
    return file_path.replace('\\', '/')


def _make_id(kind: str, file_path: str, name: str) -> str:
    """Create a deterministic node ID matching existing convention."""
    clean = name.replace(' ', '_')
    return f'{kind}:{_normalize_path(file_path)}::{clean}'


def _node_text(node: TSNode) -> str:
    """Get text from a node safely."""
    try:
        t = node.text
        if isinstance(t, bytes):
            return t.decode('utf-8', errors='replace')
        return str(t)
    except Exception:
        return ''


class TreeSitterExtractor:
    """
    Extracts code structure using tree-sitter AST parsing.

    For each file, it:
    1. Detects the language config
    2. Parses with tree-sitter
    3. Walks the AST to extract symbols and relationships
    4. Returns ExtractionResult matching the existing type system
    """

    def __init__(self):
        self._parsers: dict[str, Parser] = {}
        self._languages: dict[str, Language] = {}

    def _get_language(self, lang_id: str) -> Optional[Language]:
        """Get or create a tree-sitter Language for the given language id."""
        if lang_id not in self._languages:
            try:
                import importlib
                mod_name = f'tree_sitter_{lang_id.replace("-", "_")}'
                try:
                    mod = importlib.import_module(mod_name)
                except ModuleNotFoundError:
                    logger.debug(f"Tree-sitter grammar not available: '{lang_id}'")
                    return None

                # Try standard 'language()' function first
                lang_func = getattr(mod, 'language', None)
                if lang_func is not None:
                    self._languages[lang_id] = Language(lang_func())
                else:
                    # Some packages use language_<grammar_name> (e.g. typescript)
                    specific_func = getattr(mod, f'language_{lang_id}', None)
                    if specific_func is not None:
                        self._languages[lang_id] = Language(specific_func())
                    else:
                        logger.debug(f"No language function found in '{mod_name}'")
                        return None
            except Exception as e:
                logger.debug(f"Failed to load tree-sitter language '{lang_id}': {e}")
                return None
        return self._languages.get(lang_id)

    def _get_parser(self, lang_id: str) -> Optional[Parser]:
        """Get or create a parser for the given language."""
        if lang_id not in self._parsers:
            ts_lang = self._get_language(lang_id)
            if ts_lang is None:
                return None
            parser = Parser()
            parser.language = ts_lang
            self._parsers[lang_id] = parser
        return self._parsers.get(lang_id)

    def extract(
        self, file_path: str, source: bytes, lang_enum: LangEnum
    ) -> ExtractionResult:
        """
        Extract code structure from a source file using tree-sitter.

        Args:
            file_path: Normalized path (relative to project root)
            source: Raw file content as bytes
            lang_enum: Language enum value

        Returns:
            ExtractionResult with nodes, edges, etc.
        """
        start_time = time.time()
        config = get_config(lang_enum)
        if config is None:
            return ExtractionResult(
                nodes=[_make_file_node(file_path, source, lang_enum.value)],
                duration_ms=(time.time() - start_time) * 1000,
            )

        parser = self._get_parser(config.language_id)
        if parser is None:
            return ExtractionResult(
                nodes=[_make_file_node(file_path, source, lang_enum.value)],
                errors=[ExtractionError(
                    message=f"Tree-sitter parser not available for {lang_enum.value}",
                    severity='warning',
                )],
                duration_ms=(time.time() - start_time) * 1000,
            )

        # Parse
        try:
            tree = parser.parse(source)
        except Exception as e:
            return ExtractionResult(
                nodes=[_make_file_node(file_path, source, lang_enum.value)],
                errors=[ExtractionError(
                    message=f"Parse error: {e}",
                    file_path=file_path,
                    severity='error',
                )],
                duration_ms=(time.time() - start_time) * 1000,
            )

        # Extract
        extractor = _FileExtractor(file_path, source, config, tree, lang_enum)
        result = extractor.extract()
        result.duration_ms = (time.time() - start_time) * 1000
        return result


def _make_file_node(file_path: str, source: bytes, language: str) -> Node:
    """Create a basic file node."""
    lines = source.splitlines() if source else []
    nlines = max(len(lines), 1)
    norm = _normalize_path(file_path)
    return Node(
        id=f'file:{norm}',
        kind='file',
        name=Path(file_path).name,
        qualified_name=norm,
        file_path=norm,
        language=language,
        start_line=1,
        end_line=nlines,
        start_column=0,
        end_column=0,
    )


class _FileExtractor:
    """Extracts structure from a single file's AST."""

    def __init__(
        self,
        file_path: str,
        source: bytes,
        config: LanguageConfig,
        tree: Tree,
        lang_enum: LangEnum,
    ):
        self.file_path = file_path
        self.norm_path = _normalize_path(file_path)
        self.source = source
        self.config = config
        self.tree = tree
        self.lang = lang_enum.value

        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self.errors: list[ExtractionError] = []
        self.unresolved_refs: list[UnresolvedReference] = []

        # Node kind sets for fast dispatch
        t = config
        self._func_set = set(t.function_types)
        self._class_set = set(t.class_types)
        self._method_set = set(t.method_types)
        self._interface_set = set(t.interface_types)
        self._struct_set = set(t.struct_types)
        self._enum_set = set(t.enum_types)
        self._type_alias_set = set(t.type_alias_types)
        self._call_set = set(t.call_types)
        self._var_set = set(t.variable_types)

        # Track scope for method detection
        self._in_class_body = False
        self._current_class_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _pos(self, node: TSNode) -> tuple[int, int, int, int]:
        """Return (start_line, end_line, start_col, end_col)."""
        return (
            node.start_point[0] + 1,
            node.end_point[0] + 1,
            node.start_point[1],
            node.end_point[1],
        )

    def _node_name(self, node: TSNode) -> Optional[str]:
        """Extract name via the configured name_field."""
        name_node = node.child_by_field_name(self.config.name_field)
        if name_node is not None:
            return _node_text(name_node)
        for child in node.named_children:
            if child.type in ('identifier', 'property_identifier', 'type_identifier'):
                return _node_text(child)
        return None

    def _get_lines(self) -> int:
        return max(len(self.source.splitlines()), 1) if self.source else 1

    # ------------------------------------------------------------------
    # Main extraction
    # ------------------------------------------------------------------

    def extract(self) -> ExtractionResult:
        """Run extraction and return result."""
        root = self.tree.root_node
        nlines = self._get_lines()

        file_node = Node(
            id=f'file:{self.norm_path}',
            kind='file',
            name=Path(self.file_path).name,
            qualified_name=self.norm_path,
            file_path=self.norm_path,
            language=self.lang,
            start_line=1,
            end_line=nlines,
            start_column=0,
            end_column=0,
        )
        self.nodes.append(file_node)

        # Visit top-level children
        self._visit_children(root, file_node.id)

        return ExtractionResult(
            nodes=self.nodes,
            edges=self.edges,
            unresolved_references=self.unresolved_refs,
            errors=self.errors,
        )

    # ------------------------------------------------------------------
    # AST traversal
    # ------------------------------------------------------------------

    def _visit_children(self, parent: TSNode, parent_node_id: str):
        """Visit all named children of a node."""
        for child in parent.named_children:
            self._visit_node(child, parent_node_id)

    def _visit_node(self, node: TSNode, parent_node_id: str):
        """Dispatch a single node based on type."""
        if not node.is_named or self.config.should_skip_type(node.type):
            return

        t = node.type

        if t in self._func_set and not self._in_class_body:
            self._extract_function(node, parent_node_id)
        elif t in self._method_set and self._in_class_body:
            self._extract_method(node, parent_node_id)
        elif t in self._class_set:
            self._extract_class(node, parent_node_id)
        elif t in self._interface_set:
            self._extract_interface(node, parent_node_id)
        elif t in self._struct_set:
            self._extract_struct(node, parent_node_id)
        elif t in self._enum_set:
            self._extract_enum(node, parent_node_id)
        elif t in self._type_alias_set:
            self._extract_type_alias(node, parent_node_id)
        elif t in self._call_set:
            self._extract_call(node, parent_node_id)
        elif self.config.extract_import(node, self.source) is not None:
            # Handled by extract_import hook
            pass
        else:
            # Continue traversing
            self._visit_children(node, parent_node_id)

    # ------------------------------------------------------------------
    # Symbol extractors
    # ------------------------------------------------------------------

    def _extract_function(self, node: TSNode, parent_node_id: str):
        """Extract a top-level function."""
        name = self._node_name(node)
        if not name:
            self._visit_children(node, parent_node_id)
            return

        sl, el, sc, ec = self._pos(node)
        nid = _make_id(self.lang, self.norm_path, name)

        func_node = Node(
            id=nid,
            kind='function',
            name=name,
            qualified_name=f'{self.norm_path}::{name}',
            file_path=self.norm_path,
            language=self.lang,
            start_line=sl, end_line=el,
            start_column=sc, end_column=ec,
            signature=self.config.get_signature(node, self.source),
            is_async=self.config.is_async(node),
            is_static=self.config.is_static(node),
            is_exported=self.config.is_exported(node, self.source),
        )
        self.nodes.append(func_node)
        self.edges.append(Edge(source=parent_node_id, target=nid, kind='contains'))

        self._visit_children(node, nid)

    def _extract_method(self, node: TSNode, parent_node_id: str):
        """Extract a method inside a class."""
        name = self._node_name(node)
        if not name:
            self._visit_children(node, parent_node_id)
            return

        sl, el, sc, ec = self._pos(node)

        # Build qualified name using class name if available
        qname = name
        if self._current_class_id:
            qname = f'{self._current_class_id.split("::")[-1]}.{name}'
        nid = _make_id('method', self.norm_path, qname)

        method_node = Node(
            id=nid,
            kind='method',
            name=name,
            qualified_name=f'{self.norm_path}::{qname}',
            file_path=self.norm_path,
            language=self.lang,
            start_line=sl, end_line=el,
            start_column=sc, end_column=ec,
            signature=self.config.get_signature(node, self.source),
            is_async=self.config.is_async(node),
            is_static=self.config.is_static(node),
            is_exported=self.config.is_exported(node, self.source),
        )
        self.nodes.append(method_node)
        self.edges.append(Edge(source=parent_node_id, target=nid, kind='contains'))

        self._visit_children(node, nid)

    def _extract_class(self, node: TSNode, parent_node_id: str):
        """Extract a class definition and its body."""
        name = self._node_name(node)
        if not name:
            self._visit_children(node, parent_node_id)
            return

        sl, el, sc, ec = self._pos(node)
        nid = _make_id('class', self.norm_path, name)

        class_node = Node(
            id=nid,
            kind='class',
            name=name,
            qualified_name=f'{self.norm_path}::{name}',
            file_path=self.norm_path,
            language=self.lang,
            start_line=sl, end_line=el,
            start_column=sc, end_column=ec,
        )
        self.nodes.append(class_node)
        self.edges.append(Edge(source=parent_node_id, target=nid, kind='contains'))

        # Visit body with class scope flag
        old_class_id = self._current_class_id
        old_in_class = self._in_class_body
        self._current_class_id = nid
        self._in_class_body = True

        body = node.child_by_field_name(self.config.body_field)
        if body:
            self._visit_children(body, nid)
        else:
            self._visit_children(node, nid)

        self._in_class_body = old_in_class
        self._current_class_id = old_class_id

    def _extract_interface(self, node: TSNode, parent_node_id: str):
        """Extract an interface definition."""
        name = self._node_name(node)
        if not name:
            self._visit_children(node, parent_node_id)
            return

        sl, el, sc, ec = self._pos(node)
        nid = _make_id('interface', self.norm_path, name)

        i_node = Node(
            id=nid, kind='interface', name=name,
            qualified_name=f'{self.norm_path}::{name}',
            file_path=self.norm_path, language=self.lang,
            start_line=sl, end_line=el, start_column=sc, end_column=ec,
        )
        self.nodes.append(i_node)
        self.edges.append(Edge(source=parent_node_id, target=nid, kind='contains'))

        body = node.child_by_field_name(self.config.body_field)
        if body:
            self._visit_children(body, nid)
        else:
            self._visit_children(node, nid)

    def _extract_struct(self, node: TSNode, parent_node_id: str):
        """Extract a struct definition."""
        name = self._node_name(node)
        if not name:
            self._visit_children(node, parent_node_id)
            return

        sl, el, sc, ec = self._pos(node)
        nid = _make_id('struct', self.norm_path, name)

        s_node = Node(
            id=nid, kind='struct', name=name,
            qualified_name=f'{self.norm_path}::{name}',
            file_path=self.norm_path, language=self.lang,
            start_line=sl, end_line=el, start_column=sc, end_column=ec,
        )
        self.nodes.append(s_node)
        self.edges.append(Edge(source=parent_node_id, target=nid, kind='contains'))

        self._visit_children(node, nid)

    def _extract_enum(self, node: TSNode, parent_node_id: str):
        """Extract an enum definition."""
        name = self._node_name(node)
        if not name:
            self._visit_children(node, parent_node_id)
            return

        sl, el, sc, ec = self._pos(node)
        nid = _make_id('enum', self.norm_path, name)

        e_node = Node(
            id=nid, kind='enum', name=name,
            qualified_name=f'{self.norm_path}::{name}',
            file_path=self.norm_path, language=self.lang,
            start_line=sl, end_line=el, start_column=sc, end_column=ec,
        )
        self.nodes.append(e_node)
        self.edges.append(Edge(source=parent_node_id, target=nid, kind='contains'))

        self._visit_children(node, nid)

    def _extract_type_alias(self, node: TSNode, parent_node_id: str):
        """Extract a type alias."""
        name = self._node_name(node)
        if not name:
            return

        sl, el, sc, ec = self._pos(node)
        nid = _make_id('type_alias', self.norm_path, name)

        t_node = Node(
            id=nid, kind='type_alias', name=name,
            qualified_name=f'{self.norm_path}::{name}',
            file_path=self.norm_path, language=self.lang,
            start_line=sl, end_line=el, start_column=sc, end_column=ec,
        )
        self.nodes.append(t_node)
        self.edges.append(Edge(source=parent_node_id, target=nid, kind='contains'))

    def _extract_call(self, node: TSNode, parent_node_id: str):
        """Extract a function call (as unresolved reference)."""
        func = node.child_by_field_name('function')
        if func is None:
            return

        name = _node_text(func)
        if not name:
            return

        sl, el, sc, ec = self._pos(node)
        self.unresolved_refs.append(UnresolvedReference(
            from_node_id=parent_node_id,
            reference_name=name,
            reference_kind='call',
            line=sl,
            column=sc,
            file_path=self.norm_path,
            language=self.lang,
        ))


# =============================================================================
# Convenience: single-file parse function (drop-in for parse_with_treesitter)
# =============================================================================

_singleton_extractor: Optional[TreeSitterExtractor] = None


def get_ts_extractor() -> TreeSitterExtractor:
    """Get or create the singleton TreeSitterExtractor."""
    global _singleton_extractor
    if _singleton_extractor is None:
        _singleton_extractor = TreeSitterExtractor()
    return _singleton_extractor


def parse_file(file_path: str, content: str, language: str) -> ExtractionResult:
    """
    Parse a single file using tree-sitter.

    Args:
        file_path: Path (relative to project root, normalized)
        content: File content as string
        language: Language identifier string

    Returns:
        ExtractionResult
    """
    from . import detect_language as detect_lang

    # Map string language to enum
    lang_map = {
        'python': LangEnum.PYTHON,
        'javascript': LangEnum.JAVASCRIPT,
        'jsx': LangEnum.JSX,
        'typescript': LangEnum.TYPESCRIPT,
        'tsx': LangEnum.TSX,
        'arkts': LangEnum.ARKTS,
        'go': LangEnum.GO,
        'rust': LangEnum.RUST,
        'java': LangEnum.JAVA,
        'c': LangEnum.C,
        'cpp': LangEnum.CPP,
        'csharp': LangEnum.CSHARP,
        'php': LangEnum.PHP,
        'ruby': LangEnum.RUBY,
        'swift': LangEnum.SWIFT,
        'kotlin': LangEnum.KOTLIN,
        'dart': LangEnum.DART,
        'scala': LangEnum.SCALA,
        'lua': LangEnum.LUA,
        'luau': LangEnum.LUAU,
        'solidity': LangEnum.SOLIDITY,
        'nix': LangEnum.NIX,
        'terraform': LangEnum.TERRAFORM,
        'pascal': LangEnum.PASCAL,
        'objc': LangEnum.OBJC,
        'r': LangEnum.R,
        'svelte': LangEnum.SVELTE,
        'vue': LangEnum.VUE,
        'astro': LangEnum.ASTRO,
        'liquid': LangEnum.LIQUID,
        'razor': LangEnum.RAZOR,
        'unknown': LangEnum.UNKNOWN,
    }

    lang_enum = lang_map.get(language, LangEnum.UNKNOWN)
    extractor = get_ts_extractor()
    return extractor.extract(file_path, content.encode('utf-8'), lang_enum)
