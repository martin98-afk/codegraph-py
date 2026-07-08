"""
CodeGraph Project Configuration

Manages codegraph.json configuration file reading and writing.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from codegraph import errors

CONFIG_FILE = 'codegraph.json'


# =============================================================================
# Config Types
# =============================================================================

class ProjectConfig:
    """Project configuration loaded from codegraph.json."""

    def __init__(
        self,
        project_root: str,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        include_ignored_patterns: Optional[List[str]] = None,
        extension_overrides: Optional[Dict[str, str]] = None,
    ):
        self.project_root = project_root
        self.include_patterns = include_patterns or []
        self.exclude_patterns = exclude_patterns or []
        self.include_ignored_patterns = include_ignored_patterns or []
        self.extension_overrides = extension_overrides or {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return {
            'include_patterns': self.include_patterns,
            'exclude_patterns': self.exclude_patterns,
            'include_ignored_patterns': self.include_ignored_patterns,
            'extension_overrides': self.extension_overrides,
        }

    @classmethod
    def from_dict(cls, project_root: str, data: Dict[str, Any]) -> ProjectConfig:
        """Create config from dictionary."""
        return cls(
            project_root=project_root,
            include_patterns=data.get('include_patterns'),
            exclude_patterns=data.get('exclude_patterns'),
            include_ignored_patterns=data.get('include_ignored_patterns'),
            extension_overrides=data.get('extension_overrides'),
        )


# =============================================================================
# Config File Path
# =============================================================================

def get_config_path(project_root: str) -> str:
    """Get the path to the codegraph.json config file."""
    return os.path.join(project_root, CONFIG_FILE)


def config_exists(project_root: str) -> bool:
    """Check if codegraph.json exists in the project root."""
    return os.path.isfile(get_config_path(project_root))


# =============================================================================
# Load / Save
# =============================================================================

def load_config(project_root: str) -> ProjectConfig:
    """Load the codegraph.json configuration file.

    Args:
        project_root: Path to the project root directory.

    Returns:
        ProjectConfig object with the loaded configuration.

    Raises:
        ConfigError: If the config file cannot be read or parsed.
    """
    config_path = get_config_path(project_root)

    if not os.path.isfile(config_path):
        return ProjectConfig(project_root=project_root)

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise errors.ConfigError(
            f"Failed to parse {CONFIG_FILE}: {e}"
        ) from e
    except OSError as e:
        raise errors.ConfigError(
            f"Failed to read {CONFIG_FILE}: {e}"
        ) from e

    return ProjectConfig.from_dict(project_root, data)


def save_config(project_root: str, config: ProjectConfig) -> None:
    """Save the configuration to codegraph.json.

    Args:
        project_root: Path to the project root directory.
        config: ProjectConfig object to save.

    Raises:
        ConfigError: If the config file cannot be written.
    """
    config_path = get_config_path(project_root)

    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config.to_dict(), f, indent=2)
            f.write('\n')
    except OSError as e:
        raise errors.ConfigError(
            f"Failed to write {CONFIG_FILE}: {e}"
        ) from e


# =============================================================================
# Pattern Loaders
# =============================================================================

def load_exclude_patterns(project_root: str) -> List[str]:
    """Load exclude patterns from codegraph.json.

    Args:
        project_root: Path to the project root directory.

    Returns:
        List of exclude patterns (glob patterns).
    """
    config = load_config(project_root)
    return config.exclude_patterns


def load_include_patterns(project_root: str) -> List[str]:
    """Load include patterns from codegraph.json.

    Args:
        project_root: Path to the project root directory.

    Returns:
        List of include patterns (glob patterns).
    """
    config = load_config(project_root)
    return config.include_patterns


def load_include_ignored_patterns(project_root: str) -> List[str]:
    """Load include_ignored_patterns from codegraph.json.

    These patterns specify files to include even if they would
    normally be ignored (e.g., node_modules, .git files).

    Args:
        project_root: Path to the project root directory.

    Returns:
        List of include_ignored patterns (glob patterns).
    """
    config = load_config(project_root)
    return config.include_ignored_patterns


def load_extension_overrides(project_root: str) -> Dict[str, str]:
    """Load extension overrides from codegraph.json.

    Extension overrides allow mapping file extensions to specific
    languages (e.g., {".inc": "php"}).

    Args:
        project_root: Path to the project root directory.

    Returns:
        Dictionary mapping file extensions to language names.
    """
    config = load_config(project_root)
    return config.extension_overrides
