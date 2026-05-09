"""Conftest for the LSP live e2e suite.

Loads ``benchmarks.lsp_live_test.fixtures`` so ``lsp_sandbox`` and friends
are available without each test importing them explicitly.
"""

from __future__ import annotations

pytest_plugins = ["benchmarks.lsp_live_test.fixtures"]
