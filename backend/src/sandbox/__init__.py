"""Sandbox package — public API, lifecycle, runtime, providers, and testing.

Sub-packages:
- ``sandbox.lifecycle``        — :class:`SandboxService`, :class:`SandboxProxy`,
                                  context preparation, workspace discovery, and
                                  shell/commit submission helpers
- ``sandbox.providers``        — provider adapter registry and provider implementations
- ``sandbox.runtime``          — in-sandbox runtime bundle and service adapters
- ``sandbox.testing``          — sandbox factories and eval-file fixtures
- ``sandbox.errors``           — :class:`DaytonaUnavailableError`,
                                  :class:`AsyncDaytonaUnavailableError`

Import directly from sub-packages — this top-level ``__init__`` intentionally
re-exports nothing.
"""
