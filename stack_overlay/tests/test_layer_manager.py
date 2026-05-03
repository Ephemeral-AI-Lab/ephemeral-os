from __future__ import annotations

from pathlib import Path

from stack_overlay import LayerManager, OccCommitter, WriteChange, content_hash
from stack_overlay.models import ChangeStatus, LayerChange, Manifest
from stack_overlay.mounts import DEFAULT_MAX_DEPTH, build_mount_spec


def test_long_shell_lease_keeps_old_snapshot_while_active_manifest_squashes(
    tmp_path: Path,
) -> None:
    manager = LayerManager.create(
        tmp_path,
        {"a.txt": "base\n"},
        max_depth=10,
        squash_trigger=5,
        squash_target=3,
    )
    lease = manager.acquire()

    for index in range(1, 8):
        manager.commit([LayerChange(f"f{index}.txt", "write", f"{index}\n")])

    current = manager.snapshot()
    assert current.depth < manager.squash_trigger
    assert lease.manifest.layers != current.layers
    assert manager.read_text("a.txt", lease.manifest) == ("base\n", True)
    assert manager.retired_layers()
    assert any(manager.refcount(layer) == 1 for layer in lease.manifest.layers)

    manager.release(lease)
    assert manager.collect_garbage() == []
    assert all(manager.refcount(layer) == 0 for layer in lease.manifest.layers)


def test_occ_rejects_stale_same_path_but_accepts_non_conflicting_write(
    tmp_path: Path,
) -> None:
    manager = LayerManager.create(tmp_path, {"a.txt": "v1\n"}, max_depth=10)
    occ = OccCommitter(manager)
    lease = manager.acquire()
    old_a, existed = manager.read_text("a.txt", lease.manifest)
    assert existed

    first = occ.apply(
        [
            WriteChange(
                "a.txt",
                "v2\n",
                base_existed=True,
                base_hash=content_hash(old_a),
            )
        ]
    )
    assert first.success

    stale = occ.apply(
        [
            WriteChange(
                "a.txt",
                "stale\n",
                base_existed=True,
                base_hash=content_hash(old_a),
            ),
            WriteChange("new.txt", "ok\n", base_existed=False),
        ]
    )

    assert not stale.success
    assert stale.files[0].status is ChangeStatus.ABORTED_VERSION
    assert stale.files[1].status is ChangeStatus.COMMITTED
    assert manager.read_text("a.txt") == ("v2\n", True)
    assert manager.read_text("new.txt") == ("ok\n", True)
    manager.release(lease)


def test_squash_preserves_delete_semantics(tmp_path: Path) -> None:
    manager = LayerManager.create(
        tmp_path,
        {"a.txt": "base\n", "b.txt": "keep\n"},
        max_depth=10,
        squash_trigger=4,
        squash_target=2,
    )
    manager.commit([LayerChange("a.txt", "delete")])
    manager.commit([LayerChange("c.txt", "write", "new\n")])
    manager.commit([LayerChange("d.txt", "write", "new\n")])

    assert manager.snapshot().depth <= 2
    assert manager.read_text("a.txt") == ("", False)
    assert manager.read_text("b.txt") == ("keep\n", True)
    assert manager.read_text("c.txt") == ("new\n", True)


def test_relative_mount_spec_is_short_and_depth_100_ready(tmp_path: Path) -> None:
    manager = LayerManager.create(
        tmp_path,
        {"a.txt": "0\n"},
        max_depth=DEFAULT_MAX_DEPTH + 1,
        squash_trigger=DEFAULT_MAX_DEPTH + 1,
        squash_target=40,
    )
    for index in range(1, DEFAULT_MAX_DEPTH):
        manager.commit([LayerChange(f"{index}.txt", "write", f"{index}\n")])
    manifest = manager.snapshot()
    spec = build_mount_spec(
        session_root=manager.session_root,
        manifest=manifest,
        run_dir=tmp_path / "run",
        max_depth=DEFAULT_MAX_DEPTH,
    )

    assert manifest.depth == DEFAULT_MAX_DEPTH
    assert spec.cwd == manager.session_root
    assert "/tmp/" not in spec.lowerdir
    assert len(spec.lowerdir) < 700
    assert spec.options.endswith(",userxattr")


def test_depth_cap_for_mount_spec(tmp_path: Path) -> None:
    manager = LayerManager.create(tmp_path, {"a.txt": "0\n"})
    manifest = Manifest(
        version=1,
        layers=tuple(f"L{index:04d}" for index in range(DEFAULT_MAX_DEPTH + 1)),
    )

    try:
        build_mount_spec(
            session_root=manager.session_root,
            manifest=manifest,
            run_dir=tmp_path / "run",
            max_depth=DEFAULT_MAX_DEPTH,
        )
    except ValueError as exc:
        assert "exceeds cap" in str(exc)
    else:
        raise AssertionError("expected depth cap failure")


def test_recovery_removes_unreferenced_crash_layers(tmp_path: Path) -> None:
    manager = LayerManager.create(tmp_path, {"a.txt": "0\n"}, max_depth=10)
    manager.commit([LayerChange("b.txt", "write", "1\n")])
    partial = tmp_path / "L9999"
    partial.mkdir()
    (partial / "partial.txt").write_text("partial\n", encoding="utf-8")

    restarted = LayerManager(tmp_path)

    assert restarted.missing_manifest_layers() == ()
    assert restarted.recover_unreferenced_layers() == ["L9999"]
    assert not partial.exists()
    assert restarted.read_text("b.txt") == ("1\n", True)
