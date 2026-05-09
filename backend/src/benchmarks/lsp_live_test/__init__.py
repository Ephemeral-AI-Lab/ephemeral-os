"""Live e2e test suite for the LSP plugin tool calls.

The suite provisions a real Daytona sandbox (reusing the SWE-EVO fixture
infrastructure), writes scenario-specific Python files via the public
``sandbox.api.write_file`` / ``edit_file`` verbs, then exercises each
``lsp.*`` tool via ``tools.factory.create_tool`` and asserts the response
shape.

The "scenario-based testing" pattern is intentionally lightweight per
``docs/architecture/plugins-refactor.md`` review feedback — a frozen
dataclass + an async runner, no Hook/AuditEventBus machinery.
"""

from __future__ import annotations

__all__: list[str] = []
