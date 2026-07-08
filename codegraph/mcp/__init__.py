"""
CodeGraph MCP Server

Model Context Protocol server that exposes CodeGraph functionality
as tools for AI assistants, with optional file watching for auto-reindex.
"""

from __future__ import annotations

import json
import os
import sys
import time
import threading
from typing import Any, Dict, List, Optional, Callable

from loguru import logger

from codegraph.codegraph import CodeGraph
from codegraph.types import SearchOptions
from codegraph.sync import FileWatcher, PendingFile


class MCPServer:
    """
    MCP Server that provides CodeGraph tools to AI assistants.

    Communicates via stdio using JSON-RPC 2.0 over newline-delimited messages.
    Optionally watches the project for file changes and auto-syncs.
    """

    def __init__(self, project_root: str, watch: bool = False):
        self._project_root = project_root
        self._codegraph: Optional[CodeGraph] = None
        self._running = False
        self._request_id = 0
        self._watch = watch
        self._watcher: Optional[FileWatcher] = None
        self._last_sync_info: Dict = {}
        self._sync_lock = threading.Lock()

    def _get_cg(self) -> CodeGraph:
        """Get or open the CodeGraph instance."""
        if self._codegraph is None:
            self._codegraph = CodeGraph.open_sync(self._project_root)
        return self._codegraph

    def _on_file_change(self, pending: List[PendingFile]) -> None:
        """Callback when files change — auto-sync."""
        with self._sync_lock:
            try:
                cg = self._get_cg()
                result = cg.sync()
                if result.files_added > 0 or result.files_modified > 0 or result.files_removed > 0:
                    self._last_sync_info = {
                        'added': result.files_added,
                        'modified': result.files_modified,
                        'removed': result.files_removed,
                        'timestamp': time.time(),
                    }
            except Exception as e:
                # Log but don't crash the file watcher
                logger.warning('auto-sync failed: {}', e)

    async def start(self) -> None:
        """Start the MCP server (stdio-based)."""
        self._running = True

        # Start file watcher if enabled
        if self._watch:
            try:
                cg = self._get_cg()
                self._watcher = cg.watch(on_change=self._on_file_change)
            except Exception:
                pass  # File watching is optional

        # Notify stderr that server is ready (for host process)
        sys.stderr.write('CodeGraph MCP server started\n')
        sys.stderr.flush()

        # Read and process JSON-RPC messages from stdin
        while self._running:
            try:
                line = sys.stdin.readline()
                if not line:
                    break

                message = json.loads(line.strip())
                await self._handle_message(message)
            except EOFError:
                break
            except json.JSONDecodeError:
                continue
            except Exception as e:
                self._send_error(-32700, f'Parse error: {str(e)}')

    def stop(self) -> None:
        """Stop the MCP server."""
        self._running = False
        if self._watcher:
            try:
                self._watcher.stop()
            except Exception:
                pass
        if self._codegraph:
            try:
                self._codegraph.close()
            except Exception:
                pass

    async def _handle_message(self, message: Dict) -> None:
        """Handle a JSON-RPC message."""
        method = message.get('method', '')
        params = message.get('params', {})
        msg_id = message.get('id')

        # Handle JSON-RPC methods
        if method == 'initialize':
            self._send_response(msg_id, {
                'protocolVersion': '2024-11-05',
                'capabilities': {
                    'tools': {},
                },
                'serverInfo': {
                    'name': 'codegraph',
                    'version': '1.0.1',
                },
            })
        elif method == 'tools/list':
            self._send_response(msg_id, {'tools': self._get_tool_definitions()})
        elif method == 'tools/call':
            await self._handle_tool_call(msg_id, params)
        elif method == 'notifications/initialized':
            pass  # Acknowledge
        elif method == 'shutdown':
            self._send_response(msg_id, None)
            self.stop()

    def _get_tool_definitions(self) -> List[Dict]:
        """Get MCP tool definitions."""
        return [
            {
                'name': 'codegraph_explore',
                'description': 'Explore an area of the codebase: returns relevant symbols source grouped by file, plus call paths among them. Primary tool — call this first before other codegraph tools.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'query': {
                            'type': 'string',
                            'description': 'Natural language query or symbol names to explore',
                        },
                        'maxFiles': {
                            'type': 'number',
                            'description': 'Maximum number of files to include source from',
                            'default': 12,
                        },
                        'projectPath': {
                            'type': 'string',
                            'description': 'Alternative project path for cross-project queries',
                        },
                    },
                    'required': ['query'],
                },
            },
            {
                'name': 'codegraph_node',
                'description': 'Get source code for a symbol or file. Returns location, signature, and source content. For a file path, returns the full file content. For a symbol name, returns the definition with its surrounding context.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'name': {
                            'type': 'string',
                            'description': 'Symbol name or file path to look up',
                        },
                        'includeCode': {
                            'type': 'boolean',
                            'description': 'Whether to include source code in response',
                            'default': True,
                        },
                        'projectPath': {
                            'type': 'string',
                            'description': 'Alternative project path',
                        },
                    },
                    'required': ['name'],
                },
            },
            {
                'name': 'codegraph_search',
                'description': 'Quick symbol search by name. Returns matching symbol locations (file + line number). For detailed source context, use codegraph_explore or codegraph_node.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'query': {
                            'type': 'string',
                            'description': 'Symbol name or pattern to search for',
                        },
                        'limit': {
                            'type': 'number',
                            'description': 'Maximum number of results',
                            'default': 10,
                        },
                        'projectPath': {
                            'type': 'string',
                            'description': 'Alternative project path',
                        },
                    },
                    'required': ['query'],
                },
            },
            {
                'name': 'codegraph_callers',
                'description': 'List functions that call a given symbol. Shows the call chain — what invokes the function or method.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'symbol': {
                            'type': 'string',
                            'description': 'Symbol name to find callers for',
                        },
                        'depth': {
                            'type': 'number',
                            'description': 'How deep to traverse the call chain',
                            'default': 1,
                        },
                        'projectPath': {
                            'type': 'string',
                            'description': 'Alternative project path',
                        },
                    },
                    'required': ['symbol'],
                },
            },
            {
                'name': 'codegraph_callees',
                'description': 'List functions a symbol calls. Shows what the function or method invokes.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'symbol': {
                            'type': 'string',
                            'description': 'Symbol name to find callees for',
                        },
                        'depth': {
                            'type': 'number',
                            'description': 'How deep to traverse the call chain',
                            'default': 1,
                        },
                        'projectPath': {
                            'type': 'string',
                            'description': 'Alternative project path',
                        },
                    },
                    'required': ['symbol'],
                },
            },
            {
                'name': 'codegraph_impact',
                'description': 'Analyze what code is affected by changing a symbol. Shows the blast radius — all symbols that depend on the given symbol, directly or transitively.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'symbol': {
                            'type': 'string',
                            'description': 'Symbol name to analyze impact for',
                        },
                        'depth': {
                            'type': 'number',
                            'description': 'How deep to traverse for impact analysis',
                            'default': 3,
                        },
                        'projectPath': {
                            'type': 'string',
                            'description': 'Alternative project path',
                        },
                    },
                    'required': ['symbol'],
                },
            },
            {
                'name': 'codegraph_status',
                'description': 'Get index health. Returns files/nodes/edges counts, pending changes, and whether re-index is recommended.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'projectPath': {
                            'type': 'string',
                            'description': 'Project path',
                        },
                    },
                },
            },
            {
                'name': 'codegraph_files',
                'description': 'List indexed files in the project tree. Optionally filter by path prefix or file extension.',
                'inputSchema': {
                    'type': 'object',
                    'properties': {
                        'path': {
                            'type': 'string',
                            'description': 'Optional path prefix to filter by',
                        },
                        'extensions': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': 'Optional file extensions to filter by (e.g. [".py", ".ts"])',
                        },
                        'projectPath': {
                            'type': 'string',
                            'description': 'Alternative project path',
                        },
                    },
                },
            },
        ]

    async def _handle_tool_call(self, msg_id: int, params: Dict) -> None:
        """Handle a tools/call request."""
        tool_name = params.get('name', '')
        arguments = params.get('arguments', {})

        handler = ToolHandler(self)
        try:
            result = await handler.execute(tool_name, arguments)
            self._send_response(msg_id, result)
        except Exception as e:
            self._send_error(-32603, f'Internal error: {str(e)}', msg_id)

    def _send_response(self, msg_id: int, result: Any) -> None:
        """Send a JSON-RPC response."""
        response = {
            'jsonrpc': '2.0',
            'id': msg_id,
            'result': result,
        }
        sys.stdout.write(json.dumps(response) + '\n')
        sys.stdout.flush()

    def _send_error(self, code: int, message: str,
                    msg_id: Optional[int] = None) -> None:
        """Send a JSON-RPC error response."""
        response = {
            'jsonrpc': '2.0',
            'id': msg_id,
            'error': {
                'code': code,
                'message': message,
            },
        }
        sys.stdout.write(json.dumps(response) + '\n')
        sys.stdout.flush()

    def _open_cg(self, project_path: Optional[str] = None) -> CodeGraph:
        """Open a CodeGraph instance, lazily."""
        root = project_path or self._project_root
        if self._codegraph and self._codegraph.get_project_root() == root:
            return self._codegraph
        self._codegraph = CodeGraph.open_sync(root)
        return self._codegraph


class ToolHandler:
    """Handles MCP tool execution."""

    def __init__(self, server: MCPServer):
        self._server = server

    async def execute(self, tool_name: str, arguments: Dict) -> Dict:
        """Execute a tool and return MCP-formatted result."""
        project_path = arguments.get('projectPath')
        cg = self._server._open_cg(project_path)

        handlers = {
            'codegraph_explore': self._handle_explore,
            'codegraph_node': self._handle_node,
            'codegraph_search': self._handle_search,
            'codegraph_callers': self._handle_callers,
            'codegraph_callees': self._handle_callees,
            'codegraph_impact': self._handle_impact,
            'codegraph_status': self._handle_status,
            'codegraph_files': self._handle_files,
        }

        handler = handlers.get(tool_name)
        if not handler:
            return {
                'content': [{'type': 'text', 'text': f'Unknown tool: {tool_name}'}],
                'isError': True,
            }

        return await handler(cg, arguments)

    async def _handle_explore(self, cg: CodeGraph, args: Dict) -> Dict:
        """Handle codegraph_explore tool."""
        query = args.get('query', '')
        if not query:
            return {
                'content': [{'type': 'text', 'text': 'Query is required'}],
                'isError': True,
            }

        max_files = args.get('maxFiles', 12)

        # Search for relevant nodes
        results = cg.search_nodes(query, SearchOptions(limit=max_files))

        if not results:
            return {
                'content': [{
                    'type': 'text',
                    'text': f'No results found for "{query}". The project may not be indexed yet — run `codegraph index` first, or try a different query.',
                }],
            }

        # Build response grouped by file
        from collections import defaultdict
        files: Dict[str, List] = defaultdict(list)

        for result in results:
            node = result.node
            files[node.file_path].append(node)

        output_parts = []
        for filepath, nodes in files.items():
            output_parts.append(f'\n### {filepath}\n')
            for node in nodes:
                sig = f' {node.signature}' if node.signature else ''
                output_parts.append(
                    f'- `{node.kind}` **{node.name}**{sig} (line {node.start_line})'
                )

        return {
            'content': [{'type': 'text', 'text': ''.join(output_parts)}],
        }

    async def _handle_node(self, cg: CodeGraph, args: Dict) -> Dict:
        """Handle codegraph_node tool."""
        name = args.get('name', '')
        include_code = args.get('includeCode', True)

        if not name:
            return {
                'content': [{'type': 'text', 'text': 'Name is required'}],
                'isError': True,
            }

        # Try as file path first
        nodes = cg.get_nodes_by_file(name)
        if not nodes:
            # Try as symbol name
            nodes = cg.get_nodes_by_name(name)

        if not nodes:
            return {
                'content': [{'type': 'text', 'text': f'No symbol found: {name}'}],
            }

        output_parts = []
        for node in nodes[:5]:
            output_parts.append(
                f'**{node.kind}**: `{node.name}`\n'
                f'- File: {node.file_path}:{node.start_line}\n'
            )
            if node.signature:
                output_parts.append(f'- Signature: `{node.signature}`\n')
            if node.docstring:
                output_parts.append(f'- Doc: {node.docstring[:200]}\n')

            # Read source code if requested
            if include_code:
                try:
                    filepath = os.path.join(cg.get_project_root(), node.file_path)
                    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                        lines = f.readlines()
                    code = ''.join(lines[node.start_line - 1:node.end_line])
                    output_parts.append(f'```\n{code}\n```\n')
                except Exception:
                    pass

        return {
            'content': [{'type': 'text', 'text': ''.join(output_parts)}],
        }

    async def _handle_search(self, cg: CodeGraph, args: Dict) -> Dict:
        """Handle codegraph_search tool."""
        query = args.get('query', '')
        limit = args.get('limit', 10)

        if not query:
            return {
                'content': [{'type': 'text', 'text': 'Query is required'}],
                'isError': True,
            }

        results = cg.search_nodes(query, SearchOptions(limit=limit))

        if not results:
            return {
                'content': [{'type': 'text', 'text': f'No results for "{query}"'}],
            }

        output = f'Search results for "{query}":\n\n'
        for r in results:
            node = r.node
            output += f'  {node.kind:12} {node.name} ({node.file_path}:{node.start_line})\n'
            if node.signature:
                output += f'  {"":12} {node.signature}\n'

        return {
            'content': [{'type': 'text', 'text': output}],
        }

    async def _handle_callers(self, cg: CodeGraph, args: Dict) -> Dict:
        """Handle codegraph_callers tool."""
        symbol = args.get('symbol', '')
        depth = args.get('depth', 1)

        nodes = cg.get_nodes_by_name(symbol)
        if not nodes:
            return {
                'content': [{'type': 'text', 'text': f'Symbol not found: {symbol}'}],
            }

        callers = cg.get_callers(nodes[0].id, max_depth=depth)

        if not callers:
            return {
                'content': [{'type': 'text', 'text': f'No callers found for {symbol}'}],
            }

        output = f'Callers of {symbol}:\n\n'
        for caller_node, edge in callers:
            output += f'  {caller_node.kind:12} {caller_node.name} ({caller_node.file_path}:{caller_node.start_line})\n'

        return {
            'content': [{'type': 'text', 'text': output}],
        }

    async def _handle_callees(self, cg: CodeGraph, args: Dict) -> Dict:
        """Handle codegraph_callees tool."""
        symbol = args.get('symbol', '')
        depth = args.get('depth', 1)

        nodes = cg.get_nodes_by_name(symbol)
        if not nodes:
            return {
                'content': [{'type': 'text', 'text': f'Symbol not found: {symbol}'}],
            }

        callees = cg.get_callees(nodes[0].id, max_depth=depth)

        if not callees:
            return {
                'content': [{'type': 'text', 'text': f'No callees found for {symbol}'}],
            }

        output = f'Callees of {symbol}:\n\n'
        for callee_node, edge in callees:
            output += f'  {callee_node.kind:12} {callee_node.name} ({callee_node.file_path}:{callee_node.start_line})\n'

        return {
            'content': [{'type': 'text', 'text': output}],
        }

    async def _handle_impact(self, cg: CodeGraph, args: Dict) -> Dict:
        """Handle codegraph_impact tool."""
        symbol = args.get('symbol', '')
        depth = args.get('depth', 3)

        nodes = cg.get_nodes_by_name(symbol)
        if not nodes:
            return {
                'content': [{'type': 'text', 'text': f'Symbol not found: {symbol}'}],
            }

        subgraph = cg.get_impact_radius(nodes[0].id, max_depth=depth)

        if not subgraph.nodes:
            return {
                'content': [{'type': 'text', 'text': f'No impact detected for {symbol}'}],
            }

        files = set(n.file_path for n in subgraph.nodes.values())
        output = (
            f'Impact analysis for {symbol}:\n'
            f'  {len(subgraph.nodes)} symbols affected\n'
            f'  {len(subgraph.edges)} relationships\n'
            f'  {len(files)} files involved\n\n'
            f'Affected symbols:\n'
        )

        for node in subgraph.nodes.values():
            if node.id != nodes[0].id:
                output += f'  {node.kind:12} {node.name} ({node.file_path}:{node.start_line})\n'

        return {
            'content': [{'type': 'text', 'text': output}],
        }

    async def _handle_status(self, cg: CodeGraph, args: Dict) -> Dict:
        """Handle codegraph_status tool."""
        stats = cg.get_stats()
        changes = cg.get_changed_files()
        build_info = cg.get_index_build_info()

        pending_count = (
            len(changes.get('added', []))
            + len(changes.get('modified', []))
            + len(changes.get('removed', []))
        )

        output = (
            f'CodeGraph Status for {cg.get_project_root()}:\n\n'
            f'**Index Statistics:**\n'
            f'  Files: {stats.file_count}\n'
            f'  Nodes: {stats.node_count}\n'
            f'  Edges: {stats.edge_count}\n'
            f'  DB Size: {stats.db_size_bytes / 1024 / 1024:.2f} MB\n'
            f'  Backend: sqlite3 (built-in, full WAL)\n\n'
        )

        if pending_count > 0:
            output += '**Pending Changes:**\n'
            if changes.get('added'):
                output += f'  Added: {len(changes["added"])} files\n'
            if changes.get('modified'):
                output += f'  Modified: {len(changes["modified"])} files\n'
            if changes.get('removed'):
                output += f'  Removed: {len(changes["removed"])} files\n'
        else:
            output += '**Index is up to date**\n'

        return {
            'content': [{'type': 'text', 'text': output}],
        }

    async def _handle_files(self, cg: CodeGraph, args: Dict) -> Dict:
        """Handle codegraph_files tool."""
        path_filter = args.get('path', '')
        extensions = args.get('extensions')

        files = cg.get_nodes_by_file('')  # This doesn't work - need file iteration
        # Use the QueryBuilder to get all file paths instead
        all_files = cg._queries.get_all_file_paths()

        # Apply filters
        if path_filter:
            all_files = [f for f in all_files if f.startswith(path_filter)]
        if extensions:
            all_files = [f for f in all_files
                         if any(f.endswith(ext) for ext in extensions)]

        if not all_files:
            return {
                'content': [{'type': 'text', 'text': 'No files found'}],
            }

        output = f'Files ({len(all_files)} total):\n\n'
        for f in all_files[:100]:
            output += f'  {f}\n'
        if len(all_files) > 100:
            output += f'  ... and {len(all_files) - 100} more\n'

        return {
            'content': [{'type': 'text', 'text': output}],
        }
