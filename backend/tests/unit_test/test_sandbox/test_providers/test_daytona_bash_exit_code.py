"""Regression tests for extract_exit_code (provider CR-01).

A Daytona response with no `__CODEX_EXIT_CODE__=` marker and a non-numeric
SDK ``exit_code`` was previously coerced to ``0`` (success). The fix returns
sentinel ``255`` so a failed remote command surfaces as failure to
``adapter.exec`` (which derives ``success = (exit_code == 0)``).
"""

from __future__ import annotations

from sandbox.provider.daytona import exec_wrapper as bash_mod
from sandbox.provider.daytona.exec_wrapper import EXIT_MARKER, extract_exit_code


def _reset_warn_flag() -> None:
    bash_mod._UNPARSEABLE_EXIT_WARNED = False


class TestExtractExitCodeMarkerPresent:
    def test_marker_wins_over_fallback(self):
        _reset_warn_flag()
        stdout, code = extract_exit_code(
            f"hello\n{EXIT_MARKER}3\n",
            fallback_exit_code="ignored",
        )
        assert code == 3
        assert stdout == "hello"

    def test_negative_marker_value(self):
        _reset_warn_flag()
        stdout, code = extract_exit_code(
            f"x\n{EXIT_MARKER}-1\n",
            fallback_exit_code=None,
        )
        assert code == -1
        assert stdout == "x"


class TestExtractExitCodeNumericFallback:
    def test_int_fallback_used_when_no_marker(self):
        _reset_warn_flag()
        stdout, code = extract_exit_code("partial output", fallback_exit_code=7)
        assert code == 7
        assert stdout == "partial output"

    def test_numeric_string_fallback_used_when_no_marker(self):
        _reset_warn_flag()
        stdout, code = extract_exit_code("partial output", fallback_exit_code="42")
        assert code == 42

    def test_negative_numeric_string_fallback(self):
        _reset_warn_flag()
        stdout, code = extract_exit_code("x", fallback_exit_code="-2")
        assert code == -2


class TestExtractExitCodeFailsClosed:
    def test_non_numeric_fallback_returns_sentinel_255(self):
        """CR-01: non-numeric SDK exit_code must NOT be coerced to 0."""
        _reset_warn_flag()
        stdout, code = extract_exit_code(
            "partial output",
            fallback_exit_code="failed",
        )
        assert code == 255, (
            f"expected sentinel 255 for non-numeric fallback, got {code!r} "
            "(this would silently classify a failed command as success)"
        )
        assert stdout == "partial output"

    def test_empty_string_fallback_returns_sentinel_255(self):
        _reset_warn_flag()
        _, code = extract_exit_code("out", fallback_exit_code="")
        assert code == 255

    def test_unknown_fallback_returns_sentinel_255(self):
        _reset_warn_flag()
        _, code = extract_exit_code("out", fallback_exit_code="unknown")
        assert code == 255

    def test_none_fallback_and_no_marker_returns_sentinel_255(self):
        """No marker AND no SDK signal — treat as failure, not silent 0."""
        _reset_warn_flag()
        _, code = extract_exit_code("partial output", fallback_exit_code=None)
        assert code == 255

    def test_warning_emitted_once_per_session(self, caplog):
        """Warning is at WARN level and only fires once (no spam)."""
        import logging

        _reset_warn_flag()
        caplog.set_level(logging.WARNING, logger="sandbox.provider.daytona.exec_wrapper")

        extract_exit_code("a", fallback_exit_code="boom")
        extract_exit_code("b", fallback_exit_code="boom")
        extract_exit_code("c", fallback_exit_code=None)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
