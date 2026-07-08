"""
CodeGraph Search Module

Provides full-text search, fuzzy matching, and natural language symbol lookup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from codegraph.db.connection import DatabaseConnection
from codegraph.types import Node, SearchOptions, SearchResult, SegmentMatch


# =============================================================================
# Identifier Segmentation
# =============================================================================

# Regex patterns for identifier splitting
CAMEL_CASE_PATTERN = re.compile(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')
SNAKE_CASE_PATTERN = re.compile(r'_+|[-]+')
PASCAL_CASE_PATTERN = re.compile(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')


def split_identifier_segments(identifier: str) -> List[str]:
    """
    Split an identifier into meaningful segments.

    Handles:
    - camelCase: "myFunctionName" → ["my", "function", "name"]
    - snake_case: "my_function_name" → ["my", "function", "name"]
    - PascalCase: "MyClassName" → ["my", "class", "name"]
    - kebab-case: "my-function-name" → ["my", "function", "name"]

    Args:
        identifier: The identifier to split (e.g., "getUserById")

    Returns:
        List of lowercase segments (e.g., ["get", "user", "by", "id"])
    """
    if not identifier:
        return []

    # First split by snake_case/kebab-case separators
    segments = SNAKE_CASE_PATTERN.split(identifier)

    result = []
    for segment in segments:
        if not segment:
            continue
        # Then split camelCase within each part
        camel_parts = CAMEL_CASE_PATTERN.split(segment)
        for part in camel_parts:
            if part:
                result.append(part.lower())

    return result if result else [identifier.lower()]


# =============================================================================
# Prose Extraction
# =============================================================================

# Common words to exclude from prose matching
COMMON_WORDS = {
    'a', 'an', 'the', 'and', 'or', 'but', 'is', 'are', 'was', 'were',
    'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did',
    'will', 'would', 'could', 'should', 'may', 'might', 'must', 'shall',
    'can', 'need', 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by',
    'from', 'as', 'into', 'through', 'during', 'before', 'after',
    'above', 'below', 'between', 'under', 'again', 'further', 'then',
    'once', 'get', 'set', 'add', 'remove', 'update', 'delete', 'create',
    'make', 'new', 'this', 'that', 'these', 'those', 'it', 'its',
    'function', 'method', 'class', 'interface', 'type', 'var', 'let',
    'const', 'return', 'value', 'data', 'item', 'list', 'obj', 'name',
    'id', 'by', 'from', 'all', 'any', 'each', 'every', 'some', 'many',
    'few', 'most', 'other', 'such', 'no', 'nor', 'not', 'only', 'same',
    'so', 'than', 'too', 'very', 'just', 'also', 'now', 'here', 'there',
}

# Words that map to common programming concepts
CONCEPT_ALIASES: Dict[str, List[str]] = {
    'fetch': ['get', 'load', 'retrieve', 'read', 'fetch', 'request'],
    'fetching': ['get', 'load', 'retrieve', 'read', 'fetch', 'request'],
    'create': ['add', 'insert', 'new', 'create', 'make', 'build'],
    'update': ['edit', 'modify', 'change', 'update', 'set'],
    'delete': ['remove', 'drop', 'delete', 'clear', 'erase'],
    'save': ['store', 'persist', 'save', 'write', 'commit'],
    'find': ['search', 'query', 'lookup', 'find', 'locate', 'get'],
    'handler': ['callback', 'handler', 'listener', 'on'],
    'listener': ['callback', 'handler', 'listener', 'on'],
    'config': ['configuration', 'settings', 'options', 'config'],
    'init': ['initialize', 'setup', 'init', 'bootstrap', 'start'],
    'utils': ['utilities', 'helpers', 'tools', 'utils'],
    'req': ['request', 'req'],
    'resp': ['response', 'resp'],
    'err': ['error', 'err', 'exception'],
    'elem': ['element', 'elem'],
    'msg': ['message', 'msg'],
    'num': ['number', 'num', 'count'],
    'str': ['string', 'str'],
    'bool': ['boolean', 'bool'],
    'fn': ['function', 'func', 'fn'],
}


def extract_prose_candidates(query: str) -> List[str]:
    """
    Extract candidate words from natural language query for prose matching.

    Extracts meaningful words, normalizes them, and expands with aliases
    for common programming concepts.

    Args:
        query: Natural language query (e.g., "find user by id")

    Returns:
        List of normalized candidate words (e.g., ["find", "user", "id"])
    """
    # Tokenize: split on whitespace and punctuation, keep alphanumerics
    words = re.findall(r'[a-zA-Z0-9]+', query.lower())

    # Filter out common words and very short words
    candidates = []
    seen = set()

    for word in words:
        if word in COMMON_WORDS or len(word) < 2:
            continue
        if word in seen:
            continue
        seen.add(word)
        candidates.append(word)

        # Add aliases for common programming concepts
        if word in CONCEPT_ALIASES:
            for alias in CONCEPT_ALIASES[word]:
                if alias not in seen:
                    seen.add(alias)
                    candidates.append(alias)

    return candidates


# =============================================================================
# Query Parsing
# =============================================================================

# Field query pattern: kind:, lang:, path:
FIELD_PATTERN = re.compile(r'^(kind|lang|path):(.+)$', re.IGNORECASE)


@dataclass
class ParsedQuery:
    """A parsed search query with field filters and remaining text."""
    text: str
    kinds: List[str]
    languages: List[str]
    paths: List[str]


def parse_query(query: str) -> ParsedQuery:
    """
    Parse a search query, extracting field filters.

    Supported fields:
    - kind: Filter by node kind (e.g., "kind:function")
    - lang: Filter by language (e.g., "lang:typescript")
    - path: Filter by file path pattern (e.g., "path:src/utils")

    Examples:
    >>> parse_query("get user data")
    ParsedQuery(text='get user data', kinds=[], languages=[], paths=[])

    >>> parse_query("kind:class lang:typescript path:src")
    ParsedQuery(text='', kinds=['class'], languages=['typescript'], paths=['src'])

    >>> parse_query("find method kind:function lang:python")
    ParsedQuery(text='find method', kinds=['function'], languages=['python'], paths=[])

    Args:
        query: The raw query string

    Returns:
        ParsedQuery with extracted fields and remaining text
    """
    kinds: List[str] = []
    languages: List[str] = []
    paths: List[str] = []
    remaining_parts: List[str] = []

    # Split on quoted strings to preserve them
    tokens = re.findall(r'(?:[^\s"]+|"[^"]*")+', query)

    for token in tokens:
        # Handle quoted strings
        if token.startswith('"') and token.endswith('"'):
            remaining_parts.append(token[1:-1])
            continue

        # Check for field prefix
        match = FIELD_PATTERN.match(token)
        if match:
            field_name = match.group(1).lower()
            field_value = match.group(2).strip()

            if field_name == 'kind':
                kinds.append(field_value.lower())
            elif field_name == 'lang':
                languages.append(field_value.lower())
            elif field_name == 'path':
                paths.append(field_value.lower())
        else:
            remaining_parts.append(token)

    return ParsedQuery(
        text=' '.join(remaining_parts),
        kinds=kinds,
        languages=languages,
        paths=paths,
    )


# =============================================================================
# Fuzzy Searcher
# =============================================================================

class FuzzySearcher:
    """
    Fuzzy search for code symbols using FTS5 with LIKE fallback.

    Uses SQLite FTS5 for fast full-text search when available,
    falls back to LIKE queries for pattern matching.
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the fuzzy searcher.

        Args:
            db_path: Optional path to SQLite database
        """
        self.db_path = db_path
        self._fts_available: Optional[bool] = None

    def _check_fts_available(self) -> bool:
        """Check if FTS5 is available."""
        if self._fts_available is not None:
            return self._fts_available

        try:
            db = DatabaseConnection.open(self.db_path)
            cursor = db.get_db().cursor()
            cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='nodes_fts'"
            )
            self._fts_available = cursor.fetchone() is not None
            db.close()
            return self._fts_available
        except Exception:
            self._fts_available = False
            return False

    def _node_from_row(self, row: tuple) -> Node:
        """Convert a database row to a Node object."""
        return Node(
            id=row[0],
            kind=row[1],
            name=row[2],
            qualified_name=row[3],
            file_path=row[4],
            language=row[5],
            start_line=row[6],
            end_line=row[7],
            start_column=row[8],
            end_column=row[9],
            docstring=row[10],
            signature=row[11],
            visibility=row[12],
            is_exported=bool(row[13]),
            is_async=bool(row[14]),
            is_static=bool(row[15]),
            is_abstract=bool(row[16]),
            decorators=row[17],
            type_parameters=row[18],
            return_type=row[19],
            updated_at=row[20],
        )

    def search(
        self,
        query: str,
        options: Optional[SearchOptions] = None,
    ) -> List[SearchResult]:
        """
        Search for symbols matching the query.

        Args:
            query: Search query (supports field filters like kind:, lang:)
            options: Search options (kinds, languages, limit, etc.)

        Returns:
            List of SearchResult objects sorted by relevance
        """
        options = options or SearchOptions()

        # Parse the query for field filters
        parsed = parse_query(query)

        # Combine parsed filters with options
        kinds = parsed.kinds or (options.kinds or [])
        languages = parsed.languages or (options.languages or [])
        paths = parsed.paths or []
        text = parsed.text

        results: List[SearchResult] = []

        if self._check_fts_available() and text:
            results = self._fts_search(text, kinds, languages, paths, options)
        else:
            results = self._like_search(text, kinds, languages, paths, options)

        # Apply include/exclude patterns
        if options.include_patterns or options.exclude_patterns:
            results = self._filter_by_patterns(results, options)

        # Apply limit and offset
        return results[options.offset : options.offset + options.limit]

    def _fts_search(
        self,
        text: str,
        kinds: List[str],
        languages: List[str],
        paths: List[str],
        options: SearchOptions,
    ) -> List[SearchResult]:
        """Full-text search using FTS5."""
        db = DatabaseConnection.open(self.db_path)
        cursor = db.get_db().cursor()

        # Build FTS query with prefix matching
        fts_query = self._build_fts_query(text)

        # Build WHERE clause for filters
        where_clauses = []
        params: List = []

        if kinds:
            placeholders = ','.join('?' * len(kinds))
            where_clauses.append(f"n.kind IN ({placeholders})")
            params.extend(kinds)

        if languages:
            placeholders = ','.join('?' * len(languages))
            where_clauses.append(f"n.language IN ({placeholders})")
            params.extend(languages)

        if paths:
            path_conditions = []
            for p in paths:
                path_conditions.append("n.file_path LIKE ?")
                params.append(f"%{p}%")
            where_clauses.append(f"({' OR '.join(path_conditions)})")

        where_str = " AND ".join(where_clauses) if where_clauses else "1=1"

        sql = f"""
            SELECT
                n.id, n.kind, n.name, n.qualified_name, n.file_path, n.language,
                n.start_line, n.end_line, n.start_column, n.end_column,
                n.docstring, n.signature, n.visibility,
                n.is_exported, n.is_async, n.is_static, n.is_abstract,
                n.decorators, n.type_parameters, n.return_type, n.updated_at,
                bm25(nodes_fts) as rank
            FROM nodes_fts fts
            JOIN nodes n ON fts.id = n.id
            WHERE nodes_fts MATCH ?
              AND {where_str}
            ORDER BY rank
            LIMIT ?
        """
        params.extend([fts_query, options.limit + options.offset])

        try:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        except Exception:
            # FTS query failed, fall back to LIKE
            db.close()
            return self._like_search(text, kinds, languages, paths, options)

        db.close()

        results = []
        for row in rows:
            node = self._node_from_row(row[:-1])
            # BM25 returns negative values, convert to positive score
            score = abs(row[-1]) if row[-1] else 0.5
            results.append(SearchResult(node=node, score=score))

        return results

    def _like_search(
        self,
        text: str,
        kinds: List[str],
        languages: List[str],
        paths: List[str],
        options: SearchOptions,
    ) -> List[SearchResult]:
        """Fallback search using LIKE queries."""
        db = DatabaseConnection.open(self.db_path)
        cursor = db.get_db().cursor()

        # Build LIKE pattern
        like_pattern = f"%{text}%"

        # Build WHERE clause
        where_clauses = [
            "(n.name LIKE ? OR n.qualified_name LIKE ? OR n.docstring LIKE ?)"
        ]
        params: List = [like_pattern, like_pattern, like_pattern]

        if kinds:
            placeholders = ','.join('?' * len(kinds))
            where_clauses.append(f"n.kind IN ({placeholders})")
            params.extend(kinds)

        if languages:
            placeholders = ','.join('?' * len(languages))
            where_clauses.append(f"n.language IN ({placeholders})")
            params.extend(languages)

        if paths:
            path_conditions = []
            for p in paths:
                path_conditions.append("n.file_path LIKE ?")
                params.append(f"%{p}%")
            where_clauses.append(f"({' OR '.join(path_conditions)})")

        where_str = " AND ".join(where_clauses)

        sql = f"""
            SELECT
                n.id, n.kind, n.name, n.qualified_name, n.file_path, n.language,
                n.start_line, n.end_line, n.start_column, n.end_column,
                n.docstring, n.signature, n.visibility,
                n.is_exported, n.is_async, n.is_static, n.is_abstract,
                n.decorators, n.type_parameters, n.return_type, n.updated_at
            FROM nodes n
            WHERE {where_str}
            LIMIT ?
        """
        params.append(options.limit + options.offset)

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        db.close()

        results = []
        for row in rows:
            node = self._node_from_row(row)
            # LIKE searches get a base score, FTS gets actual BM25
            score = 0.5
            results.append(SearchResult(node=node, score=score))

        return results

    def _build_fts_query(self, text: str) -> str:
        """Build an FTS5 query with prefix matching."""
        # Add prefix matching for each term
        terms = text.split()
        if not terms:
            return '""'

        # Use OR between terms for broader matching
        fts_terms = []
        for term in terms:
            # Add wildcard for prefix matching
            fts_terms.append(f'"{term}"*')

        return ' OR '.join(fts_terms)

    def _filter_by_patterns(
        self,
        results: List[SearchResult],
        options: SearchOptions,
    ) -> List[SearchResult]:
        """Filter results by include/exclude patterns."""
        filtered = []

        for result in results:
            file_path = result.node.file_path

            # Check exclude patterns first
            if options.exclude_patterns:
                excluded = False
                for pattern in options.exclude_patterns:
                    if self._match_pattern(file_path, pattern):
                        excluded = True
                        break
                if excluded:
                    continue

            # Check include patterns (if specified)
            if options.include_patterns:
                included = False
                for pattern in options.include_patterns:
                    if self._match_pattern(file_path, pattern):
                        included = True
                        break
                if not included:
                    continue

            filtered.append(result)

        return filtered

    def _match_pattern(self, text: str, pattern: str) -> bool:
        """Simple glob-style pattern matching."""
        # Convert glob to regex
        regex_pattern = pattern.replace('.', r'\.').replace('*', '.*').replace('?', '.')
        return bool(re.match(f'^{regex_pattern}$', text, re.IGNORECASE))

    def search_by_segments(
        self,
        query: str,
        options: Optional[SearchOptions] = None,
    ) -> List[SegmentMatch]:
        """
        Search for symbols matching query segments.

        Matches prose words from the query against pre-computed name segments
        in the name_segment_vocab table.

        Args:
            query: Natural language query
            options: Search options

        Returns:
            List of SegmentMatch objects
        """
        options = options or SearchOptions()

        # Extract candidate words from query
        candidates = extract_prose_candidates(query)
        if not candidates:
            return []

        db = DatabaseConnection.open(self.db_path)
        cursor = db.get_db().cursor()

        matches: Dict[str, SegmentMatch] = {}

        for word in candidates:
            # Find nodes that have this segment in their name
            sql = """
                SELECT n.name, n.kind, n.file_path, n.start_line
                FROM nodes n
                JOIN name_segment_vocab seg ON n.name = seg.name
                WHERE seg.segment = ?
            """
            params = [word.lower()]

            if options.kinds:
                placeholders = ','.join('?' * len(options.kinds))
                sql += f" AND n.kind IN ({placeholders})"
                params.extend(options.kinds)

            sql += f" LIMIT {options.limit}"

            try:
                cursor.execute(sql, params)
                rows = cursor.fetchall()

                for row in rows:
                    key = f"{row[0]}:{row[2]}"
                    if key not in matches:
                        matches[key] = SegmentMatch(
                            name=row[0],
                            kind=row[1],
                            file_path=row[2],
                            start_line=row[3],
                            matched_words=[],
                        )
                    if word not in matches[key].matched_words:
                        matches[key].matched_words.append(word)
            except Exception:
                # Table might not exist, return empty
                pass

        db.close()

        # Sort by number of matched words
        result = list(matches.values())
        result.sort(key=lambda m: len(m.matched_words), reverse=True)

        return result[: options.limit]


# =============================================================================
# Convenience Functions
# =============================================================================

def search(
    query: str,
    kinds: Optional[List[str]] = None,
    languages: Optional[List[str]] = None,
    limit: int = 20,
) -> List[SearchResult]:
    """
    Convenience function for simple searches.

    Args:
        query: Search query
        kinds: Optional list of node kinds to filter
        languages: Optional list of languages to filter
        limit: Maximum results to return

    Returns:
        List of SearchResult objects
    """
    options = SearchOptions(
        kinds=kinds,
        languages=languages,
        limit=limit,
    )
    searcher = FuzzySearcher()
    return searcher.search(query, options)


def search_prose(
    query: str,
    kinds: Optional[List[str]] = None,
    limit: int = 20,
) -> List[SegmentMatch]:
    """
    Convenience function for prose-based segment search.

    Args:
        query: Natural language query
        kinds: Optional list of node kinds to filter
        limit: Maximum results to return

    Returns:
        List of SegmentMatch objects
    """
    options = SearchOptions(kinds=kinds, limit=limit)
    searcher = FuzzySearcher()
    return searcher.search_by_segments(query, options)
