"""Tests for code-intelligence backend routing."""

from __future__ import annotations

import pytest

from code_intelligence.routing.backend_protocol import (
    BackendQueryOutcome,
    QueryStatus,
)
from code_intelligence.routing.query_router import IntelligenceQueryRouter


class _DiagnosticsBackend:
    def __init__(
        self,
        *,
        name: str,
        priority: int,
        outcome: BackendQueryOutcome,
    ) -> None:
        self.name = name
        self.priority = priority
        self._outcome = outcome

    def supports(self, file_path: str) -> bool:
        return file_path.endswith(".py")

    def find_definitions(self, file_path: str, symbol: str, line: int, character: int):
        return BackendQueryOutcome(status=QueryStatus.UNSUPPORTED)

    def find_references(self, file_path: str, symbol: str, line: int, character: int):
        return BackendQueryOutcome(status=QueryStatus.UNSUPPORTED)

    def hover(self, file_path: str, line: int, character: int):
        return BackendQueryOutcome(status=QueryStatus.UNSUPPORTED)

    def diagnostics(self, file_path: str) -> BackendQueryOutcome:
        return self._outcome


def test_diagnostics_raise_when_only_diagnostic_backend_fails() -> None:
    router = IntelligenceQueryRouter()
    router.register(
        _DiagnosticsBackend(
            name="lsp",
            priority=100,
            outcome=BackendQueryOutcome(
                status=QueryStatus.ERROR,
                error="transport unavailable",
            ),
        )
    )
    router.register(
        _DiagnosticsBackend(
            name="symbol_index",
            priority=50,
            outcome=BackendQueryOutcome(status=QueryStatus.UNSUPPORTED),
        )
    )

    with pytest.raises(RuntimeError, match="transport unavailable"):
        router.diagnostics("pkg/mod.py")


def test_empty_diagnostics_are_successful_clean_result() -> None:
    router = IntelligenceQueryRouter()
    router.register(
        _DiagnosticsBackend(
            name="lsp",
            priority=100,
            outcome=BackendQueryOutcome(status=QueryStatus.SUCCESS, results=[]),
        )
    )

    assert router.diagnostics("pkg/mod.py") == []
