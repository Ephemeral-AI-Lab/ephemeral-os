"""Sandbox package — public API, host, runtime daemon, provider, and testing.

Sub-packages:
- ``sandbox.api``      — public verbs (lifecycle, read/write/edit/shell, raw_exec)
- ``sandbox.host``     — orchestrator-side setup, daemon client, and recovery
- ``sandbox.provider`` — provider adapter registry and provider implementations
- ``sandbox.runtime.daemon`` — in-sandbox dispatcher and services
- ``sandbox.testing``  — sandbox factories and eval-file fixtures

Import directly from sub-packages — this top-level ``__init__`` intentionally
re-exports nothing.
"""
