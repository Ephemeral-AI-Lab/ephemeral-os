"""Orchestrator-side host package for sandbox operations.

- :mod:`sandbox.host.runtime_bundle` — build and upload the daemon bundle.
- :mod:`sandbox.host.daemon_client` — client for the bundled in-sandbox daemon.
- :mod:`sandbox.host.bootstrap` — sandbox lifecycle bootstrap and recovery.
- :mod:`sandbox.host.isolated_workspace_lifecycle` — enter/exit isolated workspaces.

Layer rule: host modules may import provider registry surfaces and foundation
modules, but not the public ``sandbox.api`` facade.
"""

from __future__ import annotations

from sandbox.host.bootstrap import ensure_git, setup_after_create
from sandbox.host.daemon_client import DEFAULT_LAYER_STACK_ROOT, call_daemon_api
from sandbox.host.isolated_workspace_lifecycle import (
    enter_isolated_workspace,
    exit_isolated_workspace,
)
from sandbox.host.runtime_bundle import bundle_hash

__all__ = [
    "DEFAULT_LAYER_STACK_ROOT",
    "bundle_hash",
    "call_daemon_api",
    "ensure_git",
    "enter_isolated_workspace",
    "exit_isolated_workspace",
    "setup_after_create",
]
