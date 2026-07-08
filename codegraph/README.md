# CodeGraph

**Python version** — Semantic Code Intelligence for AI coding agents.

> Build a pre-indexed knowledge graph of your codebase. AI agents query it directly instead of crawling files — surgical context, fewer tool calls, faster answers.

---

## Quick Start

```bash
# Install
pip install codegraph

# Create a new index
cd your-project
codegraph init

# Search for symbols
codegraph query "calculateTotal"

# Explore code
codegraph explore "How does authentication work?"

# Check status
codegraph status
```

## Commands

| Command | Description |
|---------|-------------|
| `init [path]` | Initialize CodeGraph in a project directory and build initial index |
| `uninit [path]` | Remove CodeGraph from a project |
| `index [path]` | Rebuild the full index from scratch |
| `sync [path]` | Sync changes since last index |
| `status [path]` | Show index status and statistics |
| `query <search>` | Search for symbols in the codebase |
| `explore <query...>` | Explore code — source + call paths |
| `callers <symbol>` | Find what calls a function/method |
| `callees <symbol>` | Find what a function/method calls |
| `impact <symbol>` | Analyze what code is affected by changing a symbol |
| `affected [files...]` | Find test files affected by changes |
| `serve [path]` | Start MCP server for AI agent integration |
| `unlock [path]` | Remove stale lock file |

## Python API

```python
from codegraph import CodeGraph

# Initialize or open a project
cg = CodeGraph.init_sync('/path/to/project')
# or: cg = await CodeGraph.open('/path/to/project')

# Index all files
result = cg.index_all()
print(f"Indexed {result.files_indexed} files, {result.nodes_created} nodes")

# Search
results = cg.search_nodes("calculateTotal")
for r in results:
    print(f"{r.node.kind}: {r.node.name} ({r.node.file_path}:{r.node.start_line})")

# Get callers/callees
callers = cg.get_callers(node.id)
callees = cg.get_callees(node.id)

# Impact analysis
impact = cg.get_impact_radius(node.id)
print(f"{len(impact.nodes)} symbols affected")

# Statistics
stats = cg.get_stats()
print(f"Files: {stats.file_count}, Nodes: {stats.node_count}")

# Close when done
cg.close()
```

## MCP Server

Start the MCP server for AI agent integration:

```bash
codegraph serve
```

The MCP server exposes these tools:
- `codegraph_explore` — Primary exploration tool
- `codegraph_node` — Get source code for a symbol
- `codegraph_search` — Quick symbol search
- `codegraph_callers` — Find function callers
- `codegraph_callees` — Find function callees
- `codegraph_impact` — Impact analysis
- `codegraph_status` — Index health check
- `codegraph_files` — List indexed files

## How It Works

1. **`codegraph init`** creates a `.codegraph/` directory with a SQLite database
2. **Indexing** scans source files, parses them to extract code symbols (functions, classes, methods), and stores nodes + edges in the database
3. **FTS5 full-text search** enables fast symbol lookup
4. **Graph traversal** follows edges (calls, contains, extends, etc.) to find callers, callees, and impact chains
5. **MCP server** exposes all functionality as AI-agent-friendly tools

## Supported Languages

- **Python** — full support (regex-based parser, tree-sitter coming soon)
- More languages coming (tree-sitter integration planned)

## License

MIT — based on the original [CodeGraph](https://github.com/colbymchenry/codegraph) TypeScript project.
