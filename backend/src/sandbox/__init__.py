"""Sandbox package — public API, host, daemon, providers, and testing.

Sub-packages:
- ``sandbox.api``      — public verbs (lifecycle, read/write/edit/shell, raw_exec)
- ``sandbox.host``     — orchestrator-side deploy/rpc/ops package
- ``sandbox.providers`` — provider adapter registry and provider implementations
- ``sandbox.daemon``   — in-sandbox dispatcher and services
- ``sandbox.testing``  — sandbox factories and eval-file fixtures

Import directly from sub-packages — this top-level ``__init__`` intentionally
re-exports nothing.
"""
