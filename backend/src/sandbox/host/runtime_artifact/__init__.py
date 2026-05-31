"""Pinned ``eosd`` runtime-artifact coupling surface (consumer side).

The ENTIRE coupling between the Python host and the external ``/sandbox`` Rust
runtime is: the wire protocol, the data-type contract (see
``sandbox/_contract_fixtures/``), and THIS pin. The host fetches + verifies the
released ``eosd-linux-{arch}`` binary against the SHA256 + minisign signature
recorded here before any upload/exec (verify logic lands in a later phase —
this module is data only).

Phase 0 scaffold: SHA256s + pubkey are placeholders until the first signed
release. ``PROTOCOL_VERSION`` is already real (mirrors
``host.daemon_client.DAEMON_PROTOCOL_VERSION``).
"""

from __future__ import annotations

# Released eosd artifact this backend is pinned to. Bumped on a coordinated
# release per CONTRACT.md. "" until the first release exists.
EOSD_VERSION = ""

# Per-arch SHA256 of the released binary. Keys = container arch tokens the host
# maps to (amd64 / arm64). "" until the first release exists.
EOSD_SHA256: dict[str, str] = {
    "amd64": "",
    "arm64": "",
}

# Minisign trust-anchor public key (the release signing key). The host verifies
# each binary's detached .minisig against THIS key (fail-closed, AV-8). "" until
# the signing key is provisioned.
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
