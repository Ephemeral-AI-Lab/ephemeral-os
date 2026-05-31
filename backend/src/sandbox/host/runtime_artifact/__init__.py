"""Pinned ``eosd`` runtime-artifact coupling surface (consumer side).

The ENTIRE coupling between the Python host and the external ``/sandbox`` Rust
runtime is: the wire protocol, the data-type contract (see
``sandbox/_contract_fixtures/``), and THIS pin. The host fetches + verifies the
``eosd-linux-{arch}`` binary against the SHA256 recorded here before upload/exec
(verify logic lands in a later phase — this module is data only).

Phase 0 local-upload closeout: the amd64 pin is the host-local static-musl build
packaged by ``xtask`` and uploaded with provider ``put_archive``. Minisign stays
empty until the later release-grade provenance gate.
"""

from __future__ import annotations

# eosd artifact this backend is pinned to. Bumped on a coordinated protocol or
# artifact release per CONTRACT.md.
EOSD_VERSION = "0.1.0-local.20260531"

# Per-arch SHA256 of the binary. Keys = container arch tokens the host maps to
# (amd64 / arm64). arm64 remains empty until an arm64 artifact is run-verified.
EOSD_SHA256: dict[str, str] = {
    "amd64": "ad69bd919d4ed912756180927af993047166a134659d67048153317534ecb8a9",
    "arm64": "",
}

# Minisign trust-anchor public key (the release signing key). Empty for the
# Phase 0 local-upload pin; AV-8 fail-closed signature verification lands later.
MINISIGN_PUBLIC_KEY = ""

# Wire protocol version the pinned eosd speaks. MUST stay in lockstep with
# host.daemon_client.DAEMON_PROTOCOL_VERSION; a bump is a coordinated release
# event (CONTRACT.md).
PROTOCOL_VERSION = 1

__all__ = [
    "EOSD_VERSION",
    "EOSD_SHA256",
    "MINISIGN_PUBLIC_KEY",
    "PROTOCOL_VERSION",
]
