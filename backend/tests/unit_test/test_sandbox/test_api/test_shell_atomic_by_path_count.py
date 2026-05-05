"""Phase 4.x — single-path shell captures opt out of cross-path atomicity.

When the overlay capture from a shell call yields exactly one distinct
path, ``CommitOptions.atomic`` is set to ``False`` so that
``OccSerialMerger._disjoint_batches`` can coalesce concurrent shell
commits into a single revalidate-and-publish round-trip. Multi-path
captures keep ``atomic=True`` to preserve all-or-nothing semantics for
real workloads (e.g. ``make build``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from sandbox.occ.changeset.prepared import CommitOptions
from sandbox.occ.changeset.types import ChangesetResult, WriteChange
from sandbox.runtime import api_handlers


@dataclass
class _Manifest:
    version: int = 1
    layers: tuple[str, ...] = ()


class _StubOccService:
    """Captures the ``CommitOptions`` passed to prepare so the test can
    inspect the atomic flag."""

    def __init__(self) -> None:
        self.captured_options: list[CommitOptions] = []
        self._layer_stack = type(
            "_LS",
            (),
            {"storage_root": __import__("pathlib").Path("/tmp/eos-test-shell-atomic")},
        )()

    async def prepare_changeset(
        self,
        changes: Any,
        *,
        snapshot: Any = None,
        options: CommitOptions | None = None,
    ) -> Any:
        from types import SimpleNamespace

        del snapshot
        assert options is not None
        self.captured_options.append(options)
        # Build a minimal stand-in with the only attribute
        # ``_prepared_paths`` reads.
        return SimpleNamespace(
            path_groups=tuple(
                SimpleNamespace(path=ch.path) for ch in changes
            ),
            atomic=options.atomic,
        )

    async def commit_prepared(self, prepared: Any) -> ChangesetResult:
        del prepared
        return ChangesetResult(
            files=(),
            timings={},
            published_manifest_version=1,
        )


def _capture(paths: list[str]) -> Any:
    """Build a minimal stand-in for OverlayCapture.

    ``_apply_overlay_capture`` reads ``capture.snapshot_manifest`` and
    ``capture.timings``; the actual ``changes`` extraction is mocked
    via the autouse fixture below so the on-disk content readers in
    the real ``overlay_capture_to_occ_changes`` are bypassed.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        paths=tuple(paths),
        snapshot_manifest=_Manifest(),
        timings={},
        exit_code=0,
    )


@pytest.fixture(autouse=True)
def _patch_overlay_to_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out the on-disk content reader so the test stays in-memory.

    The real helper reads each ``OverlayPathChange``'s ``content_path``
    from the runner's spool directory; here we just emit one
    ``WriteChange`` per path so ``_apply_overlay_capture`` sees the
    distinct-path count it cares about.
    """

    def fake(capture: Any) -> tuple[WriteChange, ...]:
        return tuple(
            WriteChange(
                path=path,
                final_content=b"x",
                source="overlay_capture",
                create_only=False,
            )
            for path in capture.paths
        )

    monkeypatch.setattr(api_handlers, "overlay_capture_to_occ_changes", fake)


def test_single_path_capture_passes_atomic_false() -> None:
    service = _StubOccService()
    capture = _capture(["only/file.txt"])
    asyncio.run(
        api_handlers._apply_overlay_capture(
            capture,
            occ_service=service,  # type: ignore[arg-type]
            caller_id="t",
            description="single-path",
        )
    )
    assert len(service.captured_options) == 1
    assert service.captured_options[0].atomic is False


def test_multi_path_capture_keeps_atomic_true() -> None:
    service = _StubOccService()
    capture = _capture(["build/out.o", "build/out.so"])
    asyncio.run(
        api_handlers._apply_overlay_capture(
            capture,
            occ_service=service,  # type: ignore[arg-type]
            caller_id="t",
            description="multi-path",
        )
    )
    assert len(service.captured_options) == 1
    assert service.captured_options[0].atomic is True


def test_repeated_writes_to_one_path_are_single_path() -> None:
    """Two changes touching the same path → still one distinct path →
    atomic=False (atomicity is degenerate when there's only one path)."""
    service = _StubOccService()
    capture = _capture(["dup/file.txt", "dup/file.txt"])
    asyncio.run(
        api_handlers._apply_overlay_capture(
            capture,
            occ_service=service,  # type: ignore[arg-type]
            caller_id="t",
            description="dup",
        )
    )
    assert service.captured_options[0].atomic is False
