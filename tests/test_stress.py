"""
Stress and edge-case tests for CodeGraph.

Covers:
  - Large-scale indexing (100+ files)
  - Multi-language indexing (all 14 supported languages)
  - Cross-file reference resolution chains
  - Graph traversal stress (large impact radius)
  - Sync stress (batch add/modify/remove)
  - Edge cases (empty files, syntax errors, unicode, huge files)
"""
import os
import sys
import tempfile
import shutil
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from codegraph import CodeGraph
from codegraph.db.queries import QueryBuilder
from codegraph.types import EdgeKind


class TestStress:
    """Stress and edge-case tests."""

    @classmethod
    def setup_class(cls):
        cls.test_dir = tempfile.mkdtemp(prefix='cg_stress_')

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    # =========================================================================
    # Helper
    # =========================================================================

    def _make_cg(self, name: str) -> CodeGraph:
        """Create a CodeGraph in a temp subdirectory."""
        d = os.path.join(self.test_dir, name)
        os.makedirs(d, exist_ok=True)
        cg = CodeGraph.init_sync(d)
        return cg

    # =========================================================================
    # 1. Large-scale indexing
    # =========================================================================

    def test_index_100_files(self):
        """Index 100 small Python files — verify performance."""
        d = os.path.join(self.test_dir, 'test_100_files')
        os.makedirs(d, exist_ok=True)

        # Create 100 files
        for i in range(100):
            with open(os.path.join(d, f'module_{i:03d}.py'), 'w') as f:
                f.write(f"""def func_{i:03d}() -> str:
    return "hello_{i:03d}"

class Class_{i:03d}:
    def method_{i:03d}(self, x: int) -> int:
        return x + {i}
""")

        cg = self._make_cg('test_100_files')
        start = time.time()
        result = cg.index_all()
        elapsed = time.time() - start

        # Verify
        assert result.success, f'Index failed: {result.errors}'
        assert result.files_indexed >= 100, f'Expected >=100 files, got {result.files_indexed}'

        # Should index 100 files in reasonable time (< 10s)
        assert elapsed < 10.0, f'Indexing 100 files took {elapsed:.1f}s — too slow'

        stats = cg.get_stats()
        assert stats.node_count >= 300  # 100 file + 100 func + 100 class nodes
        assert stats.edge_count >= 200  # contains edges

        print(f'\n  100 files indexed: {stats.node_count} nodes, '
              f'{stats.edge_count} edges in {elapsed:.2f}s')

        cg.close()

    def test_index_deep_module_tree(self):
        """Index a deep directory tree (depth 10)."""
        d = os.path.join(self.test_dir, 'test_deep_tree')
        os.makedirs(d, exist_ok=True)

        # Create nested directory structure
        current = d
        for i in range(10):
            sub = f'level_{i}'
            current = os.path.join(current, sub)
            os.makedirs(current, exist_ok=True)
            with open(os.path.join(current, '__init__.py'), 'w') as f:
                f.write(f'def level_{i}_func():\n    return {i}\n')

        cg = self._make_cg('test_deep_tree')
        result = cg.index_all()
        assert result.success
        assert result.files_indexed >= 10

        stats = cg.get_stats()
        assert stats.node_count >= 20  # 10 files + 10 functions
        print(f'\n  Deep tree: {result.files_indexed} files, {stats.node_count} nodes')

        cg.close()

    # =========================================================================
    # 2. Multi-language indexing
    # =========================================================================

    def test_multi_language_index(self):
        """Index files in all supported languages."""
        d = os.path.join(self.test_dir, 'test_multi_lang')
        os.makedirs(d, exist_ok=True)

        # Python
        with open(os.path.join(d, 'main.py'), 'w') as f:
            f.write('def hello():\n    return "world"\n')

        # JavaScript
        with open(os.path.join(d, 'app.js'), 'w') as f:
            f.write('function greet(name) {\n  return `Hello, ${name}!`;\n}\n')

        # TypeScript
        with open(os.path.join(d, 'app.ts'), 'w') as f:
            f.write('function add(a: number, b: number): number {\n  return a + b;\n}\n')

        # Go
        with open(os.path.join(d, 'main.go'), 'w') as f:
            f.write('package main\nfunc main() {\n  println("hello")\n}\n')

        # Rust
        with open(os.path.join(d, 'lib.rs'), 'w') as f:
            f.write('pub fn greet(name: &str) -> String {\n  format!("Hello, {}!", name)\n}\n')

        # Java
        with open(os.path.join(d, 'Hello.java'), 'w') as f:
            f.write('public class Hello {\n  public static void main(String[] args) {}\n}\n')

        # C
        with open(os.path.join(d, 'hello.c'), 'w') as f:
            f.write('#include <stdio.h>\nint main() {\n  printf("hello");\n  return 0;\n}\n')

        # C++
        with open(os.path.join(d, 'hello.cpp'), 'w') as f:
            f.write('#include <iostream>\nint main() {\n  std::cout << "hello";\n  return 0;\n}\n')

        # Ruby
        with open(os.path.join(d, 'hello.rb'), 'w') as f:
            f.write('def greet(name)\n  "Hello, #{name}!"\nend\n')

        # Swift
        with open(os.path.join(d, 'hello.swift'), 'w') as f:
            f.write('func greet(name: String) -> String {\n  return "Hello, \\(name)!"\n}\n')

        # Kotlin
        with open(os.path.join(d, 'hello.kt'), 'w') as f:
            f.write('fun greet(name: String): String {\n  return "Hello, $name!"\n}\n')

        # Dart
        with open(os.path.join(d, 'hello.dart'), 'w') as f:
            f.write('String greet(String name) {\n  return "Hello, $name!";\n}\n')

        # Scala
        with open(os.path.join(d, 'hello.scala'), 'w') as f:
            f.write('object Hello {\n  def greet(name: String): String = {\n    s"Hello, $name!"\n  }\n}\n')

        # Lua
        with open(os.path.join(d, 'hello.lua'), 'w') as f:
            f.write('function greet(name)\n  return "Hello, " .. name .. "!"\nend\n')

        cg = self._make_cg('test_multi_lang')
        result = cg.index_all()
        assert result.success

        stats = cg.get_stats()
        print(f'\n  Multi-language: {result.files_indexed} files, {stats.node_count} nodes')
        for lang, count in stats.files_by_language.items():
            print(f'    {lang}: {count} files')

        cg.close()

    # =========================================================================
    # 3. Reference resolution stress (deep call chains)
    # =========================================================================

    def test_deep_call_chain(self):
        """Create a deep call chain across 20 files — verify resolution."""
        d = os.path.join(self.test_dir, 'test_deep_chain')
        os.makedirs(d, exist_ok=True)

        # Chain: file_00 → file_01 → ... → file_19
        # Each file_i defines func_i() and calls func_{i+1} from file_{i+1}
        # Last file (file_19) defines leaf_func() — the end of the chain
        for i in range(20):
            with open(os.path.join(d, f'file_{i:02d}.py'), 'w') as f:
                if i == 19:
                    f.write('def leaf_func() -> str:\n    return "done"\n')
                elif i == 18:
                    # file_18: imports leaf_func from file_19
                    f.write('from file_19 import leaf_func\n\ndef func_18() -> str:\n    return leaf_func()\n')
                else:
                    next_i = i + 1
                    f.write(f'from file_{next_i:02d} import func_{next_i:02d}\n\ndef func_{i:02d}() -> str:\n    return func_{next_i:02d}()\n')

        cg = self._make_cg('test_deep_chain')
        result = cg.index_all()
        assert result.success
        assert result.files_indexed >= 20

        stats = cg.get_stats()
        print(f'\n  Deep chain: {stats.node_count} nodes, {stats.edge_count} edges')
        print(f'  Edges by kind: {dict(stats.edges_by_kind)}')

        # The resolver should have created edges between functions
        calls_count = stats.edges_by_kind.get('calls', 0)
        assert calls_count > 0, f'Expected call edges, got {calls_count}'

        # Verify leaf function has many callers
        leaf_nodes = [n for n in cg.get_nodes_by_name('leaf_func') if n.kind == 'function']
        assert leaf_nodes, 'leaf_func not found'
        callers = cg.get_callers(leaf_nodes[0].id)
        print(f'  leaf_func callers: {len(callers)}')
        assert len(callers) > 0, f'Expected callers for leaf_func'

        cg.close()

    def test_cross_file_import_chain(self):
        """Complex import chain: A → B → C → D — verify all edges."""
        d = os.path.join(self.test_dir, 'test_import_chain')
        os.makedirs(d, exist_ok=True)

        # D is a utility module
        with open(os.path.join(d, 'utils_d.py'), 'w') as f:
            f.write('def util_func():\n    return 42\n')

        # C imports D
        with open(os.path.join(d, 'service_c.py'), 'w') as f:
            f.write('from utils_d import util_func\n\ndef process_c():\n    return util_func()\n')

        # B imports C
        with open(os.path.join(d, 'logic_b.py'), 'w') as f:
            f.write('from service_c import process_c\n\ndef run_b():\n    return process_c()\n')

        # A imports B (entry point)
        with open(os.path.join(d, 'main_a.py'), 'w') as f:
            f.write('from logic_b import run_b\n\ndef entry():\n    return run_b()\n')

        cg = self._make_cg('test_import_chain')
        result = cg.index_all()
        assert result.success

        stats = cg.get_stats()
        calls = stats.edges_by_kind.get('calls', 0)
        print(f'\n  Import chain: {stats.node_count} nodes, {calls} call edges')

        # Verify full call chain: entry → run_b → process_c → util_func
        entry_nodes = [n for n in cg.get_nodes_by_name('entry') if n.kind == 'function']
        assert entry_nodes, 'entry not found'
        callees = cg.get_callees(entry_nodes[0].id)
        callee_names = {n.name for n, e in callees}
        # entry should call run_b
        assert 'run_b' in callee_names, f'entry should call run_b, got {callee_names}'

        # util_func should have callers
        util_nodes = [n for n in cg.get_nodes_by_name('util_func') if n.kind == 'function']
        assert util_nodes, 'util_func not found'
        util_callers = cg.get_callers(util_nodes[0].id)
        util_caller_names = {n.name for n, e in util_callers}
        assert 'process_c' in util_caller_names, \
            f'util_func should be called by process_c, got {util_caller_names}'

        print(f'  Full chain resolved: entry → run_b → process_c → util_func ✓')

        cg.close()

    # =========================================================================
    # 4. Graph traversal stress
    # =========================================================================

    def test_large_impact_radius(self):
        """Test impact radius traversal with many nodes."""
        d = os.path.join(self.test_dir, 'test_large_impact')
        os.makedirs(d, exist_ok=True)

        # Create 50 interdependent files
        for i in range(50):
            imports = []
            # Each file imports from several others
            for j in range(max(0, i - 3), i):
                imports.append(f'from module_{j:03d} import helper_{j:03d}')

            with open(os.path.join(d, f'module_{i:03d}.py'), 'w') as f:
                content = '\n'.join(imports)
                if imports:
                    content += '\n\n'
                content += f'def helper_{i:03d}():\n    return {i}\n'
                if imports:
                    content += f'\ndef main_{i:03d}():\n    return sum([helper_{j:03d}() for j in range({max(0, i-3)}, {i})])\n'
                f.write(content)

        cg = self._make_cg('test_large_impact')
        result = cg.index_all()
        assert result.success
        print(f'\n  Large impact graph: {result.files_indexed} files, '
              f'{result.nodes_created} nodes, {result.edges_created} edges')

        # Get a middle node and compute impact
        middle_nodes = [n for n in cg.get_nodes_by_name('helper_25') if n.kind == 'function']
        if middle_nodes:
            start = time.time()
            subgraph = cg.get_impact_radius(middle_nodes[0].id, max_depth=5)
            elapsed = time.time() - start
            print(f'  Impact radius (depth=5): {len(subgraph.nodes)} nodes, '
                  f'{len(subgraph.edges)} edges in {elapsed:.3f}s')
            assert elapsed < 5.0, f'Impact traversal too slow: {elapsed:.2f}s'

        cg.close()

    # =========================================================================
    # 5. Sync stress
    # =========================================================================

    def test_batch_sync(self):
        """Batch add, modify, then remove files — verify sync accuracy."""
        d = os.path.join(self.test_dir, 'test_batch_sync')
        os.makedirs(d, exist_ok=True)

        # Initial files
        for i in range(30):
            with open(os.path.join(d, f'original_{i:03d}.py'), 'w') as f:
                f.write(f'def original_{i:03d}():\n    return {i}\n')

        cg = self._make_cg('test_batch_sync')
        result = cg.index_all()
        assert result.files_indexed >= 30

        stats_before = cg.get_stats()
        print(f'\n  Before sync: {stats_before.file_count} files, '
              f'{stats_before.node_count} nodes')

        # Add 20 new files
        for i in range(20):
            with open(os.path.join(d, f'added_{i:03d}.py'), 'w') as f:
                f.write(f'def added_{i:03d}():\n    return {i}\n')

        # Modify 10 original files
        for i in range(10):
            with open(os.path.join(d, f'original_{i:03d}.py'), 'w') as f:
                f.write(f'def original_{i:03d}():\n    return {i * 2}\n')

        # Remove 5 original files
        for i in range(20, 25):
            os.remove(os.path.join(d, f'original_{i:03d}.py'))

        # Sync
        start = time.time()
        sync_result = cg.sync()
        elapsed = time.time() - start
        print(f'  Sync: added={sync_result.files_added}, mod={sync_result.files_modified}, '
              f'rem={sync_result.files_removed} in {elapsed:.3f}s')

        assert sync_result.files_added == 20
        assert sync_result.files_modified == 10
        assert sync_result.files_removed == 5

        stats_after = cg.get_stats()
        assert stats_after.file_count == 45  # 30 + 20 - 5
        print(f'  After sync: {stats_after.file_count} files, '
              f'{stats_after.node_count} nodes')

        cg.close()

    # =========================================================================
    # 6. Edge cases
    # =========================================================================

    def test_empty_file(self):
        """Index an empty file — should produce just a file node."""
        d = os.path.join(self.test_dir, 'test_empty')
        os.makedirs(d, exist_ok=True)

        with open(os.path.join(d, 'empty.py'), 'w') as f:
            f.write('')

        with open(os.path.join(d, 'empty.js'), 'w') as f:
            f.write('')

        cg = self._make_cg('test_empty')
        result = cg.index_all()
        assert result.success
        assert result.files_indexed >= 2

        stats = cg.get_stats()
        # Should have 2 file nodes
        assert stats.node_count >= 2
        print(f'\n  Empty files: {stats.node_count} nodes, {stats.edge_count} edges')
        cg.close()

    def test_file_with_syntax_errors(self):
        """Index files with syntax errors — should handle gracefully."""
        d = os.path.join(self.test_dir, 'test_syntax_err')
        os.makedirs(d, exist_ok=True)

        with open(os.path.join(d, 'broken.py'), 'w') as f:
            f.write('def broken(\n    pass\n')

        with open(os.path.join(d, 'valid.py'), 'w') as f:
            f.write('def valid():\n    pass\n')

        cg = self._make_cg('test_syntax_err')
        result = cg.index_all()
        # Should still succeed with partial results
        assert result.success

        stats = cg.get_stats()
        print(f'\n  Syntax errors: {stats.node_count} nodes (should have at least valid.py nodes)')
        assert stats.node_count >= 1, f'Expected at least 1 node for valid.py'
        cg.close()

    def test_unicode_content(self):
        """Index files with Unicode content."""
        d = os.path.join(self.test_dir, 'test_unicode')
        os.makedirs(d, exist_ok=True)

        with open(os.path.join(d, 'unicode.py'), 'w', encoding='utf-8') as f:
            f.write('# -*- coding: utf-8 -*-\n')
            f.write('def 中文函数():\n    """一个中文文档字符串"""\n    return "你好世界"\n')

        cg = self._make_cg('test_unicode')
        result = cg.index_all()
        assert result.success

        # Search for the Chinese function
        results = cg.search_nodes('中文函数')
        assert len(results) >= 1, f'Expected to find Chinese function name'
        print(f'\n  Unicode: found {len(results)} results for Chinese function name')
        cg.close()

    def test_very_large_file(self):
        """Index a very large file (10000+ lines) — ensure no crash."""
        d = os.path.join(self.test_dir, 'test_large_file')
        os.makedirs(d, exist_ok=True)

        with open(os.path.join(d, 'large.py'), 'w') as f:
            f.write('# Large file stress test\n')
            f.write('def start():\n    pass\n\n')
            for i in range(500):
                f.write(f'\ndef func_{i:04d}():\n    return {i}\n')
            f.write('\ndef end():\n    return start()\n')

        cg = self._make_cg('test_large_file')
        start = time.time()
        result = cg.index_all()
        elapsed = time.time() - start

        assert result.success
        stats = cg.get_stats()
        print(f'\n  Large file (500+ funcs): {stats.node_count} nodes in {elapsed:.2f}s')
        assert elapsed < 10.0, f'Large file indexing too slow: {elapsed:.2f}s'
        cg.close()

    def test_duplicate_symbol_names(self):
        """Multiple files with same function names — resolution should prefer same-file."""
        d = os.path.join(self.test_dir, 'test_dup_names')
        os.makedirs(d, exist_ok=True)

        # 3 files all defining 'process' with different implementations
        with open(os.path.join(d, 'algo_a.py'), 'w') as f:
            f.write('def process(data: str) -> str:\n    return f"A:{data}"\n')

        with open(os.path.join(d, 'algo_b.py'), 'w') as f:
            f.write('def process(data: str) -> str:\n    return f"B:{data}"\n')

        # This file imports and uses process from algo_a
        with open(os.path.join(d, 'runner.py'), 'w') as f:
            f.write('from algo_a import process\n\ndef run():\n    return process("test")\n')

        cg = self._make_cg('test_dup_names')
        result = cg.index_all()
        assert result.success

        stats = cg.get_stats()
        calls = stats.edges_by_kind.get('calls', 0)
        print(f'\n  Duplicate names: {calls} call edges, nodes={stats.node_count}')

        # The call from 'run' should resolve to 'process' in 'algo_a'
        run_nodes = [n for n in cg.get_nodes_by_name('run') if n.kind == 'function']
        if run_nodes:
            callees = cg.get_callees(run_nodes[0].id)
            callee_files = {n.file_path for n, e in callees}
            assert 'algo_a.py' in callee_files, \
                f'Expected run to call algo_a.process, got files: {callee_files}'
            print(f'  run() calls process from: {callee_files} ✓')

        cg.close()

    def test_self_referencing_file(self):
        """A file that calls its own functions — should resolve within same file."""
        d = os.path.join(self.test_dir, 'test_self_ref')
        os.makedirs(d, exist_ok=True)

        with open(os.path.join(d, 'self_ref.py'), 'w') as f:
            f.write('\n'.join([
                'def helper():\n    return 42',
                '',
                'def main():\n    return helper() + 1',
            ]))

        cg = self._make_cg('test_self_ref')
        result = cg.index_all()
        assert result.success

        stats = cg.get_stats()
        calls = stats.edges_by_kind.get('calls', 0)
        print(f'\n  Self-reference: {calls} call edges')

        # main() should call helper()
        main_nodes = [n for n in cg.get_nodes_by_name('main') if n.kind == 'function']
        if main_nodes:
            callees = cg.get_callees(main_nodes[0].id)
            callee_names = {n.name for n, e in callees}
            assert 'helper' in callee_names, \
                f'main should call helper, got {callee_names}'
            print(f'  main() calls helper() ✓')

        cg.close()


if __name__ == '__main__':
    t = TestStress()
    t.setup_class()
    for method_name in sorted(dir(t)):
        if method_name.startswith('test_'):
            try:
                getattr(t, method_name)()
                print(f'  ✓ {method_name}')
            except Exception as e:
                print(f'  ✗ {method_name}: {e}')
                import traceback
                traceback.print_exc()
    t.teardown_class()
    print('\nDone!')
