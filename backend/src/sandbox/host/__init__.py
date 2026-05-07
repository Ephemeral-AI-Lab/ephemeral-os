"""Orchestrator-side host package for sandbox operations.

- :mod:`sandbox.host.deploy` — build and upload the daemon bundle.
- :mod:`sandbox.host.rpc` — client for the bundled in-sandbox daemon.
- :mod:`sandbox.host.ops` — host-side operations against a sandbox
  (setup sequencing, recovery, git, workspace, context).

Layer rule: ``host.ops`` may import ``host.deploy`` and ``host.rpc``; never the
reverse.
"""

from __future__ import annotations

__all__: list[str] = []
