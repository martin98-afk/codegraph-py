"""
CodeGraph Directory Management

Manages the .codegraph/ directory structure.
"""

from __future__ import annotations

import os
import json
import shutil
from pathlib import Path
from typing import Optional, List, Tuple

import codegraph.errors as err

CODEGRAPH_DIR = '.codegraph'


def get_codegraph_dir(project_root: str) -> str:
    """Get the path to the .codegraph directory."""
    return os.path.join(project_root, CODEGRAPH_DIR)


def get_database_path(project_root: str) -> str:
    """Get the path to the SQLite database."""
    return os.path.join(get_codegraph_dir(project_root), 'codegraph.db')


def is_initialized(project_root: str) -> bool:
    """Check if a directory has been initialized as a CodeGraph project."""
    cg_dir = get_codegraph_dir(project_root)
    db_path = os.path.join(cg_dir, 'codegraph.db')
    if not (os.path.isdir(cg_dir) and os.path.isfile(db_path)):
        return False
    # Verify the database has the expected schema tables
    try:
        import sqlite3
        db = sqlite3.connect(db_path)
        cur = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='files'"
        )
        has_files_table = cur.fetchone() is not None
        db.close()
        return has_files_table
    except Exception:
        return False


def create_directory(project_root: str) -> None:
    """Create the .codegraph directory structure."""
    cg_dir = get_codegraph_dir(project_root)
    os.makedirs(cg_dir, exist_ok=True)


def remove_directory(project_root: str) -> None:
    """Remove the .codegraph directory structure."""
    cg_dir = get_codegraph_dir(project_root)
    if os.path.isdir(cg_dir):
        shutil.rmtree(cg_dir)


def validate_directory(project_root: str) -> Tuple[bool, List[str]]:
    """Validate the .codegraph directory structure."""
    errors_list = []
    cg_dir = get_codegraph_dir(project_root)

    if not os.path.isdir(cg_dir):
        errors_list.append(f'{CODEGRAPH_DIR}/ directory not found')

    db_path = os.path.join(cg_dir, 'codegraph.db')
    if not os.path.isfile(db_path):
        errors_list.append(f'{CODEGRAPH_DIR}/codegraph.db not found')

    return (len(errors_list) == 0, errors_list)


def find_nearest_codegraph_root(path: str) -> Optional[str]:
    """Walk up from path to find the nearest .codegraph directory."""
    path = os.path.normpath(os.path.abspath(path))
    root = Path(path).anchor

    while path and path != root:
        if is_initialized(path):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent

    return None


def is_codegraph_data_dir(name: str) -> bool:
    """Check if a directory name is the CodeGraph data directory."""
    return name == CODEGRAPH_DIR


def unsafe_index_root_reason(path: str) -> Optional[str]:
    """Check if a path is unsafe to index (home dir, root, etc)."""
    home = os.path.expanduser('~')
    path = os.path.normpath(os.path.abspath(path))

    if path == home:
        return 'your home directory'
    if path == Path(path).anchor:
        return 'a filesystem root'

    # Check common dangerous paths
    dangerous = [
        '/etc', '/var', '/sys', '/proc', '/dev', '/bin', '/sbin',
        '/usr/bin', '/usr/lib', '/usr/sbin',
    ]
    if os.name == 'posix':
        for d in dangerous:
            if path == d:
                return 'a system directory'

    return None


def derive_project_name_tokens(project_root: str) -> List[str]:
    """Derive project name tokens from project configuration files."""
    tokens = []

    # Try pyproject.toml
    pyproject = os.path.join(project_root, 'pyproject.toml')
    if os.path.isfile(pyproject):
        try:
            # Minimal TOML parsing for project name
            with open(pyproject, 'r') as f:
                for line in f:
                    if line.strip().startswith('name'):
                        import re
                        m = re.search(r'name\s*=\s*["\'](.+?)["\']', line)
                        if m:
                            tokens.extend(m.group(1).replace('-', ' ').replace('_', ' ').split())
                        break
        except Exception:
            pass

    # Try package.json
    pkg_json = os.path.join(project_root, 'package.json')
    if os.path.isfile(pkg_json):
        try:
            with open(pkg_json, 'r') as f:
                data = json.load(f)
                if 'name' in data:
                    tokens.extend(data['name'].replace('-', ' ').replace('_', ' ').split())
        except Exception:
            pass

    # Try setup.cfg
    setup_cfg = os.path.join(project_root, 'setup.cfg')
    if os.path.isfile(setup_cfg):
        try:
            with open(setup_cfg, 'r') as f:
                for line in f:
                    if line.strip().startswith('name'):
                        import re
                        m = re.search(r'name\s*=\s*(.+?)$', line)
                        if m:
                            tokens.extend(m.group(1).strip().replace('-', ' ').replace('_', ' ').split())
                        break
        except Exception:
            pass

    # Fallback: use directory name
    if not tokens:
        dir_name = os.path.basename(project_root)
        tokens = dir_name.replace('-', ' ').replace('_', ' ').split()

    return [t.lower() for t in tokens if len(t) > 1]
