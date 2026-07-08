"""
CodeGraph Error Types

Custom exceptions and logging utilities.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional


class CodeGraphError(Exception):
    """Base error for all CodeGraph errors."""
    def __init__(self, message: str, cause: Optional[Exception] = None):
        self.message = message
        self.cause = cause
        super().__init__(message)


class FileError(CodeGraphError):
    """Error related to file operations."""
    pass


class ParseError(CodeGraphError):
    """Error during code parsing."""
    def __init__(self, message: str, file_path: Optional[str] = None,
                 line: Optional[int] = None, column: Optional[int] = None):
        self.file_path = file_path
        self.line = line
        self.column = column
        super().__init__(message)


class DatabaseError(CodeGraphError):
    """Error related to database operations."""
    pass


class SearchError(CodeGraphError):
    """Error during search operations."""
    pass


class ConfigError(CodeGraphError):
    """Error related to configuration."""
    pass


# =============================================================================
# Logger
# =============================================================================

_logger: Optional[logging.Logger] = None


def get_logger() -> logging.Logger:
    """Get the global CodeGraph logger."""
    global _logger
    if _logger is None:
        _logger = logging.getLogger('codegraph')
        if not _logger.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter(
                '[%(levelname)s] %(message)s'
            ))
            _logger.addHandler(handler)
            _logger.setLevel(logging.WARNING)
    return _logger


def set_logger(logger: logging.Logger) -> None:
    """Set a custom logger."""
    global _logger
    _logger = logger


def log_debug(message: str, **kwargs) -> None:
    """Log a debug message."""
    extra = f' {" ".join(f"{k}={v}" for k, v in kwargs.items())}' if kwargs else ''
    get_logger().debug(f'{message}{extra}')


def log_info(message: str, **kwargs) -> None:
    """Log an info message."""
    extra = f' {" ".join(f"{k}={v}" for k, v in kwargs.items())}' if kwargs else ''
    get_logger().info(f'{message}{extra}')


def log_warn(message: str, **kwargs) -> None:
    """Log a warning message."""
    extra = f' {" ".join(f"{k}={v}" for k, v in kwargs.items())}' if kwargs else ''
    get_logger().warning(f'{message}{extra}')


def log_error(message: str, **kwargs) -> None:
    """Log an error message."""
    extra = f' {" ".join(f"{k}={v}" for k, v in kwargs.items())}' if kwargs else ''
    get_logger().error(f'{message}{extra}')
