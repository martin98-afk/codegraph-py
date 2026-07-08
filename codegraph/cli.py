"""
CodeGraph CLI

Command-line interface for CodeGraph code intelligence.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Optional, List, Dict

import click

from codegraph import __version__
from codegraph.codegraph import CodeGraph
from codegraph.directory import (
    is_initialized, find_nearest_codegraph_root,
    unsafe_index_root_reason, derive_project_name_tokens,
)
from codegraph.types import SearchOptions, SearchResult, GraphStats


# =============================================================================
# CLI Colors & Helpers
# =============================================================================

class Colors:
    """ANSI color helpers."""
    RESET = '\x1b[0m'
    BOLD = '\x1b[1m'
    DIM = '\x1b[2m'
    RED = '\x1b[31m'
    GREEN = '\x1b[32m'
    YELLOW = '\x1b[33m'
    BLUE = '\x1b[34m'
    CYAN = '\x1b[36m'
    GRAY = '\x1b[90m'


def bold(s: str) -> str:
    return f'{Colors.BOLD}{s}{Colors.RESET}'


def dim(s: str) -> str:
    return f'{Colors.DIM}{s}{Colors.RESET}'


def red(s: str) -> str:
    return f'{Colors.RED}{s}{Colors.RESET}'


def green(s: str) -> str:
    return f'{Colors.GREEN}{s}{Colors.RESET}'


def yellow(s: str) -> str:
    return f'{Colors.YELLOW}{s}{Colors.RESET}'


def blue(s: str) -> str:
    return f'{Colors.BLUE}{s}{Colors.RESET}'


def format_number(n: int) -> str:
    """Format a number with commas."""
    return f'{n:,}'


def _pluralize(word: str) -> str:
    """Simple English pluralization."""
    if word.endswith(('s', 'x', 'ch', 'sh')):
        return word + 'es'
    if word.endswith('y') and len(word) > 2 and word[-2] not in 'aeiou':
        return word[:-1] + 'ies'
    return word + 's'


def format_duration(ms: float) -> str:
    """Format duration in milliseconds to human readable."""
    if ms < 1000:
        return f'{ms:.0f}ms'
    seconds = ms / 1000
    if seconds < 60:
        return f'{seconds:.1f}s'
    minutes = int(seconds / 60)
    remaining = seconds % 60
    return f'{minutes}m {remaining:.0f}s'


def resolve_project_path(path_arg: Optional[str] = None) -> str:
    """Resolve project path from argument or current directory."""
    absolute = os.path.abspath(path_arg or os.getcwd())

    if is_initialized(absolute):
        return absolute

    # Walk up to find nearest parent with CodeGraph initialized
    current = absolute
    root = os.path.splitdrive(absolute)[0] + os.sep

    while current != root:
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
        if is_initialized(current):
            return current

    return absolute


# =============================================================================
# CLI Commands
# =============================================================================

@click.group(context_settings=dict(help_option_names=['-h', '--help']))
@click.version_option(version=__version__, prog_name='codegraph')
def main():
    """CodeGraph — Semantic code intelligence for AI coding agents.

    Build a knowledge graph of your codebase for instant symbol lookup,
    call-chain analysis, and surgical context extraction.

    \b
    Common workflows:
      codegraph init            Initialize and index current project
      codegraph status          View index statistics
      codegraph query "class"   Search for symbols matching "class"
      codegraph query --kind class   List all classes in the project
      codegraph explore "main"  Show source code and call paths for "main"
      codegraph callers "func"  Show what calls the function "func"
      codegraph callees "func"  Show what "func" calls
      codegraph files           List all indexed files
    """
    pass


@main.command()
@click.argument('path', required=False, default=None)
@click.option('-f', '--force', is_flag=True, help='Initialize even if path looks unsafe')
@click.option('-v', '--verbose', is_flag=True, help='Show detailed progress')
def init(path: Optional[str], force: bool, verbose: bool):
    """Initialize CodeGraph in a project directory and build initial index."""
    project_path = os.path.abspath(path or os.getcwd())

    # Check unsafe paths
    unsafe = unsafe_index_root_reason(project_path)
    if unsafe and not force:
        click.echo(red(
            f'Refusing to initialize in {project_path} — it looks like {unsafe}.'
        ))
        click.echo('Run this inside a specific project directory, or pass --force.')
        return

    if is_initialized(project_path):
        click.echo(yellow(f'Already initialized in {project_path}'))
        click.echo('Use "codegraph index" to re-index or "codegraph sync" to update')
        return

    click.echo(bold('\nInitializing CodeGraph...\n'))

    try:
        cg = CodeGraph.init_sync(project_path)
        click.echo(green(f'✓ Initialized in {project_path}'))

        # Run initial index
        click.echo('Indexing project...')

        result = cg.index_all(verbose=verbose)

        if result.success and result.files_indexed > 0:
            click.echo(green(
                f'✓ Indexed {format_number(result.files_indexed)} files'
            ))
            click.echo(
                f'{format_number(result.nodes_created)} nodes, '
                f'{format_number(result.edges_created)} edges '
                f'in {format_duration(result.duration_ms)}'
            )
        elif result.files_errored > 0:
            click.echo(yellow(
                f'Indexed {format_number(result.files_indexed)} files '
                f'({format_number(result.files_errored)} errors)'
            ))
        else:
            click.echo(yellow('No files found to index'))

        cg.destroy()
        click.echo(green('\nDone! Run "codegraph status" to see the results.'))

    except Exception as e:
        click.echo(red(f'Failed: {str(e)}'))
        sys.exit(1)


@main.command()
@click.argument('path', required=False, default=None)
def uninit(path: Optional[str]):
    """Remove CodeGraph from a project (deletes .codegraph/ directory)."""
    project_path = resolve_project_path(path)

    if not is_initialized(project_path):
        click.echo(yellow(f'CodeGraph is not initialized in {project_path}'))
        return

    click.echo(yellow(
        f'This will permanently delete all CodeGraph data from {project_path}'
    ))
    if not click.confirm('Continue?'):
        click.echo('Cancelled')
        return

    try:
        cg = CodeGraph.open_sync(project_path)
        cg.uninitialize()
        click.echo(green(f'Removed CodeGraph from {project_path}'))
    except Exception as e:
        click.echo(red(f'Failed: {str(e)}'))
        sys.exit(1)


@main.command()
@click.argument('path', required=False, default=None)
@click.option('-f', '--force', is_flag=True, help='Index even if path looks unsafe')
@click.option('-q', '--quiet', is_flag=True, help='Suppress progress output')
@click.option('-v', '--verbose', is_flag=True, help='Show detailed progress')
def index(path: Optional[str], force: bool, quiet: bool, verbose: bool):
    """Rebuild the full index from scratch."""
    project_path = resolve_project_path(path)

    # Check unsafe paths
    unsafe = unsafe_index_root_reason(project_path)
    if unsafe and not force:
        click.echo(red(
            f'Refusing to index {project_path} — it looks like {unsafe}. '
            f'Pass --force to override.'
        ))
        return

    if not is_initialized(project_path):
        click.echo(red(f'CodeGraph not initialized in {project_path}'))
        click.echo('Run "codegraph init" first')
        return

    click.echo(bold('\nIndexing project...\n'))

    try:
        # Recreate database for fresh index
        cg = CodeGraph.open_sync(project_path)

        result = cg.index_all(verbose=verbose)

        if not quiet:
            if result.success and result.files_indexed > 0:
                click.echo(green(
                    f'✓ Indexed {format_number(result.files_indexed)} files'
                ))
                click.echo(
                    f'{format_number(result.nodes_created)} nodes, '
                    f'{format_number(result.edges_created)} edges '
                    f'in {format_duration(result.duration_ms)}'
                )
            elif result.files_errored > 0:
                click.echo(yellow(
                    f'Indexed {format_number(result.files_indexed)} files '
                    f'({format_number(result.files_errored)} errors)'
                ))
            else:
                click.echo(yellow('No files found to index'))

        cg.destroy()
        click.echo(green('\nDone!'))

    except Exception as e:
        click.echo(red(f'Failed: {str(e)}'))
        sys.exit(1)


@main.command()
@click.argument('path', required=False, default=None)
@click.option('-q', '--quiet', is_flag=True, help='Suppress output')
def sync(path: Optional[str], quiet: bool):
    """Sync changes since last index."""
    project_path = resolve_project_path(path)

    if not is_initialized(project_path):
        if not quiet:
            click.echo(red(f'CodeGraph not initialized in {project_path}'))
        return

    try:
        cg = CodeGraph.open_sync(project_path)

        result = cg.sync()

        if not quiet:
            total = result.files_added + result.files_modified + result.files_removed
            if total == 0:
                click.echo('Already up to date')
            else:
                click.echo(green(f'Synced {format_number(total)} changed files'))
                details = []
                if result.files_added > 0:
                    details.append(f'Added: {result.files_added}')
                if result.files_modified > 0:
                    details.append(f'Modified: {result.files_modified}')
                if result.files_removed > 0:
                    details.append(f'Removed: {result.files_removed}')
                click.echo(
                    f'{", ".join(details)} — '
                    f'{format_number(result.nodes_updated)} nodes '
                    f'in {format_duration(result.duration_ms)}'
                )

        cg.destroy()
        if not quiet:
            click.echo(green('Done!'))

    except Exception as e:
        if not quiet:
            click.echo(red(f'Failed: {str(e)}'))
        sys.exit(1)


@main.command()
@click.argument('path', required=False, default=None)
@click.option('-j', '--json', 'json_output', is_flag=True, help='Output as JSON')
def status(path: Optional[str], json_output: bool):
    """Show index status, statistics, and pending changes.

    Displays file count, node/edge counts, node breakdown by kind,
    and any pending changes since the last index.

    Use --json/-j for machine-readable output.
    """
    project_path = resolve_project_path(path)

    if not is_initialized(project_path):
        if json_output:
            click.echo(json.dumps({
                'initialized': False,
                'version': '1.0.1',
                'projectPath': project_path,
            }))
            return

        click.echo(bold('\nCodeGraph Status\n'))
        click.echo(f'Project: {project_path}')
        click.echo(yellow('Not initialized'))
        click.echo('Run "codegraph init" to initialize')
        return

    try:
        cg = CodeGraph.open_sync(project_path)
        stats = cg.get_stats()
        changes = cg.get_changed_files()
        build_info = cg.get_index_build_info()

        if json_output:
            click.echo(json.dumps({
                'initialized': True,
                'version': '1.0.1',
                'projectPath': project_path,
                'fileCount': stats.file_count,
                'nodeCount': stats.node_count,
                'edgeCount': stats.edge_count,
                'dbSizeBytes': stats.db_size_bytes,
                'backend': 'sqlite3',
                'nodesByKind': stats.nodes_by_kind,
                'languages': [k for k, v in stats.files_by_language.items() if v > 0],
                'pendingChanges': {
                    'added': len(changes.get('added', [])),
                    'modified': len(changes.get('modified', [])),
                    'removed': len(changes.get('removed', [])),
                },
            }, indent=2))
            cg.destroy()
            return

        click.echo(bold('\nCodeGraph Status\n'))
        click.echo(f'{blue("Project:")} {project_path}')
        click.echo()

        # Index stats
        click.echo(bold('Index Statistics:'))
        click.echo(f'  Files:     {format_number(stats.file_count)}')
        click.echo(f'  Nodes:     {format_number(stats.node_count)}')
        click.echo(f'  Edges:     {format_number(stats.edge_count)}')
        click.echo(f'  DB Size:   {stats.db_size_bytes / 1024 / 1024:.2f} MB')
        click.echo(f'  Backend:   {green("sqlite3 — built-in (full WAL)")}')
        click.echo()

        # Node breakdown
        if stats.nodes_by_kind:
            click.echo(bold('Nodes by Kind:'))
            for kind, count in sorted(
                stats.nodes_by_kind.items(),
                key=lambda x: -x[1]
            ):
                click.echo(f'  {kind:15} {format_number(count)}')
            click.echo()

        # Pending changes
        total = (
            len(changes.get('added', []))
            + len(changes.get('modified', []))
            + len(changes.get('removed', []))
        )
        if total > 0:
            click.echo(bold('Pending Changes:'))
            if changes.get('added'):
                click.echo(f'  Added:     {len(changes["added"])} files')
            if changes.get('modified'):
                click.echo(f'  Modified:  {len(changes["modified"])} files')
            if changes.get('removed'):
                click.echo(f'  Removed:   {len(changes["removed"])} files')
            click.echo(blue('Run "codegraph sync" to update the index'))
        else:
            click.echo(green('Index is up to date'))
        click.echo()

        cg.destroy()

    except Exception as e:
        click.echo(red(f'Failed: {str(e)}'))
        sys.exit(1)


@main.command()
@click.argument('search', required=False, default='')
@click.option('-p', '--path', 'path_arg', help='Project path')
@click.option('-l', '--limit', default=20, type=int, help='Maximum results')
@click.option('-k', '--kind', help='Filter by node kind (function, class, etc.)')
@click.option('--exact', is_flag=True, help='Exact name match (instead of fuzzy/prefix)')
@click.option('--fuzzy', is_flag=True, help='Force fuzzy name matching (default)')
@click.option('-j', '--json', 'json_output', is_flag=True, help='Output as JSON')
def query(search: str, path_arg: Optional[str], limit: int,
           kind: Optional[str], exact: bool, fuzzy: bool,
           json_output: bool):
    """Search for symbols in the codebase.

    If SEARCH is omitted and --kind is provided, lists all symbols of that kind.
    Use --exact for precise symbol name lookup.
    """
    project_path = resolve_project_path(path_arg)

    if not is_initialized(project_path):
        click.echo(red(f'CodeGraph not initialized in {project_path}'))
        return

    try:
        cg = CodeGraph.open_sync(project_path)

        opts = SearchOptions(limit=limit)
        if kind:
            opts.kinds = [kind]

        if exact:
            opts.exact_match = True

        if not search.strip() and kind:
            # No search term — list by kind only
            nodes = cg._queries.get_nodes_by_kind(kind) if kind else []
            results = [SearchResult(node=n, score=1.0) for n in nodes]
        else:
            results = cg.search_nodes(search, opts)

        if json_output:
            output = []
            for r in results:
                output.append({
                    'node': {
                        'id': r.node.id,
                        'kind': r.node.kind,
                        'name': r.node.name,
                        'qualifiedName': r.node.qualified_name,
                        'filePath': r.node.file_path,
                        'startLine': r.node.start_line,
                        'endLine': r.node.end_line,
                        'signature': r.node.signature,
                    },
                    'score': r.score,
                })
            click.echo(json.dumps(output, indent=2))
            cg.destroy()
            return

        if not results:
            if search:
                click.echo(f'No results found for "{search}"')
            elif kind:
                click.echo(f'No symbols of kind "{kind}" found')
            else:
                click.echo('No search term provided. Use --kind to browse by type.')
        else:
            title = f'\nSearch Results for "{search}":' if search else f'\nAll {_pluralize(kind)}:' if kind else '\nResults:'
            click.echo(bold(title + '\n'))
            for r in results:
                node = r.node
                location = f'{node.file_path}:{node.start_line}'
                click.echo(f'  {blue(node.kind.ljust(12))} {bold(node.name)}')
                click.echo(f'  {dim("  " + location)}')
                if node.signature:
                    click.echo(f'  {dim("  " + node.signature)}')
                click.echo()

        cg.destroy()

    except Exception as e:
        click.echo(red(f'Search failed: {str(e)}'))
        sys.exit(1)


@main.command()
@click.argument('query_parts', nargs=-1, required=True)
@click.option('-p', '--path', 'path_arg', help='Project path')
@click.option('--max-files', type=int, default=12, help='Maximum files to include')
def explore(query_parts: List[str], path_arg: Optional[str], max_files: int):
    """Explore code: relevant symbols' source + call paths in one shot.

    QUERY_PARTS is one or more search terms for symbol lookup.
    Combines search, callers/callees, and source display in a single view.

    \b
    Examples:
      codegraph explore main          Find "main" and show source + call context
      codegraph explore ChatBackend   Explore ChatBackend class and its relationships

    Same output as the codegraph_explore MCP tool.
    """
    query = ' '.join(query_parts)
    project_path = resolve_project_path(path_arg)

    if not is_initialized(project_path):
        click.echo(red(
            f'CodeGraph not initialized in {project_path}. '
            f'Run "codegraph init" first.'
        ))
        return

    try:
        cg = CodeGraph.open_sync(project_path)
        results = cg.search_nodes(query, SearchOptions(limit=max_files))

        if not results:
            click.echo(f'No results found for "{query}"')
            cg.destroy()
            return

        # Group by file
        from collections import defaultdict
        files: Dict[str, List] = defaultdict(list)
        for r in results:
            files[r.node.file_path].append(r.node)

        for filepath, nodes in files.items():
            click.echo(bold(f'\n### {filepath}'))
            for node in nodes:
                sig = f' {node.signature}' if node.signature else ''
                click.echo(
                    f'  {blue(node.kind.ljust(12))} '
                    f'{bold(node.name)}{sig} '
                    f'({dim(f"line {node.start_line}")})'
                )
                # Show docstring if present
                if node.docstring:
                    doc = node.docstring.strip()[:150]
                    click.echo(f'  {dim("  " + doc)}')

        cg.destroy()

    except Exception as e:
        click.echo(red(f'Explore failed: {str(e)}'))
        sys.exit(1)


@main.command()
@click.argument('symbol')
@click.option('-p', '--path', 'path_arg', help='Project path')
@click.option('-d', '--depth', default=1, type=int, help='Traversal depth (1=direct, 2=indirect)')
def callers(symbol: str, path_arg: Optional[str], depth: int):
    """Find what calls a function/method.

    SYMBOL is the symbol name to find callers for (e.g. "ChatBackend", "main").

    Use -d 2 to include indirect callers (callers of callers).
    """
    project_path = resolve_project_path(path_arg)

    if not is_initialized(project_path):
        click.echo(red(f'CodeGraph not initialized in {project_path}'))
        return

    try:
        cg = CodeGraph.open_sync(project_path)
        nodes = cg.get_nodes_by_name(symbol)

        if not nodes:
            click.echo(yellow(f'Symbol not found: {symbol}'))
            cg.destroy()
            return

        callers_list = cg.get_callers(nodes[0].id, depth)

        if not callers_list:
            click.echo(f'No callers found for {symbol}')
        else:
            click.echo(bold(f'\nCallers of "{symbol}":\n'))
            seen = set()
            for caller_node, edge in callers_list:
                key = f'{caller_node.file_path}:{caller_node.start_line}'
                if key in seen:
                    continue
                seen.add(key)
                location = f'{caller_node.file_path}:{caller_node.start_line}'
                click.echo(f'  {blue(caller_node.kind.ljust(12))} {bold(caller_node.name)}')
                click.echo(f'  {dim("  " + location)}')
                click.echo()

        cg.destroy()

    except Exception as e:
        click.echo(red(f'Failed: {str(e)}'))
        sys.exit(1)


@main.command()
@click.argument('symbol')
@click.option('-p', '--path', 'path_arg', help='Project path')
@click.option('-d', '--depth', default=1, type=int, help='Traversal depth (1=direct, 2=indirect)')
def callees(symbol: str, path_arg: Optional[str], depth: int):
    """Find what a function/method calls.

    SYMBOL is the symbol name to find callees for (e.g. "run", "process").

    Use -d 2 to include indirect callees (callees of callees).
    """
    project_path = resolve_project_path(path_arg)

    if not is_initialized(project_path):
        click.echo(red(f'CodeGraph not initialized in {project_path}'))
        return

    try:
        cg = CodeGraph.open_sync(project_path)
        nodes = cg.get_nodes_by_name(symbol)

        if not nodes:
            click.echo(yellow(f'Symbol not found: {symbol}'))
            cg.destroy()
            return

        callees_list = cg.get_callees(nodes[0].id, depth)

        if not callees_list:
            click.echo(f'No callees found for {symbol}')
        else:
            click.echo(bold(f'\nCallees of "{symbol}":\n'))
            seen = set()
            for callee_node, edge in callees_list:
                key = f'{callee_node.file_path}:{callee_node.start_line}'
                if key in seen:
                    continue
                seen.add(key)
                location = f'{callee_node.file_path}:{callee_node.start_line}'
                click.echo(f'  {blue(callee_node.kind.ljust(12))} {bold(callee_node.name)}')
                click.echo(f'  {dim("  " + location)}')
                click.echo()

        cg.destroy()

    except Exception as e:
        click.echo(red(f'Failed: {str(e)}'))
        sys.exit(1)


@main.command()
@click.argument('symbol')
@click.option('-p', '--path', 'path_arg', help='Project path')
@click.option('-d', '--depth', default=3, type=int, help='Traversal depth')
@click.option('-j', '--json', 'json_output', is_flag=True, help='Output as JSON')
def impact(symbol: str, path_arg: Optional[str], depth: int, json_output: bool):
    """Analyze what code is affected by changing a symbol."""
    project_path = resolve_project_path(path_arg)

    if not is_initialized(project_path):
        click.echo(red(f'CodeGraph not initialized in {project_path}'))
        return

    try:
        cg = CodeGraph.open_sync(project_path)
        nodes = cg.get_nodes_by_name(symbol)

        if not nodes:
            click.echo(yellow(f'Symbol not found: {symbol}'))
            cg.destroy()
            return

        subgraph = cg.get_impact_radius(nodes[0].id, depth)

        if json_output:
            output = {
                'symbol': symbol,
                'nodeCount': len(subgraph.nodes),
                'edgeCount': len(subgraph.edges),
                'files': list(set(n.file_path for n in subgraph.nodes.values())),
                'nodes': [
                    {
                        'id': n.id,
                        'kind': n.kind,
                        'name': n.name,
                        'filePath': n.file_path,
                        'startLine': n.start_line,
                    }
                    for n in subgraph.nodes.values()
                ],
            }
            click.echo(json.dumps(output, indent=2))
            cg.destroy()
            return

        files = set(n.file_path for n in subgraph.nodes.values())
        click.echo(bold(f'\nImpact analysis for "{symbol}":\n'))
        click.echo(f'  {len(subgraph.nodes)} symbols affected')
        click.echo(f'  {len(subgraph.edges)} relationships')
        click.echo(f'  {len(files)} files involved')
        click.echo()

        if subgraph.nodes:
            click.echo(bold('Affected symbols:'))
            for n in subgraph.nodes.values():
                if n.id != nodes[0].id:
                    location = f'{n.file_path}:{n.start_line}'
                    click.echo(f'  {blue(n.kind.ljust(12))} {bold(n.name)}')
                    click.echo(f'  {dim("  " + location)}')

        cg.destroy()

    except Exception as e:
        click.echo(red(f'Failed: {str(e)}'))
        sys.exit(1)


@main.command()
@click.argument('files', nargs=-1)
@click.option('-p', '--path', 'path_arg', help='Project path')
def affected(files: List[str], path_arg: Optional[str]):
    """Find test files affected by changes."""
    project_path = resolve_project_path(path_arg)

    if not is_initialized(project_path):
        click.echo(red(f'CodeGraph not initialized in {project_path}'))
        return

    try:
        cg = CodeGraph.open_sync(project_path)

        affected_files: set = set()
        for filepath in files:
            # Get nodes in the changed file
            nodes = cg.get_nodes_by_file(filepath)
            for node in nodes:
                # Find what depends on these nodes
                dependents = cg.get_impact_radius(node.id, max_depth=2)
                for n in dependents.nodes.values():
                    if n.file_path.endswith('_test.py') or n.file_path.endswith('test_'):
                        affected_files.add(n.file_path)

        if not affected_files:
            click.echo('No affected test files found')
        else:
            click.echo(bold('\nAffected test files:\n'))
            for f in sorted(affected_files):
                click.echo(f'  {f}')

        cg.destroy()

    except Exception as e:
        click.echo(red(f'Failed: {str(e)}'))
        sys.exit(1)


@main.command()
@click.argument('name', required=True)
@click.option('--path', '-p', 'path_arg', default=None, help='Project path')
def node(name: str, path_arg: Optional[str]):
    """View details about a specific symbol node (source code, metadata)."""
    project_path = resolve_project_path(path_arg)

    if not is_initialized(project_path):
        click.echo(red(f'CodeGraph not initialized in {project_path}'))
        click.echo('Run "codegraph init" first')
        return

    try:
        cg = CodeGraph.open_sync(project_path)

        # Search for nodes by name
        opts = SearchOptions(limit=20)
        results = cg.search_nodes(name, opts)
        if not results:
            click.echo(yellow(f'No nodes found matching "{name}"'))
            cg.destroy()
            return

        # If exact match, show details
        exact_matches = [r for r in results if r.node.name == name]
        if exact_matches:
            target = exact_matches[0].node
        else:
            target = results[0].node

        click.echo(bold(f'\n📦 {target.kind}: {target.name}'))
        click.echo(f'  {dim("ID:")}       {target.id}')
        click.echo(f'  {dim("File:")}     {target.file_path}')
        click.echo(f'  {dim("Lines:")}    {target.start_line}-{target.end_line}')
        if target.signature:
            click.echo(f'  {dim("Signature:")} {target.signature}')
        if target.docstring:
            click.echo(f'  {dim("Docstring:")} {target.docstring}')
        if target.qualified_name:
            click.echo(f'  {dim("QName:")}    {target.qualified_name}')
        click.echo(f'  {dim("Exported:")} {green("yes") if target.is_exported else "no"}')

        # Show callers
        caller_pairs = cg.get_callers(target.id)
        if caller_pairs:
            click.echo(bold(f'\n  Callers ({len(caller_pairs)}):'))
            for c_node, c_edge in caller_pairs:
                click.echo(f'    ← {c_node.name} ({c_node.file_path}:{c_node.start_line})')

        # Show callees
        callee_pairs = cg.get_callees(target.id)
        if callee_pairs:
            click.echo(bold(f'\n  Callees ({len(callee_pairs)}):'))
            for c_node, c_edge in callee_pairs:
                click.echo(f'    → {c_node.name} ({c_node.file_path}:{c_node.start_line})')

        cg.destroy()

    except Exception as e:
        click.echo(red(f'Failed: {str(e)}'))
        sys.exit(1)


@main.command()
@click.option('--path', '-p', 'path_arg', default=None, help='Project path')
@click.option('--json-output', 'json_output', is_flag=True, help='Output as JSON')
@click.option('--by-directory', 'by_directory', is_flag=True, help='Group files by directory')
@click.option('--stats', 'show_stats', is_flag=True, help='Show per-file symbol stats (default when no flags)')
def files(path_arg: Optional[str], json_output: bool, by_directory: bool, show_stats: bool):
    """List indexed files with language and symbol statistics."""
    project_path = resolve_project_path(path_arg)

    if not is_initialized(project_path):
        click.echo(red(f'CodeGraph not initialized in {project_path}'))
        click.echo('Run "codegraph init" first')
        return

    try:
        cg = CodeGraph.open_sync(project_path)
        stats = cg.get_stats()
        all_files = cg._queries.get_all_file_paths()

        if json_output:
            file_list = []
            for f in sorted(all_files):
                nodes = cg.get_nodes_by_file(f)
                langs = set(n.language for n in nodes if n.language)
                kinds = {}
                for n in nodes:
                    kinds[n.kind] = kinds.get(n.kind, 0) + 1
                file_list.append({
                    'path': f,
                    'node_count': len(nodes),
                    'languages': list(langs),
                    'node_kinds': kinds,
                })
            result = {
                'total_files': len(all_files),
                'total_nodes': stats.node_count,
                'total_edges': stats.edge_count,
                'files': file_list,
            }
            click.echo(json.dumps(result, indent=2))
            cg.destroy()
            return

        show_stats = show_stats or not by_directory

        if by_directory:
            # Group files by directory
            dirs: Dict[str, List[str]] = {}
            for f in sorted(all_files):
                d = os.path.dirname(f) or '.'
                dirs.setdefault(d, []).append(f)
            click.echo(bold(f'\n📁 Indexed Files ({len(all_files)} total, {len(dirs)} directories)\n'))
            for directory in sorted(dirs):
                click.echo(blue(f'  {directory}/'))
                for f in dirs[directory]:
                    click.echo(f'    {os.path.basename(f)}')
            click.echo()
        else:
            click.echo(bold(f'\n📁 Indexed Files ({len(all_files)} total)\n'))
            for f in sorted(all_files):
                nodes = cg.get_nodes_by_file(f)
                lang = ''
                for n in nodes:
                    if n.language and n.language != 'unknown':
                        lang = n.language
                        break
                if show_stats:
                    kinds = {}
                    for n in nodes:
                        k = n.kind
                        kinds[k] = kinds.get(k, 0) + 1
                    kind_str = ', '.join(f'{v} {k}' for k, v in sorted(kinds.items()) if k != 'file')
                    if kind_str:
                        click.echo(f'  {dim(lang+"  ") if lang else ""}{f}  {dim("("+kind_str+")")}')
                    else:
                        click.echo(f'  {f}')
                else:
                    click.echo(f'  {dim(lang+"  ") if lang else ""}{f}')

        cg.destroy()

    except Exception as e:
        click.echo(red(f'Failed: {str(e)}'))
        sys.exit(1)


@main.command()
@click.option('--path', '-p', 'path_arg', default=None, help='Project path')
def install(path_arg: Optional[str]):
    """Install shell command integration (alias, completion)."""
    project_path = resolve_project_path(path_arg)
    click.echo(bold('\n🔧 CodeGraph Shell Integration'))
    click.echo()
    click.echo('To add CodeGraph to your shell, add one of the following to your')
    click.echo('shell configuration file (~/.bashrc, ~/.zshrc, etc.):')
    click.echo()
    click.echo(dim('  # Bash/Zsh'))
    click.echo(f'  alias cg="codegraph"')
    click.echo()
    click.echo(dim('  # Or for click shell completion:'))
    click.echo('  eval "$(_CODEGRAPH_COMPLETE=bash_source codegraph)"  # Bash')
    click.echo('  eval "$(_CODEGRAPH_COMPLETE=zsh_source codegraph)"   # Zsh')
    click.echo('  codegraph completion install                       # Fish')
    click.echo()
    click.echo(green('  CodeGraph is ready at: ' + project_path))
    click.echo()


@main.command()
@click.argument('path', required=False, default=None)
@click.option('-w', '--watch', is_flag=True, help='Watch for file changes and auto-sync')
@click.option('--verbose', '-v', is_flag=True, help='Log sync info to stderr')
def serve(path: Optional[str], watch: bool, verbose: bool):
    """Start MCP server for AI agent integration.

    Listens on stdin/stdout using the Model Context Protocol.
    AI assistants connect to this server to query the code graph.
    """
    project_path = resolve_project_path(path)

    if not is_initialized(project_path):
        click.echo(red(f'CodeGraph not initialized in {project_path}'))
        click.echo('Run "codegraph init" first')
        return

    click.echo(f'Starting CodeGraph MCP server for {project_path}...')
    if watch:
        click.echo(green('  File watching enabled — auto-syncing on changes'))

    from codegraph.mcp import MCPServer
    server = MCPServer(project_path, watch=watch)

    import asyncio
    async def run():
        await server.start()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        click.echo('\nServer stopped')


@main.command()
@click.argument('path', required=False, default=None)
def unlock(path: Optional[str]):
    """Remove stale lock file."""
    project_path = resolve_project_path(path)
    lock_path = os.path.join(project_path, '.codegraph', 'codegraph.lock')

    if os.path.isfile(lock_path):
        os.remove(lock_path)
        click.echo(green('Lock file removed'))
    else:
        click.echo('No lock file found')


if __name__ == '__main__':
    main()
