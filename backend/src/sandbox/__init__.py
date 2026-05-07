"""Sandbox package — public API, control plane, runtime, providers, and testing.

Sub-packages:
- ``sandbox.api``      — public verbs (lifecycle, read/write/edit/shell, raw_exec)
- ``sandbox.host``  — orchestrator-side control plane (``daemon`` + ``ops``)
- ``sandbox.providers`` — provider adapter registry and provider implementations
- ``sandbox.daemon``  — in-sandbox dispatcher (server.py, overlay_shell)
- ``sandbox.testing``  — sandbox factories and eval-file fixtures

Import directly from sub-packages — this top-level ``__init__`` intentionally
re-exports nothing.
"""
