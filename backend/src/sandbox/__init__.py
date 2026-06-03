"""Sandbox package: public API, host bootstrap, provider adapters, and audit.

Sub-packages:
- ``sandbox.api``      — host public verbs and Rust daemon transport contracts
- ``sandbox.host``     — orchestrator-side setup, daemon client, and bundle upload
- ``sandbox.provider`` — provider adapter registry and provider implementations
- ``sandbox.audit``    — host-readable audit schemas and translation helpers

The public API surface is documented in ``docs/architecture/sandbox``.
"""

from __future__ import annotations
