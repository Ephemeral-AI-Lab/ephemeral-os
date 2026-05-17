"""Orchestrator-side host package for sandbox operations.

- :mod:`sandbox.host.runtime_bundle` — build and upload the daemon bundle.
- :mod:`sandbox.host.daemon_client` — client for the bundled in-sandbox daemon.
- :mod:`sandbox.host.bootstrap` — sandbox lifecycle bootstrap and recovery.

Layer rule: host modules may import provider registry surfaces and foundation
modules, but not the public ``sandbox.api`` facade.
"""

from __future__ import annotations

__all__: list[str] = []
