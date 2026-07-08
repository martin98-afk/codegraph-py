"""
CodeGraph Extraction Layer

Handles file scanning, language detection, and code parsing for indexing.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from codegraph.db.queries import QueryBuilder
from codegraph.types import (
    Edge,
    ExtractionError,
    ExtractionResult,
    FileRecord,
    Language,
    Node,
)

# =============================================================================
# Language Detection
# =============================================================================

# Mapping from file extensions to language identifiers
LANGUAGES: Dict[str, str] = {
    # TypeScript/JavaScript
    '.ts': 'typescript',
    '.tsx': 'tsx',
    '.js': 'javascript',
    '.jsx': 'jsx',
    '.mjs': 'javascript',
    '.cjs': 'javascript',
    '.mts': 'typescript',
    '.cts': 'typescript',
    # ArkTS (HarmonyOS)
    '.ets': 'arkts',
    # Python
    '.py': 'python',
    '.pyi': 'python',
    # Go
    '.go': 'go',
    # Rust
    '.rs': 'rust',
    # Java/Kotlin
    '.java': 'java',
    '.kt': 'kotlin',
    '.kts': 'kotlin',
    # C/C++
    '.c': 'c',
    '.h': 'c',
    '.cpp': 'cpp',
    '.cc': 'cpp',
    '.cxx': 'cpp',
    '.hpp': 'cpp',
    '.hxx': 'cpp',
    # C#
    '.cs': 'csharp',
    # PHP
    '.php': 'php',
    # Ruby
    '.rb': 'ruby',
    '.rake': 'ruby',
    # Swift
    '.swift': 'swift',
    # Dart
    '.dart': 'dart',
    # Svelte
    '.svelte': 'svelte',
    # Vue
    '.vue': 'vue',
    # Astro
    '.astro': 'astro',
    # Liquid
    '.liquid': 'liquid',
    # Pascal
    '.pas': 'pascal',
    '.pp': 'pascal',
    # Scala
    '.scala': 'scala',
    # Lua
    '.lua': 'lua',
    '.luau': 'luau',
    # Objective-C
    '.m': 'objc',
    '.mm': 'objc',
    # R
    '.r': 'r',
    '.R': 'r',
    # Solidity
    '.sol': 'solidity',
    # Nix
    '.nix': 'nix',
    # Terraform
    '.tf': 'terraform',
    '.tfvars': 'terraform',
    # YAML
    '.yaml': 'yaml',
    '.yml': 'yaml',
    # Twig
    '.twig': 'twig',
    # XML
    '.xml': 'xml',
    # Properties
    '.properties': 'properties',
    # CFML/CFScript
    '.cfm': 'cfml',
    '.cfc': 'cfscript',
    # CFQuery
    '.cfquery': 'cfquery',
    # COBOL
    '.cbl': 'cobol',
    '.cob': 'cobol',
    # VB.NET
    '.vb': 'vbnet',
    # Erlang
    '.erl': 'erlang',
    # Razor
    '.cshtml': 'razor',
    '.razor': 'razor',
}

# Extensions that should be treated as source files
SOURCE_EXTENSIONS: Set[str] = set(LANGUAGES.keys())


def detect_language(file_path: str) -> str:
    """
    Detect the programming language of a file based on its extension.
    
    Args:
        file_path: Path to the file (can be absolute or relative)
        
    Returns:
        Language identifier string, or 'unknown' if not detected
    """
    ext = Path(file_path).suffix.lower()
    return LANGUAGES.get(ext, 'unknown')


def is_source_file(file_path: str) -> bool:
    """
    Check if a file is a source file that should be indexed.
    
    Args:
        file_path: Path to the file
        
    Returns:
        True if the file has a recognized source extension
    """
    ext = Path(file_path).suffix.lower()
    return ext in SOURCE_EXTENSIONS


# =============================================================================
# Progress & Result Types
# =============================================================================

@dataclass
class IndexProgress:
    """Progress information during indexing."""
    total_files: int = 0
    processed_files: int = 0
    current_file: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    
    @property
    def percentage(self) -> float:
        if self.total_files == 0:
            return 0.0
        return (self.processed_files / self.total_files) * 100


@dataclass
class IndexResult:
    """Result from indexing a single file."""
    file_path: str
    success: bool
    nodes_count: int = 0
    edges_count: int = 0
    errors: List[ExtractionError] = field(default_factory=list)
    duration_ms: float = 0.0
    skipped: bool = False
    skipped_reason: Optional[str] = None


@dataclass
class SyncResult:
    """Result from a full sync/indexing operation."""
    indexed_files: List[str] = field(default_factory=list)
    skipped_files: List[str] = field(default_factory=list)
    deleted_files: List[str] = field(default_factory=list)
    total_nodes: int = 0
    total_edges: int = 0
    total_errors: int = 0
    duration_ms: float = 0.0
    progress: Optional[IndexProgress] = None


# =============================================================================
# GitIgnore Support
# =============================================================================

class GitIgnore:
    """Simple .gitignore pattern matcher."""
    
    def __init__(self, root_path: str):
        self.root_path = Path(root_path).resolve()
        self._patterns: List[tuple] = []
        self._load_gitignore()
    
    def _load_gitignore(self) -> None:
        """Load .gitignore patterns from the root directory."""
        gitignore_path = self.root_path / '.gitignore'
        if not gitignore_path.exists():
            return
            
        with open(gitignore_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                self._patterns.append(self._parse_pattern(line))
    
    def _parse_pattern(self, pattern: str) -> tuple:
        """Parse a single gitignore pattern."""
        # Handle directory patterns
        if pattern.endswith('/'):
            return ('dir', pattern[:-1])
        
        # Handle negation
        is_negation = pattern.startswith('!')
        if is_negation:
            pattern = pattern[1:]
        
        # Handle wildcards
        if '*' in pattern:
            return ('wildcard', pattern, is_negation)
        
        if '**' in pattern:
            return ('glob', pattern, is_negation)
        
        return ('exact', pattern, is_negation)
    
    def matches(self, path: str) -> bool:
        """
        Check if a path matches any gitignore pattern.
        
        Args:
            path: Path to check (relative to root)
            
        Returns:
            True if the path should be ignored
        """
        import fnmatch
        
        rel_path = Path(path)
        if not rel_path.is_absolute():
            try:
                rel_path = Path(path).resolve().relative_to(self.root_path)
            except ValueError:
                rel_path = Path(path)
        
        path_str = str(rel_path)
        parts = path_str.split(os.sep)
        
        # Check each pattern
        ignored = False
        for pattern_type, *pattern_args in self._patterns:
            if pattern_type == 'exact':
                pattern, is_negation = pattern_args
                if pattern in parts or path_str == pattern:
                    ignored = not is_negation if is_negation else True
                    
            elif pattern_type == 'dir':
                pattern = pattern_args[0]
                if pattern in parts:
                    ignored = True
                    
            elif pattern_type == 'wildcard':
                pattern, is_negation = pattern_args
                # Simple wildcard matching
                for part in parts:
                    if fnmatch.fnmatch(part, pattern):
                        ignored = not is_negation if is_negation else True
                        
            elif pattern_type == 'glob':
                pattern, is_negation = pattern_args
                if fnmatch.fnmatch(path_str, pattern):
                    ignored = not is_negation if is_negation else True
        
        return ignored


# =============================================================================
# Python Parser (regex-based fallback)
# =============================================================================

import re

# Regex patterns for Python code elements
# Note: ^ at line start with re.MULTILINE - we allow leading whitespace via (?P<indent> *)
# to match methods inside classes
PY_FUNCTION_RE = re.compile(
    r'^(?P<indent> *)(?P<decorators>(?:@\w+(?:\([^)]*\))?\s*\n\s*)*)'
    r'(?:async\s+)?def\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)\s*(?:->\s*(?P<return_type>[^:]+))?\s*:',
    re.MULTILINE
)

PY_CLASS_RE = re.compile(
    r'^(?P<indent> *)class\s+(?P<name>\w+)\s*(?:\((?P<bases>[^)]*)\))?\s*:',
    re.MULTILINE
)

PY_DECORATOR_RE = re.compile(r'^\s*@(\w+)', re.MULTILINE)
PY_IMPORT_RE = re.compile(
    r'^import\s+(?P<modules>[^#\n]+)'
    r'|^from\s+(?P<from_module>[^#\n\s]+)\s+import\s+(?P<imports>[^#\n]+)',
    re.MULTILINE
)
PY_DOCSTRING_RE = re.compile(r'^\s*"""(.+?)"""', re.DOTALL | re.MULTILINE)


def _make_node_id(file_path: str, kind: str, name: str) -> str:
    """Create a deterministic node ID."""
    clean = name.replace(' ', '_')
    return f'{kind}:{file_path}::{clean}'


def _normalize_path(file_path: str) -> str:
    """Normalize file path to use forward slashes."""
    return file_path.replace('\\', '/')


def parse_python(file_path: str, content: str) -> ExtractionResult:
    """Parse Python source code using regex."""
    from codegraph.types import Node, Edge, ExtractionResult, ExtractionError, UnresolvedReference

    nodes = []
    edges = []
    errors = []
    unresolved = []
    lines = content.splitlines()
    nlines = len(lines)
    norm_path = _normalize_path(file_path)

    # File node
    file_node = Node(
        id=f'file:{norm_path}',
        kind='file',
        name=Path(file_path).name,
        qualified_name=norm_path,
        file_path=norm_path,
        language='python',
        start_line=1,
        end_line=nlines,
        start_column=0,
        end_column=0,
    )
    nodes.append(file_node)

    # Extract classes
    seen_decorators: List[str] = []
    for match in PY_DECORATOR_RE.finditer(content):
        seen_decorators.append(match.group(1))

    for match in PY_CLASS_RE.finditer(content):
        name = match.group('name')
        bases = match.group('bases') or ''
        start_line = content[:match.start()].count('\n') + 1

        # Find class end line
        end_line = _find_block_end(lines, start_line)

        class_node = Node(
            id=_make_node_id(norm_path, 'class', name),
            kind='class',
            name=name,
            qualified_name=f'{norm_path}::{name}',
            file_path=norm_path,
            language='python',
            start_line=start_line,
            end_line=end_line,
            start_column=0,
            end_column=0,
            is_exported=True,
        )
        nodes.append(class_node)
        edges.append(Edge(source=file_node.id, target=class_node.id, kind='contains'))

        # Extract docstring
        doc_match = PY_DOCSTRING_RE.search(content, match.end())
        if doc_match:
            class_node.docstring = doc_match.group(1).strip()

        # Extract bases (extends edges)
        if bases.strip():
            for base in bases.split(','):
                base = base.strip()
                if base and base != 'object':
                    # Create base class node reference
                    base_id = _make_node_id(norm_path, 'class', base.split('.')[-1])
                    edges.append(Edge(
                        source=class_node.id, target=base_id, kind='extends'
                    ))

        # Extract methods inside the class
        class_body = '\n'.join(lines[start_line - 1:end_line])
        method_offset = start_line - 1
        for m in PY_FUNCTION_RE.finditer(class_body):
            # Get the indentation of the matched function
            raw_indent = m.group('indent') or ''
            indent = len(raw_indent)
            if indent <= 0:
                continue  # not indented = top-level, skip

            is_async = 'async' in m.group(0)[:10]
            fname = m.group('name')
            fparams = m.group('params') or ''
            fret = m.group('return_type')

            fstart = method_offset + class_body[:m.start()].count('\n') + 1
            # Clamp fstart to valid range
            fstart = max(1, min(fstart, len(lines)))
            fend = _find_block_end(lines, fstart)
            fend = max(fstart, fend)

            # Only include if within class bounds
            if fstart > end_line:
                continue

            # Check for @staticmethod, @classmethod
            pre_line = lines[fstart - 2].strip() if fstart > 1 else ''
            is_static = pre_line == '@staticmethod'
            is_classmethod = pre_line == '@classmethod'

            method_node = Node(
                id=_make_node_id(norm_path, 'method', f'{name}.{fname}'),
                kind='method',
                name=fname,
                qualified_name=f'{norm_path}::{name}.{fname}',
                file_path=norm_path,
                language='python',
                start_line=fstart,
                end_line=fend,
                start_column=0,
                end_column=0,
                signature=f'({fparams})' + (f' -> {fret}' if fret else ''),
                is_async=is_async,
                is_static=is_static,
                is_exported=True,
                decorators=[pre_line.replace('@', '')] if pre_line.startswith('@') else None,
            )
            nodes.append(method_node)
            edges.append(Edge(source=class_node.id, target=method_node.id, kind='contains'))

    # Extract top-level functions
    for match in PY_FUNCTION_RE.finditer(content):
        fname = match.group('name')
        fparams = match.group('params') or ''
        fret = match.group('return_type')

        start_line = content[:match.start()].count('\n') + 1

        # Skip if inside a class (the line itself is indented)
        actual_line = lines[start_line - 1] if start_line <= len(lines) else ''
        actual_indent = len(actual_line) - len(actual_line.lstrip())
        if actual_indent > 0:
            continue

        ftype = match.group(0).strip().startswith('async')
        end_line = _find_block_end(lines, start_line)

        # Check decorators
        decorators = []
        line_idx = start_line - 2
        while line_idx >= 0:
            l = lines[line_idx].strip()
            if l.startswith('@'):
                decorators.insert(0, l[1:])
                line_idx -= 1
            else:
                break

        is_exported = not fname.startswith('_')
        is_async = 'async' in match.group(0)[:10]

        func_node = Node(
            id=_make_node_id(norm_path, 'function', fname),
            kind='function',
            name=fname,
            qualified_name=f'{norm_path}::{fname}',
            file_path=norm_path,
            language='python',
            start_line=start_line,
            end_line=end_line,
            start_column=0,
            end_column=0,
            signature=f'({fparams})' + (f' -> {fret}' if fret else ''),
            is_async=is_async,
            is_exported=is_exported,
            decorators=decorators if decorators else None,
        )
        nodes.append(func_node)
        edges.append(Edge(source=file_node.id, target=func_node.id, kind='contains'))

        # Add decorator edges
        for dec in decorators:
            edges.append(Edge(source=func_node.id, target=dec, kind='decorates'))

    # Extract imports
    for match in PY_IMPORT_RE.finditer(content):
        start_line = content[:match.start()].count('\n') + 1

        if match.group('modules'):
            modules = match.group('modules')
            for mod in modules.split(','):
                mod = mod.strip()
                if mod:
                    imp_node = Node(
                        id=_make_node_id(norm_path, 'import', mod),
                        kind='import',
                        name=mod,
                        qualified_name=mod,
                        file_path=norm_path,
                        language='python',
                        start_line=start_line,
                        end_line=start_line,
                        start_column=0,
                        end_column=0,
                    )
                    nodes.append(imp_node)
                    edges.append(Edge(source=file_node.id, target=imp_node.id, kind='contains'))

        if match.group('from_module'):
            from_mod = match.group('from_module').strip()
            imports = match.group('imports')
            for imp in imports.split(','):
                imp = imp.strip()
                if imp:
                    imp_node = Node(
                        id=_make_node_id(norm_path, 'import', f'{from_mod}.{imp}'),
                        kind='import',
                        name=imp,
                        qualified_name=f'{from_mod}.{imp}',
                        file_path=norm_path,
                        language='python',
                        start_line=start_line,
                        end_line=start_line,
                        start_column=0,
                        end_column=0,
                    )
                    nodes.append(imp_node)
                    edges.append(Edge(source=file_node.id, target=imp_node.id, kind='contains'))
                    # Create import edge pointing to the module
                    edges.append(Edge(source=imp_node.id, target=from_mod, kind='imports'))

    return ExtractionResult(
        nodes=nodes,
        edges=edges,
        errors=errors,
        unresolved_references=unresolved,
    )


def _find_block_end(lines: List[str], start_line: int) -> int:
    """Find the end line of a Python block (returns to previous indentation)."""
    if start_line > len(lines):
        return len(lines)

    # Find the indentation of the first line
    first_line = lines[start_line - 1] if start_line > 0 else ''
    indent = len(first_line) - len(first_line.lstrip())

    # Handle single-line blocks (e.g., decorators, one-liners)
    if indent == 0 and start_line <= len(lines):
        stripped = lines[start_line - 1].strip()
        if stripped.endswith(':') and start_line < len(lines):
            next_line = lines[start_line].strip()
            if next_line and not next_line.startswith(('#', '@', '"""', "'")):
                if len(lines[start_line]) - len(lines[start_line].lstrip()) <= indent:
                    return start_line

    for i in range(start_line, len(lines) + 1):
        if i >= len(lines):
            return len(lines)
        line = lines[i]
        if line.strip() == '':
            continue
        current_indent = len(line) - len(line.lstrip())
        if current_indent <= indent and line.strip() and not line.strip().startswith('#'):
            # Check if it's a continuation of a decorator
            if line.strip().startswith('@'):
                continue
            # Check for class/function/method at same level
            stripped = line.strip()
            if stripped.startswith(('def ', 'class ', 'async def ', '@')):
                return i
            return i

    return len(lines)


# =============================================================================
# Parser Dispatch
# =============================================================================

def parse_with_treesitter(file_path: str, content: str, language: str) -> ExtractionResult:
    """
    Parse a source file and extract code symbols.

    Uses tree-sitter AST parsing for supported languages (Python, JavaScript,
    TypeScript, Go, Java, Rust), with regex-based Python parser as fallback.
    For unsupported languages, creates a basic file node.

    Args:
        file_path: Path to the source file (relative to project root)
        content: File content
        language: Programming language

    Returns:
        ExtractionResult with parsed nodes and edges
    """
    # Try tree-sitter first
    from .tree_sitter_extractor import parse_file
    result = parse_file(file_path, content, language)

    # If tree-sitter returned useful nodes, we're done
    if len(result.nodes) > 0 or len(result.errors) == 0:
        # For python, regex parser has better decorator/docstring support
        if language == 'python' and len(result.nodes) <= 1:
            return parse_python(file_path, content)
        return result

    # Fallback for Python
    if language == 'python':
        return parse_python(file_path, content)

    # Fallback: create a basic file node
    from codegraph.types import Node, Edge, ExtractionResult
    nodes = []
    lines = content.splitlines()
    nlines = len(lines)
    norm_path = _normalize_path(file_path)

    file_node = Node(
        id=f'file:{norm_path}',
        kind='file',
        name=Path(file_path).name,
        qualified_name=norm_path,
        file_path=norm_path,
        language=language,
        start_line=1,
        end_line=nlines,
        start_column=0,
        end_column=0,
    )
    nodes.append(file_node)

    return ExtractionResult(nodes=nodes)


# =============================================================================
# Extraction Orchestrator
# =============================================================================

class ExtractionOrchestrator:
    """
    Orchestrates the extraction of code elements from source files.
    
    Handles:
    - File scanning (respecting .gitignore)
    - Language detection
    - Code parsing (via tree-sitter)
    - Database storage of results
    """
    
    def __init__(
        self,
        root_path: str,
        db: QueryBuilder,
        ignore_patterns: Optional[List[str]] = None,
    ):
        """
        Initialize the extraction orchestrator.
        
        Args:
            root_path: Root directory to scan
            db: Database query builder for storing results
            ignore_patterns: Additional patterns to ignore
        """
        self.root_path = Path(root_path).resolve()
        self.db = db
        self.ignore_patterns = ignore_patterns or []
        self._gitignore = GitIgnore(str(self.root_path))
    
    def scan_files(
        self,
        extensions: Optional[List[str]] = None,
    ) -> List[str]:
        """
        Scan for source files in the root path.
        
        Args:
            extensions: Optional list of extensions to filter (e.g., ['.py', '.ts'])
            
        Returns:
            List of relative file paths
        """
        files: List[str] = []
        
        for root, dirs, filenames in os.walk(self.root_path):
            # Skip hidden directories and common ignore paths
            dirs[:] = [
                d for d in dirs
                if not d.startswith('.')
                and d not in ('node_modules', '__pycache__', 'venv', '.venv')
                and d not in self.ignore_patterns
            ]
            
            for filename in filenames:
                if filename.startswith('.'):
                    continue
                    
                file_path = os.path.join(root, filename)
                
                # Check gitignore
                try:
                    rel_path = os.path.relpath(file_path, self.root_path)
                except ValueError:
                    continue
                    
                if self._gitignore.matches(rel_path):
                    continue
                
                # Check extension filter
                if extensions:
                    ext = Path(filename).suffix.lower()
                    if ext not in extensions:
                        continue
                
                # Check if it's a source file
                if is_source_file(file_path):
                    files.append(rel_path)
        
        return sorted(files)
    
    def index_file(
        self,
        file_path: str,
        force: bool = False,
    ) -> IndexResult:
        """
        Index a single file.
        
        Args:
            file_path: Path to the file (relative to root)
            force: Force re-indexing even if file hasn't changed
            
        Returns:
            IndexResult with extraction results
        """
        start_time = time.time()
        
        # Resolve full path
        full_path = self.root_path / file_path
        
        if not full_path.exists():
            return IndexResult(
                file_path=file_path,
                success=False,
                skipped=True,
                skipped_reason='file_not_found',
            )
        
        # Detect language
        language = detect_language(file_path)
        
        if language == 'unknown':
            return IndexResult(
                file_path=file_path,
                success=False,
                skipped=True,
                skipped_reason='unknown_language',
            )
        
        # Check if file has changed
        try:
            content = full_path.read_text(encoding='utf-8')
            content_hash = hashlib.md5(content.encode()).hexdigest()
            file_stat = full_path.stat()
            file_size = file_stat.st_size
            modified_at = int(file_stat.st_mtime * 1000)
        except (OSError, UnicodeDecodeError) as e:
            return IndexResult(
                file_path=file_path,
                success=False,
                errors=[ExtractionError(
                    message=f"Failed to read file: {str(e)}",
                    file_path=file_path,
                    severity='error',
                )],
            )
        
        # Check existing file record
        existing_file = self.db.get_file(file_path)
        if existing_file and not force:
            if existing_file.content_hash == content_hash:
                return IndexResult(
                    file_path=file_path,
                    success=True,
                    nodes_count=existing_file.node_count,
                    skipped=True,
                    skipped_reason='unchanged',
                )
        
        # Delete existing nodes and edges for this file
        self.db.delete_nodes_by_file(file_path)
        self.db.delete_edges_for_file(file_path)
        self.db.delete_unresolved_refs_for_file(file_path)
        
        # Parse the file - use normalized relative path
        import os as _os
        rel_path = _os.path.relpath(str(full_path), str(self.root_path)).replace('\\', '/')
        result = parse_with_treesitter(rel_path, content, language)
        
        # Store nodes
        if result.nodes:
            self.db.insert_nodes(result.nodes)
        
        # Store edges
        if result.edges:
            self.db.insert_edges(result.edges)
        
        # Upsert file record
        indexed_at = int(time.time() * 1000)
        file_record = FileRecord(
            path=file_path,
            content_hash=content_hash,
            language=language,
            size=file_size,
            modified_at=modified_at,
            indexed_at=indexed_at,
            node_count=len(result.nodes),
            errors=result.errors if result.errors else None,
        )
        self.db.upsert_file(file_record)
        
        duration_ms = (time.time() - start_time) * 1000
        
        return IndexResult(
            file_path=file_path,
            success=True,
            nodes_count=len(result.nodes),
            edges_count=len(result.edges),
            errors=result.errors,
            duration_ms=duration_ms,
        )
    
    def sync(
        self,
        force: bool = False,
        extensions: Optional[List[str]] = None,
    ) -> SyncResult:
        """
        Perform a full sync: scan, index, and clean up deleted files.
        
        Args:
            force: Force re-indexing of all files
            extensions: Optional list of extensions to filter
            
        Returns:
            SyncResult with sync statistics
        """
        start_time = time.time()
        
        # Scan for files
        files_to_index = self.scan_files(extensions=extensions)
        
        # Get existing files from database
        existing_files = set(self.db.get_all_file_paths())
        files_to_index_set = set(files_to_index)
        
        # Find deleted files
        deleted_files = existing_files - files_to_index_set
        
        # Remove deleted files from database
        for file_path in deleted_files:
            self.db.delete_file(file_path)
            self.db.delete_nodes_by_file(file_path)
            self.db.delete_edges_for_file(file_path)
        
        # Index files
        progress = IndexProgress(total_files=len(files_to_index))
        indexed_files: List[str] = []
        skipped_files: List[str] = []
        total_nodes = 0
        total_edges = 0
        total_errors = 0
        
        for file_path in files_to_index:
            progress.current_file = file_path
            
            result = self.index_file(file_path, force=force)
            
            if result.success:
                if result.skipped:
                    skipped_files.append(file_path)
                else:
                    indexed_files.append(file_path)
                total_nodes += result.nodes_count
                total_edges += result.edges_count
                total_errors += len(result.errors)
            else:
                progress.errors.append(f"{file_path}: {result.errors}")
                total_errors += 1
            
            progress.processed_files += 1
        
        progress.current_file = None
        duration_ms = (time.time() - start_time) * 1000
        
        return SyncResult(
            indexed_files=indexed_files,
            skipped_files=skipped_files,
            deleted_files=list(deleted_files),
            total_nodes=total_nodes,
            total_edges=total_edges,
            total_errors=total_errors,
            duration_ms=duration_ms,
            progress=progress,
        )


__all__ = [
    # Language detection
    'LANGUAGES',
    'detect_language',
    'is_source_file',
    # Types
    'IndexProgress',
    'IndexResult',
    'SyncResult',
    # Orchestrator
    'ExtractionOrchestrator',
]
