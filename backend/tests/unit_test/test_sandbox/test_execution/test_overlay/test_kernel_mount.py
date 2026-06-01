"""Unit tests for kernel_mount.py with the required mount syscalls wrappers."""

from __future__ import annotations

from pathlib import Path

import pytest

import sandbox.overlay.kernel_mount as km
from sandbox.overlay.kernel_mount import (
    mount_overlay,
    umount,
    validate_mount_inputs,
)


def test_mount_overlay_raises_on_missing_libc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_libc(_fsname: bytes) -> int:
        raise OSError("libc not found")

    monkeypatch.setattr(km, "fsopen", missing_libc)
    with pytest.raises(OSError, match="libc not found"):
        mount_overlay(
            workspace_root=Path("/workspace"),
            layer_paths=(Path("/storage/L1"),),
            upperdir=Path("/scratch/upper"),
            workdir=Path("/scratch/work"),
        )


def test_mount_overlay_calls_fsopen_then_fsconfig_per_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assert fsopen → fsconfig(lowerdir+) × N → fsconfig(upperdir) → fsconfig(workdir)
    → fsconfig(CMD_CREATE) → fsmount → move_mount sequence."""
    calls: list[tuple[object, ...]] = []

    closed: list[int] = []
    monkeypatch.setattr(km.os, "close", lambda fd: closed.append(fd))
    monkeypatch.setattr(km, "fsopen", lambda fsname: calls.append(("fsopen", fsname)) or 3)
    monkeypatch.setattr(
        km,
        "fsconfig_string",
        lambda fd, key, value: calls.append(("fsconfig_string", fd, key, value)),
    )
    monkeypatch.setattr(km, "fsconfig_create", lambda fd: calls.append(("fsconfig_create", fd)))
    monkeypatch.setattr(km, "fsmount", lambda fd: calls.append(("fsmount", fd)) or 4)
    monkeypatch.setattr(
        km,
        "move_mount",
        lambda fd, target: calls.append(("move_mount", fd, target)),
    )

    mount_overlay(
        workspace_root=Path("/workspace"),
        layer_paths=(Path("/storage/L1"), Path("/storage/L2")),
        upperdir=Path("/scratch/upper"),
        workdir=Path("/scratch/work"),
    )

    assert calls == [
        ("fsopen", b"overlay"),
        ("fsconfig_string", 3, b"lowerdir+", b"/storage/L1"),
        ("fsconfig_string", 3, b"lowerdir+", b"/storage/L2"),
        ("fsconfig_string", 3, b"upperdir", b"/scratch/upper"),
        ("fsconfig_string", 3, b"workdir", b"/scratch/work"),
        ("fsconfig_create", 3),
        ("fsmount", 3),
        ("move_mount", 4, b"/workspace"),
    ]
    assert closed == [4, 3]


def test_mount_overlay_iterates_layers_in_natural_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First element of layer_paths must be the first lowerdir+ call (top priority)."""
    lowerdir_values: list[bytes] = []

    def record_fsconfig(_fd: int, key: bytes, value: bytes) -> None:
        if key == b"lowerdir+":
            lowerdir_values.append(value)

    monkeypatch.setattr(km, "fsopen", lambda _fsname: 3)
    monkeypatch.setattr(km, "fsconfig_string", record_fsconfig)
    monkeypatch.setattr(km, "fsconfig_create", lambda _fd: None)
    monkeypatch.setattr(km, "fsmount", lambda _fd: 4)
    monkeypatch.setattr(km, "move_mount", lambda _fd, _target: None)
    monkeypatch.setattr(km.os, "close", lambda fd: None)

    layer_paths = (
        Path("/storage/newest"),
        Path("/storage/middle"),
        Path("/storage/oldest"),
    )
    mount_overlay(
        workspace_root=Path("/workspace"),
        layer_paths=layer_paths,
        upperdir=Path("/scratch/upper"),
        workdir=Path("/scratch/work"),
    )

    assert lowerdir_values == [
        km.os.fsencode("/storage/newest"),
        km.os.fsencode("/storage/middle"),
        km.os.fsencode("/storage/oldest"),
    ]


def test_mount_overlay_propagates_fsopen_errno(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = OSError(1, "operation not permitted")
    monkeypatch.setattr(km, "fsopen", lambda _fsname: (_ for _ in ()).throw(expected))

    with pytest.raises(OSError) as exc_info:
        mount_overlay(
            workspace_root=Path("/workspace"),
            layer_paths=(Path("/storage/L1"),),
            upperdir=Path("/scratch/upper"),
            workdir=Path("/scratch/work"),
        )
    assert exc_info.value is expected


@pytest.mark.parametrize(
    ("lazy", "raise_on_failure", "returncodes", "raises", "expected_calls"),
    [
        (False, False, [1], False, [("umount", "/workspace")]),
        (True, False, [1, 0], False, [("umount", "/workspace"), ("umount", "-l", "/workspace")]),
        (False, True, [1], True, [("umount", "/workspace")]),
        (True, True, [1, 1], True, [("umount", "/workspace"), ("umount", "-l", "/workspace")]),
    ],
)
def test_kernel_umount_lazy_raise_modes(
    monkeypatch: pytest.MonkeyPatch,
    lazy: bool,
    raise_on_failure: bool,
    returncodes: list[int],
    raises: bool,
    expected_calls: list[tuple[str, ...]],
) -> None:
    calls: list[tuple[str, ...]] = []

    class _Result:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

    def fake_run(argv, **_kwargs):
        calls.append(tuple(str(part) for part in argv))
        return _Result(returncodes.pop(0))

    monkeypatch.setattr(km, "_is_mountpoint", lambda path: True)
    monkeypatch.setattr(km.subprocess, "run", fake_run)

    if raises:
        with pytest.raises(RuntimeError, match="failed to detach existing mount"):
            umount(
                Path("/workspace"),
                lazy=lazy,
                raise_on_failure=raise_on_failure,
            )
    else:
        umount(
            Path("/workspace"),
            lazy=lazy,
            raise_on_failure=raise_on_failure,
        )

    assert calls == expected_calls


# ---------------------------------------------------------------------------
# validate_mount_inputs — fd layout
# ---------------------------------------------------------------------------


def test_validate_mount_inputs_keeps_real_mountpoint_and_fd_backed_layers(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    layer1 = tmp_path / "layer1"
    layer2 = tmp_path / "layer2"
    workspace_root.mkdir()
    layer1.mkdir()
    layer2.mkdir()

    inputs = validate_mount_inputs(
        workspace_root=workspace_root,
        layer_paths=(layer1, layer2),
        upperdir=tmp_path / "upper",
        workdir=tmp_path / "work",
    )
    try:
        assert inputs.workspace_root == workspace_root
        assert len(inputs.layer_paths) == 2
        assert all(p.as_posix().startswith("/proc/self/fd/") for p in inputs.layer_paths)
        assert inputs.upperdir == tmp_path / "upper"
        assert inputs.workdir == tmp_path / "work"
        # fd count: workspace + 2 layers + upperdir + workdir = 5
        assert len(inputs.fds) == 5
    finally:
        inputs.close()


def test_validate_mount_inputs_rejects_symlinked_layer(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    real_layer = tmp_path / "real_layer"
    real_layer.mkdir()
    sym_layer = tmp_path / "sym_layer"
    sym_layer.symlink_to(real_layer)

    with pytest.raises(ValueError, match="symlink"):
        validate_mount_inputs(
            workspace_root=workspace_root,
            layer_paths=(sym_layer,),
            upperdir=tmp_path / "upper",
            workdir=tmp_path / "work",
        )


def test_validate_mount_inputs_rejects_missing_layer(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    with pytest.raises(ValueError, match="missing"):
        validate_mount_inputs(
            workspace_root=workspace_root,
            layer_paths=(tmp_path / "nonexistent",),
            upperdir=tmp_path / "upper",
            workdir=tmp_path / "work",
        )


def test_validate_mount_inputs_closes_fds_on_error(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    closed: list[int] = []
    original_close = km.os.close

    import contextlib

    def tracking_close(fd: int) -> None:
        closed.append(fd)
        with contextlib.suppress(OSError):
            original_close(fd)

    import unittest.mock

    with unittest.mock.patch.object(km.os, "close", tracking_close):
        with pytest.raises(ValueError):
            validate_mount_inputs(
                workspace_root=workspace_root,
                layer_paths=(tmp_path / "nonexistent",),
                upperdir=tmp_path / "upper",
                workdir=tmp_path / "work",
            )

    assert len(closed) >= 1  # workspace_root fd was opened and closed
