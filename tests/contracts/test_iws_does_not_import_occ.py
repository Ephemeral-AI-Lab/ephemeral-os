"""Phase 2.6 C3 invariant: isolated_workspace must not import OCC.

Isolated workspace writes are confined to the per-handle upperdir and dropped
at ``exit_isolated_workspace`` via ``shutil.rmtree(scratch_dir)``. There is
no OCC commit. A future contributor who reaches for ``OCCMutationClient``
or anything under ``sandbox.occ`` from inside ``isolated_workspace/`` is
re-introducing the publish boundary the design explicitly excludes — pin
the invariant here so the regression surfaces in CI, not in production.

Rescope: the iws helper package also lives under ``isolated_workspace/``;
the same rule applies after the C3.9 ``helper/`` move because the glob is
recursive.
"""

from __future__ import annotations

from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_IWS_ROOT = _PROJECT_ROOT / "backend" / "src" / "sandbox" / "isolated_workspace"


# Forbidden symbols + modules that would re-open the OCC publish path.
# ``sandbox.occ.layer_stack_client`` is intentionally allowed — it is a pure
# layer-stack adapter (no mutation surface) that iws binds at bootstrap to
# satisfy the unified ``LayerStackPort`` contract. The mutation modules
# below ARE the publish surface and are what we want to keep out.
_FORBIDDEN_TOKENS = (
    "OCCMutationClient",
    "apply_changeset",
    "sandbox.occ.changeset",
    "sandbox.occ.maintenance",
    "sandbox.occ.publish",
    "sandbox.occ.ports",
)


def test_iws_does_not_import_occ_mutation_client() -> None:
    assert _IWS_ROOT.is_dir(), f"isolated_workspace tree missing at {_IWS_ROOT}"

    offenders: list[tuple[str, str]] = []
    for path in _IWS_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in _FORBIDDEN_TOKENS:
            if token in text:
                offenders.append((str(path.relative_to(_PROJECT_ROOT)), token))

    assert not offenders, (
        "isolated_workspace must not import OCC mutation surface; offenders: "
        + ", ".join(f"{p}: {t}" for p, t in offenders)
    )
