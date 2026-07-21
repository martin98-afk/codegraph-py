"""
Python Web Framework Route Detector

Detects routing patterns in Django, Flask, and FastAPI projects and emits
`route` nodes linked by `references` edges to their handler functions/classes.

Supported frameworks:
  - Django: path(), re_path(), url() in urls.py files
  - Flask: @app.route() / @blueprint.route() decorators
  - FastAPI: @app.get() / @router.post() / @app.api_route() decorators
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from codegraph.types import Node, Edge, ExtractionResult


# =============================================================================
# Django: path(), re_path(), url() in urls.py
# =============================================================================

# Match path('url/', view_func, ...) / path("url/", include(...)) etc.
_DJANGO_PATH_RE = re.compile(
    r'(?:path|re_path)\s*\(\s*'
    r"(?:r)?['\"](?P<url>[^'\"]+)['\"]\s*,\s*"
    r'(?P<handler>[^,)]+?)'
    r'(?:\s*,\s*(?:name|kwargs)\s*=.*)?\s*\)',
    re.MULTILINE,
)

# Match url(r'^url/$', view_func, name='x')
_DJANGO_URL_RE = re.compile(
    r'url\s*\(\s*'
    r"(?:r)?['\"](?P<url>[^'\"]+)['\"]\s*,\s*"
    r'(?P<handler>[^,)]+?)'
    r'(?:\s*,\s*.*)?\s*\)',
    re.MULTILINE,
)

# Match include('app.urls') inside path()
_DJANGO_INCLUDE_RE = re.compile(
    r"include\s*\(\s*['\"](?P<module>[^'\"]+)['\"]\s*\)"
)


def detect_django_routes(
    file_path: str,
    content: str,
    lines: List[str],
    norm_path: str,
) -> Tuple[List[Node], List[Edge]]:
    """Detect Django URL routing patterns in urls.py files."""
    nodes: List[Node] = []
    edges: List[Edge] = []

    basename = Path(file_path).name
    if basename != 'urls.py' and not basename.endswith('_urls.py'):
        return nodes, edges

    # Detect path() and re_path() calls
    for match in _DJANGO_PATH_RE.finditer(content):
        url_pattern = match.group('url')
        handler_expr = match.group('handler').strip()
        start_line = content[:match.start()].count('\n') + 1

        # Skip includes — handled by include() detection
        if handler_expr.startswith('include'):
            continue

        route_id = _make_route_id(norm_path, f'django:{url_pattern}')
        route_node = Node(
            id=route_id,
            kind='route',
            name=url_pattern,
            qualified_name=f'{norm_path}::{url_pattern}',
            file_path=norm_path,
            language='python',
            start_line=start_line,
            end_line=start_line,
            start_column=0,
            end_column=0,
            signature=f'Django URL: {url_pattern} → {handler_expr}',
            is_exported=True,
        )
        nodes.append(route_node)

        # Create references edge to handler
        handler_name = _extract_handler_name(handler_expr)
        if handler_name:
            edges.append(Edge(
                source=route_id,
                target=handler_name,
                kind='references',
                metadata={'framework': 'django', 'url': url_pattern, 'handler': handler_expr},
                line=start_line,
                provenance='route-detector',
            ))

    # Detect url() calls (Django <2.0 style)
    for match in _DJANGO_URL_RE.finditer(content):
        url_pattern = match.group('url')
        handler_expr = match.group('handler').strip()
        start_line = content[:match.start()].count('\n') + 1

        if handler_expr.startswith('include'):
            continue

        route_id = _make_route_id(norm_path, f'django-legacy:{url_pattern}')
        route_node = Node(
            id=route_id,
            kind='route',
            name=url_pattern,
            qualified_name=f'{norm_path}::{url_pattern}',
            file_path=norm_path,
            language='python',
            start_line=start_line,
            end_line=start_line,
            start_column=0,
            end_column=0,
            signature=f'Django URL (legacy): {url_pattern} → {handler_expr}',
            is_exported=True,
        )
        nodes.append(route_node)

        handler_name = _extract_handler_name(handler_expr)
        if handler_name:
            edges.append(Edge(
                source=route_id,
                target=handler_name,
                kind='references',
                metadata={'framework': 'django', 'url': url_pattern, 'handler': handler_expr},
                line=start_line,
                provenance='route-detector',
            ))

    return nodes, edges


# =============================================================================
# Flask: @app.route() / @blueprint.route() decorators
# =============================================================================

# Match @app.route('/path', methods=['GET']) decorators
_FLASK_ROUTE_RE = re.compile(
    r'@\w+\.route\s*\(\s*'
    r"['\"](?P<url>[^'\"]+)['\"]"
    r'(?P<args>.*?)'
    r'\s*\)',
    re.MULTILINE,
)

# Match @app.get('/path'), @app.post('/path'), etc. (Flask 2.0+)
_FLASK_SHORT_RE = re.compile(
    r'@\w+\.(?:get|post|put|delete|patch|head|options)\s*\(\s*'
    r"['\"](?P<url>[^'\"]+)['\"]"
    r'(?P<args>.*?)'
    r'\s*\)',
    re.MULTILINE,
)


def _parse_flask_methods(args_str: str) -> List[str]:
    """Extract HTTP methods from Flask route decorator args."""
    methods_match = re.search(r'methods\s*=\s*\[(.*?)\]', args_str)
    if methods_match:
        raw = methods_match.group(1)
        return [m.strip().strip("'\"") for m in raw.split(',') if m.strip()]
    return ['GET']


def detect_flask_routes(
    file_path: str,
    content: str,
    lines: List[str],
    norm_path: str,
) -> Tuple[List[Node], List[Edge]]:
    """Detect Flask routing decorators."""
    nodes: List[Node] = []
    edges: List[Edge] = []
    seen: Set[str] = set()

    # @app.route('/path') style
    for match in _FLASK_ROUTE_RE.finditer(content):
        url_pattern = match.group('url')
        args_str = match.group('args') or ''
        methods = _parse_flask_methods(args_str)
        start_line = content[:match.start()].count('\n') + 1

        # Find the decorated function (next non-decorator line)
        handler_line, handler_name = _find_decorated_function(lines, start_line)
        if not handler_name:
            continue

        key = f'flask:{url_pattern}:{handler_name}'
        if key in seen:
            continue
        seen.add(key)

        method_str = '/'.join(methods)
        route_id = _make_route_id(norm_path, key)
        route_node = Node(
            id=route_id,
            kind='route',
            name=url_pattern,
            qualified_name=f'{norm_path}::{handler_name}:{url_pattern}',
            file_path=norm_path,
            language='python',
            start_line=start_line,
            end_line=handler_line,
            start_column=0,
            end_column=0,
            signature=f'Flask {method_str} {url_pattern} → {handler_name}',
            is_exported=True,
            decorators=[f'route({url_pattern})'],
        )
        nodes.append(route_node)
        edges.append(Edge(
            source=route_id,
            target=handler_name,
            kind='references',
            metadata={'framework': 'flask', 'url': url_pattern, 'methods': methods},
            line=start_line,
            provenance='route-detector',
        ))

    # @app.get('/path') style (Flask 2.0+)
    for match in _FLASK_SHORT_RE.finditer(content):
        decorator_text = match.group(0)
        method_verb = re.match(r'@\w+\.(\w+)', decorator_text)
        http_method = method_verb.group(1).upper() if method_verb else 'GET'
        url_pattern = match.group('url')
        start_line = content[:match.start()].count('\n') + 1

        handler_line, handler_name = _find_decorated_function(lines, start_line)
        if not handler_name:
            continue

        key = f'flask-short:{url_pattern}:{handler_name}'
        if key in seen:
            continue
        seen.add(key)

        route_id = _make_route_id(norm_path, key)
        route_node = Node(
            id=route_id,
            kind='route',
            name=url_pattern,
            qualified_name=f'{norm_path}::{handler_name}:{url_pattern}',
            file_path=norm_path,
            language='python',
            start_line=start_line,
            end_line=handler_line,
            start_column=0,
            end_column=0,
            signature=f'Flask {http_method} {url_pattern} → {handler_name}',
            is_exported=True,
        )
        nodes.append(route_node)
        edges.append(Edge(
            source=route_id,
            target=handler_name,
            kind='references',
            metadata={'framework': 'flask', 'url': url_pattern, 'method': http_method},
            line=start_line,
            provenance='route-detector',
        ))

    return nodes, edges


# =============================================================================
# FastAPI: @app.get() / @router.post() / @app.api_route()
# =============================================================================

# Match @app.get('/path'), @router.post('/path'), etc.
_FASTAPI_METHOD_RE = re.compile(
    r'@\w+\.(?:get|post|put|delete|patch|head|options|trace|api_route)\s*\(\s*'
    r"['\"](?P<url>[^'\"]+)['\"]"
    r'(?P<args>.*?)'
    r'\s*\)',
    re.MULTILINE,
)


def detect_fastapi_routes(
    file_path: str,
    content: str,
    lines: List[str],
    norm_path: str,
) -> Tuple[List[Node], List[Edge]]:
    """Detect FastAPI routing decorators."""
    nodes: List[Node] = []
    edges: List[Edge] = []
    seen: Set[str] = set()

    for match in _FASTAPI_METHOD_RE.finditer(content):
        decorator_text = match.group(0)
        method_match = re.match(r'@\w+\.(\w+)', decorator_text)
        if not method_match:
            continue

        http_method = method_match.group(1).upper()
        url_pattern = match.group('url')
        args_str = match.group('args') or ''
        start_line = content[:match.start()].count('\n') + 1

        # Find the decorated function
        handler_line, handler_name = _find_decorated_function(lines, start_line)
        if not handler_name:
            continue

        key = f'fastapi:{http_method}:{url_pattern}:{handler_name}'
        if key in seen:
            continue
        seen.add(key)

        # Check for api_route with methods param
        methods = [http_method]
        if http_method == 'API_ROUTE':
            methods_match = re.search(r'methods\s*=\s*\[(.*?)\]', args_str)
            if methods_match:
                raw = methods_match.group(1)
                methods = [m.strip().strip("'\"") for m in raw.split(',') if m.strip()]
            else:
                methods = ['GET']

        method_str = '/'.join(methods)
        route_id = _make_route_id(norm_path, key)
        route_node = Node(
            id=route_id,
            kind='route',
            name=url_pattern,
            qualified_name=f'{norm_path}::{handler_name}:{url_pattern}',
            file_path=norm_path,
            language='python',
            start_line=start_line,
            end_line=handler_line,
            start_column=0,
            end_column=0,
            signature=f'FastAPI {method_str} {url_pattern} → {handler_name}',
            is_exported=True,
        )
        nodes.append(route_node)
        edges.append(Edge(
            source=route_id,
            target=handler_name,
            kind='references',
            metadata={'framework': 'fastapi', 'url': url_pattern, 'methods': methods},
            line=start_line,
            provenance='route-detector',
        ))

    return nodes, edges


# =============================================================================
# Shared Helpers
# =============================================================================

def _make_route_id(file_path: str, route_key: str) -> str:
    """Create a deterministic route node ID."""
    clean = route_key.replace(' ', '_').replace('/', '_').replace(':', '_')
    return f'route:{file_path}::{clean}'


def _extract_handler_name(handler_expr: str) -> Optional[str]:
    """Extract handler function/class name from Django-style handler expression.

    Handles: 'view_func', 'ViewClass.as_view()', 'module.views.view_func'
    """
    handler_expr = handler_expr.strip()

    # Remove .as_view() call
    if '.as_view()' in handler_expr:
        handler_expr = handler_expr.split('.as_view()')[0]
        # Get the class name from dotted path
        parts = handler_expr.split('.')
        return parts[-1].strip() if parts else handler_expr.strip()

    # Handle dotted paths: module.views.view_func → view_func
    parts = handler_expr.split('.')
    name = parts[-1].strip() if parts else handler_expr.strip()

    # Clean up: remove quotes, whitespace
    name = name.strip("'\"").strip()
    return name if name else None


def _find_decorated_function(
    lines: List[str],
    decorator_line: int,
) -> Tuple[Optional[int], Optional[str]]:
    """Find the function definition right after a decorator.

    Returns (line_number, function_name) or (None, None).
    """
    for i in range(decorator_line, min(decorator_line + 10, len(lines))):
        line = lines[i - 1] if i > 0 else ''
        stripped = line.strip()

        # Skip blank lines and other decorators
        if not stripped or stripped.startswith('@'):
            continue

        # Match: def function_name(...):
        func_match = re.match(r'^\s*(?:async\s+)?def\s+(\w+)\s*\(', stripped)
        if func_match:
            return i, func_match.group(1)

        # Match: class ClassName(...):
        class_match = re.match(r'^\s*class\s+(\w+)\s*\(', stripped)
        if class_match:
            return i, class_match.group(1)

        # Reached non-matching code — stop looking
        break

    return None, None


# =============================================================================
# Main Detection Entry Point
# =============================================================================

def detect_routes(
    file_path: str,
    content: str,
    language: str,
) -> Tuple[List[Node], List[Edge]]:
    """Detect web framework routes in a source file.

    Args:
        file_path: Path to the source file (relative to project root)
        content: File content
        language: Programming language identifier

    Returns:
        Tuple of (route_nodes, edges_from_route_to_handler)
    """
    if language != 'python':
        return [], []

    norm_path = file_path.replace('\\', '/')
    lines = content.splitlines()
    all_nodes: List[Node] = []
    all_edges: List[Edge] = []
    seen_routes: Set[str] = set()

    def _route_key(node: Node) -> str:
        """Normalized dedup key: url + method + handler name."""
        sig = node.signature or ''
        # Extract URL and handler from signature
        parts = sig.split('→')
        url_part = parts[0].rsplit(' ', 1)[-1] if ' ' in parts[0] else parts[0]
        handler_part = parts[-1].strip() if len(parts) > 1 else ''
        return f'{url_part}|{handler_part}'

    def _add_unique(node: Node, edge: Optional[Edge]) -> None:
        key = _route_key(node)
        if key in seen_routes:
            return
        seen_routes.add(key)
        all_nodes.append(node)
        if edge:
            all_edges.append(edge)

    # Django routes
    n, e = detect_django_routes(file_path, content, lines, norm_path)
    for i, node in enumerate(n):
        _add_unique(node, e[i] if i < len(e) else None)

    # Flask routes
    n, e = detect_flask_routes(file_path, content, lines, norm_path)
    for i, node in enumerate(n):
        _add_unique(node, e[i] if i < len(e) else None)

    # FastAPI routes
    n, e = detect_fastapi_routes(file_path, content, lines, norm_path)
    for i, node in enumerate(n):
        _add_unique(node, e[i] if i < len(e) else None)

    return all_nodes, all_edges
