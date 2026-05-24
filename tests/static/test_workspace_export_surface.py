"""Phase 2.6 C3.8 invariant: pin the public ``__init__.py`` export surface.

Prevents accidental re-leak of private internals (e.g. ``_LinuxRuntime``,
``_PhaseTimer``, ``_ManagerConfig``, ``LayerSnapshotLike``, bootstrap
helpers) that pre-Phase-2.6 leaked from the iws ``__init__``. The eph
package has always exposed a single ``EphemeralPipeline`` symbol; iws
mirrors that minimalism with a tight 4-symbol surface.

If a new public symbol is genuinely needed, add it to the expected set
below in the same commit that exposes it, with a one-line rationale.
"""

from __future__ import annotations

import sandbox.ephemeral_workspace as eph
import sandbox.isolated_workspace as iws


EXPECTED_EPH_EXPORTS = frozenset({"EphemeralPipeline"})

EXPECTED_IWS_EXPORTS = frozenset(
    {
        "AuditSink",
        "IsolatedPipeline",
        "IsolatedWorkspaceError",
        "IsolatedWorkspaceHandle",
    }
)


def test_ephemeral_workspace_init_exports() -> None:
    actual = frozenset(eph.__all__)
    assert actual == EXPECTED_EPH_EXPORTS, (
        f"ephemeral_workspace.__init__ exports drifted: "
        f"added={sorted(actual - EXPECTED_EPH_EXPORTS)} "
        f"removed={sorted(EXPECTED_EPH_EXPORTS - actual)}"
    )
    for name in EXPECTED_EPH_EXPORTS:
        assert hasattr(eph, name), f"missing eph export: {name}"


def test_isolated_workspace_init_exports() -> None:
    actual = frozenset(iws.__all__)
    assert actual == EXPECTED_IWS_EXPORTS, (
        f"isolated_workspace.__init__ exports drifted: "
        f"added={sorted(actual - EXPECTED_IWS_EXPORTS)} "
        f"removed={sorted(EXPECTED_IWS_EXPORTS - actual)}"
    )
    for name in EXPECTED_IWS_EXPORTS:
        assert hasattr(iws, name), f"missing iws export: {name}"


def test_no_private_leaks_in_iws_init() -> None:
    leaked = [name for name in iws.__all__ if name.startswith("_")]
    assert not leaked, (
        f"isolated_workspace.__init__ leaks private symbols: {leaked}. "
        "Move imports of leading-underscore names to the submodule path."
    )


def test_no_private_leaks_in_eph_init() -> None:
    leaked = [name for name in eph.__all__ if name.startswith("_")]
    assert not leaked, (
        f"ephemeral_workspace.__init__ leaks private symbols: {leaked}. "
        "Move imports of leading-underscore names to the submodule path."
    )
