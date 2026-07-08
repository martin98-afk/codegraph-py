# codegraph-py

**Python 版 CodeGraph** — 基于知识图谱的语义代码智能引擎，为 AI 编程助手提供精准的代码上下文。

> 对代码库建立预索引的知识图谱。AI Agent 直接查询图结构而非逐文件扫描 —— 精准上下文、更少工具调用、更快回答。

---

## 特性

- 🔍 **多语言 AST 解析** — 基于 tree-sitter 的符号级解析（Python/JS/TS/Go/Java/Rust，更多开发中）
- 🧠 **知识图谱** — 函数、类、方法、接口之间的包含、调用、继承关系全量索引
- ⚡ **FTS5 全文搜索** — 毫秒级符号查找，支持 camelCase / snake_case 分词
- 🔗 **调用链分析** — `callers` / `callees` / `impact` 精准定位影响范围
- 🏗 **增量同步** — 仅索引变更文件，监听文件系统变更
- 🛠 **MCP 服务器** — 8 个工具，直接对接 Claude/Cursor 等 AI Agent
- 🚀 **SQLite WAL 模式** — 256MB 内存映射 I/O，批量 checkpoint，生产级性能

## 快速开始

```bash
# 安装
pip install codegraph-py

# 在项目目录初始化索引
cd your-project
codegraph init

# 搜索符号
codegraph query "calculateTotal"

# 探索代码上下文
codegraph explore "How does authentication work?"

# 查看索引状态
codegraph status
```

## CLI 命令

| 命令 | 描述 |
|------|------|
| `init [path]` | 初始化并建立索引 |
| `uninit [path]` | 移除 CodeGraph 索引 |
| `index [path]` | 重建完整索引 |
| `sync [path]` | 增量同步变更 |
| `status [path]` | 显示索引统计 |
| `query <search>` | 全文搜索符号 |
| `explore <query>` | 探索代码（符号源码 + 调用路径） |
| `node <name>` | 查看符号详细信息 |
| `files` | 列出索引文件及符号统计 |
| `callers <symbol>` | 查找调用者 |
| `callees <symbol>` | 查找被调用者 |
| `impact <symbol>` | 影响范围分析 |
| `affected [files]` | 查找受影响的测试文件 |
| `serve [path]` | 启动 MCP 服务器 |
| `unlock [path]` | 移除陈旧锁文件 |
| `install` | CLI 集成指南 |

## Python API

```python
from codegraph import CodeGraph

# 初始化或打开项目
cg = CodeGraph.init_sync('/path/to/project')

# 全量索引
result = cg.index_all()
print(f"Indexed {result.files_indexed} files, {result.nodes_created} nodes")

# 搜索符号
results = cg.search_nodes("calculateTotal")
for r in results:
    print(f"{r.node.kind}: {r.node.name} ({r.node.file_path}:{r.node.start_line})")

# 调用链分析
callers = cg.get_callers(node.id)
callees = cg.get_callees(node.id)

# 影响分析
impact = cg.get_impact_radius(node.id)
print(f"{len(impact.nodes)} symbols affected by change")

# 统计信息
stats = cg.get_stats()
print(f"Files: {stats.file_count}, Nodes: {stats.node_count}")

# 关闭连接
cg.close()
```

## MCP 服务器

启动 MCP 服务器供 AI Agent 集成：

```bash
codegraph serve
```

暴露的 MCP 工具：

| 工具 | 功能 |
|------|------|
| `codegraph_explore` | 主探索工具 — 自然语言查询符号和上下文 |
| `codegraph_node` | 获取符号或文件的源码 |
| `codegraph_search` | 快速符号搜索 |
| `codegraph_callers` | 查找函数调用者 |
| `codegraph_callees` | 查找函数被调用者 |
| `codegraph_impact` | 影响范围分析 |
| `codegraph_status` | 索引健康检查 |
| `codegraph_files` | 列出索引文件 |

## 工作原理

1. **`codegraph init`** 在项目根创建 `.codegraph/` 目录（含 SQLite 数据库 + `.gitignore`）
2. **索引阶段** 扫描源文件，用 tree-sitter 解析 AST，提取符号（函数、类、方法、接口等），存入节点和边
3. **FTS5 全文索引** 支持毫秒级符号搜索，智能分词 camelCase / snake_case
4. **图遍历引擎** 沿边（调用、包含、继承等）查找调用链和影响范围
5. **增量同步** 通过文件 mtime 对比实现，仅处理变更文件
6. **MCP 服务器** 基于 JSON-RPC 2.0 stdio 协议，8 个工具直连 AI Agent

## 技术栈

| 组件 | 技术 |
|------|------|
| 数据库 | SQLite (WAL + mmap + FTS5) |
| AST 解析 | tree-sitter (Python/JS/TS/Go/Java/Rust) |
| CLI | Click |
| MCP | JSON-RPC 2.0 over stdio |
| 文件监控 | watchfiles（可选） |

## 性能

在 **原版 CodeGraph TypeScript 项目**（329 个源文件）上的压测结果：

| 指标 | 数据 |
|------|------|
| 索引文件数 | 329 |
| 提取节点数 | 2,349（1,069 函数 + 699 方法 + 153 接口 + 60 类 + 39 类型别名） |
| 索引耗时 | **3.7 秒** |
| 数据库 PRAGMA | WAL + 256MB mmap + 64MB cache + NORMAL sync |

## 支持的语言

### tree-sitter 完整 AST 解析
- Python ✅
- JavaScript / JSX ✅
- TypeScript / TSX ✅
- Go ✅
- Java ✅
- Rust ✅

### 更多语言（文件级别跟踪，AST 解析即将支持）
- C/C++, C#, PHP, Ruby, Swift, Kotlin, Dart, Scala, Lua, Objective-C, R, Solidity, Terraform, 等 50+ 种

## 安装

```bash
# 基础安装
pip install codegraph-py

# 带 tree-sitter 多语言支持
pip install "codegraph-py[ts]"

# 带文件监控
pip install "codegraph-py[watch]"

# 全功能
pip install "codegraph-py[all]"
```

## 项目结构

```
codegraph/
├── codegraph/
│   ├── cli.py                    # 16 个 CLI 命令
│   ├── codegraph.py              # 核心 CodeGraph 类
│   ├── types.py                  # 类型定义
│   ├── db/                       # SQLite 数据库层
│   │   ├── connection.py         # 连接管理 + PRAGMA 优化
│   │   ├── queries.py            # 查询构建器
│   │   └── schema.sql            # 数据库 schema
│   ├── extraction/               # 代码解析引擎
│   │   ├── tree_sitter_extractor.py  # tree-sitter 核心提取器
│   │   └── languages/            # 语言配置（6 种）
│   ├── graph/                    # 图遍历引擎
│   ├── search/                   # FTS5 全文搜索
│   ├── mcp/                      # MCP 服务器
│   ├── sync/                     # 文件同步/监控
│   └── resolution/               # 引用解析
├── tests/
│   └── test_codegraph.py         # 11 个单元测试
└── pyproject.toml
```

## 许可证

MIT — 基于原版 [CodeGraph](https://github.com/colbymchenry/codegraph) TypeScript 项目。
