"""Tests for CodeGraph Python package."""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from codegraph import CodeGraph
from codegraph.types import NodeKind, EdgeKind


class TestCodeGraph:
    """Test the CodeGraph Python package."""

    @classmethod
    def setup_class(cls):
        """Set up test fixtures - create files once."""
        cls.test_dir = tempfile.mkdtemp(prefix='codegraph_test_')
        cls._create_test_files()

    @classmethod
    def teardown_class(cls):
        """Clean up test fixtures."""
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    @classmethod
    def _create_test_files(cls):
        """Create test Python files."""
        files = {
            'hello.py': 'def hello():\n    return "world"\n\nclass Greeter:\n    def greet(self, name: str) -> str:\n        return f"Hello, {name}!"\n',
            'math_utils.py': 'def add(a: int, b: int) -> int:\n    return a + b\n\ndef subtract(a: int, b: int) -> int:\n    return a - b\n\nclass Calculator:\n    def multiply(self, a: int, b: int) -> int:\n        return a * b\n',
            'complex.py': 'import os\nimport sys\nfrom typing import Optional\n\ndef helper():\n    pass\n\nasync def fetch_data(url: str) -> dict:\n    return {"data": None}\n\nclass DataProcessor:\n    @staticmethod\n    def validate(item: str) -> bool:\n        return True\n\n    async def process(self, items: list) -> list:\n        return []\n',
        }
        for filename, content in files.items():
            with open(os.path.join(cls.test_dir, filename), 'w') as f:
                f.write(content.strip())

    def _make_cg(self, subdir=None):
        """Create a fresh CodeGraph instance in a test subdirectory."""
        d = self.test_dir if subdir is None else os.path.join(self.test_dir, subdir)
        os.makedirs(d, exist_ok=True)
        # Copy files into subdirectory
        for f in ['hello.py', 'math_utils.py', 'complex.py']:
            src = os.path.join(self.test_dir, f)
            dst = os.path.join(d, f)
            if os.path.isfile(src) and not os.path.isfile(dst):
                shutil.copy2(src, dst)
        cg = CodeGraph.init_sync(d)
        result = cg.index_all()
        assert result.success, f'Index failed: {result.errors}'
        return cg

    def _clean_cg(self, cg):
        """Close a CodeGraph instance."""
        try:
            cg.close()
        except Exception:
            pass

    def test_init_and_index(self):
        """Test initialization and indexing."""
        d = os.path.join(self.test_dir, 'test_init')
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'test.py'), 'w') as f:
            f.write('def foo():\n    pass\n')

        cg = CodeGraph.init_sync(d)
        assert CodeGraph.is_initialized(d)

        result = cg.index_all()
        assert result.success
        assert result.files_indexed >= 1
        assert result.nodes_created >= 1
        self._clean_cg(cg)

    def test_search(self):
        """Test search functionality."""
        cg = self._make_cg('test_search')

        # Search function
        results = cg.search_nodes('hello')
        names = [r.node.name for r in results]
        assert 'hello' in names, f'Expected "hello" in {names}'

        # Search class
        results = cg.search_nodes('Greeter')
        names = [r.node.name for r in results]
        assert 'Greeter' in names, f'Expected "Greeter" in {names}'

        # Search method
        results = cg.search_nodes('add')
        names = [r.node.name for r in results]
        assert 'add' in names, f'Expected "add" in {names}'

        self._clean_cg(cg)

    def test_nodes_by_name(self):
        """Test get_nodes_by_name."""
        cg = self._make_cg('test_nodes')

        node_list = cg.get_nodes_by_name('hello')
        assert len(node_list) >= 1
        assert node_list[0].name == 'hello'
        assert node_list[0].kind == 'function'

        node_list = cg.get_nodes_by_name('Calculator')
        assert len(node_list) >= 1
        assert node_list[0].kind == 'class'

        self._clean_cg(cg)

    def test_nodes_by_file(self):
        """Test get_nodes_by_file."""
        cg = self._make_cg('test_file')

        nodes = cg.get_nodes_by_file('hello.py')
        assert len(nodes) >= 1
        assert all(n.file_path == 'hello.py' for n in nodes)

        self._clean_cg(cg)

    def test_callers_and_callees(self):
        """Test callers and callees."""
        cg = self._make_cg('test_chain')

        nodes = cg.get_nodes_by_name('hello')
        assert len(nodes) >= 1

        callers = cg.get_callers(nodes[0].id)
        assert isinstance(callers, list)

        callees = cg.get_callees(nodes[0].id)
        assert isinstance(callees, list)

        self._clean_cg(cg)

    def test_impact(self):
        """Test impact analysis."""
        cg = self._make_cg('test_impact')

        nodes = cg.get_nodes_by_name('Greeter')
        assert len(nodes) >= 1

        subgraph = cg.get_impact_radius(nodes[0].id)
        assert subgraph is not None
        assert nodes[0].id in subgraph.nodes

        self._clean_cg(cg)

    def test_stats(self):
        """Test statistics."""
        cg = self._make_cg('test_stats')

        stats = cg.get_stats()
        assert stats.file_count >= 1
        assert stats.node_count >= 3
        assert stats.edge_count >= 2
        assert stats.db_size_bytes >= 0  # May be 0 on some filesystems

        self._clean_cg(cg)

    def test_types(self):
        """Test types module."""
        from codegraph.types import Node, Edge

        assert NodeKind.FUNCTION.value == 'function'
        assert EdgeKind.CALLS.value == 'calls'

        node = Node(
            id='test:id', kind='function', name='test_func',
            qualified_name='test.py::test_func', file_path='test.py',
            language='python', start_line=1, end_line=10,
            start_column=0, end_column=0,
        )
        assert node.name == 'test_func'

        edge = Edge(source='src', target='tgt', kind='calls')
        assert edge.source == 'src'
        assert edge.kind == 'calls'

    def test_sync(self):
        """Test sync operation."""
        d = os.path.join(self.test_dir, 'test_sync')
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'original.py'), 'w') as f:
            f.write('def existing():\n    pass\n')

        cg = CodeGraph.init_sync(d)
        cg.index_all()
        stats_before = cg.get_stats()

        # Add a new file
        with open(os.path.join(d, 'added.py'), 'w') as f:
            f.write('def new_func():\n    return 42\n')

        result = cg.sync()
        assert result.files_added >= 1

        stats_after = cg.get_stats()
        assert stats_after.node_count > stats_before.node_count

        self._clean_cg(cg)

    def test_complex_parsing(self):
        """Test parsing complex Python code."""
        cg = self._make_cg('test_complex')

        # Async function
        results = cg.search_nodes('fetch_data')
        assert len(results) >= 1

        # Static method
        results = cg.search_nodes('validate')
        assert len(results) >= 1

        # Class
        results = cg.search_nodes('DataProcessor')
        assert len(results) >= 1

        self._clean_cg(cg)

    def test_file_paths(self):
        """Test file path handling."""
        cg = self._make_cg('test_paths')

        # All file paths should be forward-slash relative
        nodes = cg.get_nodes_by_name('hello')
        for n in nodes:
            assert '\\' not in n.file_path, f'Backslash in path: {n.file_path}'
            assert not n.file_path.startswith('/'), f'Absolute path: {n.file_path}'
            assert not n.file_path.startswith('C:'), f'Absolute Windows path: {n.file_path}'

        self._clean_cg(cg)


if __name__ == '__main__':
    t = TestCodeGraph()
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
