"""Configuration constants for the code intelligence service."""

from __future__ import annotations

# Symbol index
SYMBOL_INDEX_MAX_FILES = 10_000
SYMBOL_INDEX_BATCH_SIZE = 50
# File scanning
SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".tox",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "egg-info",
    }
)

SUPPORTED_EXTENSIONS = frozenset(
    {
        ".py",
    }
)
