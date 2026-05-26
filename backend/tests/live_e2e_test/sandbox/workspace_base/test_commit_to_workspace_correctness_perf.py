"""Phase-01 commit_to_workspace correctness + performance coverage.

Scenarios drafted:
  C1. Byte-equivalence — workspace contents after commit_to_workspace
      equal a fresh projection of the manifest taken just before commit.
  C2. Active lease refusal — commit_to_workspace raises while any lease
      is active; succeeds once all leases are released.
  C3. Base reset shape — post-commit storage has exactly one layer
      (B000001-*), and projection-temp dirs are cleaned up.
  C4. Symlink / whiteout / opaque-dir survival across commit.
  C5. Large-fanout performance — N layers folded into base, with
      separate timings for project / replace_workspace / rebuild_base.
"""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle
from .._harness.workspace_base_metrics import write_jsonl_artifact
from .._harness.workspace_base_probe import run_workspace_base_probe


pytestmark = pytest.mark.asyncio


_COMMIT_BODY = r"""
label = "workspace_base.commit_to_workspace_correctness_perf"
case = "commit_to_workspace_correctness_perf"
started = time.perf_counter()
workspace_inv = _inventory(WORKSPACE_ROOT)
rows = []
summary_binding = None
summary_timings = {}


def _tree_digest_local(root):
    root = Path(root)
    digest = hashlib.sha256()
    for current_root, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(current_root)
        dirnames.sort()
        filenames.sort()
        for dirname in dirnames:
            path = current / dirname
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                digest.update(("symlink-dir\0%s\0%s\0" % (rel, os.readlink(path))).encode("utf-8"))
            else:
                digest.update(("dir\0%s\0" % rel).encode("utf-8"))
        for filename in filenames:
            path = current / filename
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                digest.update(("symlink\0%s\0%s\0" % (rel, os.readlink(path))).encode("utf-8"))
            elif path.is_file():
                digest.update(("file\0%s\0%s\0" % (rel, _file_sha(path))).encode("utf-8"))
    return digest.hexdigest()


def _provision_workspace(workspace_root, files):
    workspace_root.mkdir(parents=True, exist_ok=True)
    for relpath, content in files.items():
        path = workspace_root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_bytes(content)


# ---------------------------------------------------------------------------
# C1. Byte-equivalence — pre-commit projection vs. post-commit workspace
# ---------------------------------------------------------------------------
c1_workspace = _phase01_root(label, "c1-workspace-src")
_provision_workspace(c1_workspace, {
    "src/a.py": "print('a')\n",
    "src/sub/b.py": "print('b')\n",
    "docs/README.md": "# doc\n",
})
c1_stack = _phase01_root(label, "c1-stack")
binding_c1, timings_c1 = _build_base(c1_stack, workspace_root=c1_workspace)
manager = LayerStack(c1_stack)
_publish_changes(manager, [
    WriteLayerChange(
        path="src/a.py",
        source_path=str(_source(c1_stack, "a-new", "print('a updated')\n")),
    ),
    WriteLayerChange(
        path="src/c.py",
        source_path=str(_source(c1_stack, "c", "print('c')\n")),
    ),
])
_publish_changes(manager, [DeleteLayerChange(path="docs/README.md")])
_publish_changes(manager, [
    WriteLayerChange(
        path="src/sub/b.py",
        source_path=str(_source(c1_stack, "b-new", "print('b updated')\n")),
    )
])

# Snapshot expected via projection BEFORE commit (snapshot-only, no swap yet).
expected_dest = c1_stack / "expected-projection"
manager.project(expected_dest)
expected_digest = _tree_digest_local(expected_dest)

c1_t0 = time.perf_counter()
commit_timings = {}
commit_manifest = manager.commit_to_workspace(
    workspace_root=c1_workspace,
    timings=commit_timings,
)
c1_elapsed = time.perf_counter() - c1_t0

after_digest = _tree_digest_local(c1_workspace)
assert after_digest == expected_digest, (after_digest, expected_digest)
assert commit_manifest.depth == 1
assert commit_manifest.layers[0].layer_id.startswith("B")
assert "layer_stack.commit_to_workspace.project_s" in commit_timings
assert "layer_stack.commit_to_workspace.replace_workspace_s" in commit_timings
assert "layer_stack.commit_to_workspace.rebuild_base_s" in commit_timings
assert "layer_stack.commit_to_workspace.total_s" in commit_timings

rows.append(_call_row(
    case, "c1_byte_equivalent_with_projection", True, c1_t0,
    timings={"layer_stack.commit_to_workspace.total_s": c1_elapsed, **commit_timings, **timings_c1},
    extra={
        "expected_digest": expected_digest,
        "workspace_digest_after": after_digest,
        "post_commit_depth": commit_manifest.depth,
    },
))

# ---------------------------------------------------------------------------
# C2. Active lease refusal
# ---------------------------------------------------------------------------
c2_workspace = _phase01_root(label, "c2-workspace")
_provision_workspace(c2_workspace, {"a.txt": "alpha\n"})
c2_stack = _phase01_root(label, "c2-stack")
binding_c2, timings_c2 = _build_base(c2_stack, workspace_root=c2_workspace)
manager = LayerStack(c2_stack)
_publish_changes(manager, [
    WriteLayerChange(
        path="a.txt",
        source_path=str(_source(c2_stack, "a-2", "alpha-2\n")),
    )
])
lease = manager.acquire_snapshot_lease("c2-blocker")

c2_t0 = time.perf_counter()
refused = False
try:
    manager.commit_to_workspace(workspace_root=c2_workspace)
except RuntimeError as exc:
    refused = "commit_to_workspace blocked by active leases" in str(exc)
assert refused, "commit_to_workspace must refuse while a lease is active"

manager.release_lease(lease.lease_id)
commit_timings_c2 = {}
post_release_manifest = manager.commit_to_workspace(
    workspace_root=c2_workspace,
    timings=commit_timings_c2,
)
assert post_release_manifest.depth == 1
assert (c2_workspace / "a.txt").read_text(encoding="utf-8") == "alpha-2\n"

rows.append(_call_row(
    case, "c2_refused_with_active_lease_then_succeeds", True, c2_t0,
    timings={**commit_timings_c2, **timings_c2},
    extra={
        "refused_initially": refused,
        "post_release_depth": post_release_manifest.depth,
    },
))

# ---------------------------------------------------------------------------
# C3. Base reset shape — single B-layer, projection temp cleaned
# ---------------------------------------------------------------------------
c3_workspace = _phase01_root(label, "c3-workspace")
_provision_workspace(c3_workspace, {"keep.txt": "k\n"})
c3_stack = _phase01_root(label, "c3-stack")
binding_c3, timings_c3 = _build_base(c3_stack, workspace_root=c3_workspace)
manager = LayerStack(c3_stack)
for index in range(5):
    _publish_changes(manager, [
        WriteLayerChange(
            path="layer-%d.txt" % index,
            source_path=str(_source(c3_stack, "layer-%d" % index, ("v%d\n" % index).encode("utf-8"))),
        )
    ])

c3_t0 = time.perf_counter()
commit_timings_c3 = {}
post_manifest_c3 = manager.commit_to_workspace(
    workspace_root=c3_workspace,
    timings=commit_timings_c3,
)
c3_elapsed = time.perf_counter() - c3_t0

layer_dirs = sorted((c3_stack / "layers").iterdir())
assert len(layer_dirs) == 1, layer_dirs
assert layer_dirs[0].name.startswith("B")
assert post_manifest_c3.depth == 1
assert post_manifest_c3.layers[0].layer_id == layer_dirs[0].name
# Temp projection root cleaned.
commit_temp = c3_stack / "runtime" / "commit"
if commit_temp.exists():
    assert list(commit_temp.iterdir()) == [], list(commit_temp.iterdir())

rows.append(_call_row(
    case, "c3_post_commit_has_single_base_layer", True, c3_t0,
    timings={"layer_stack.commit_to_workspace.total_s": c3_elapsed, **commit_timings_c3, **timings_c3},
    extra={
        "post_depth": post_manifest_c3.depth,
        "layer_dir_count": len(layer_dirs),
        "base_layer_id": layer_dirs[0].name,
        "projection_temp_clean": True,
    },
))

# ---------------------------------------------------------------------------
# C4. Symlink + whiteout + opaque-dir survival
# ---------------------------------------------------------------------------
c4_workspace = _phase01_root(label, "c4-workspace")
_provision_workspace(c4_workspace, {
    "data/keep.txt": "keep\n",
    "data/drop.txt": "drop\n",
    "subdir/old/a.txt": "old-a\n",
    "subdir/old/b.txt": "old-b\n",
})
c4_stack = _phase01_root(label, "c4-stack")
binding_c4, timings_c4 = _build_base(c4_stack, workspace_root=c4_workspace)
manager = LayerStack(c4_stack)
_publish_changes(manager, [
    DeleteLayerChange(path="data/drop.txt"),
    SymlinkLayerChange(
        path="data/link-to-keep",
        source_path="keep.txt",
    ),
])
_publish_changes(manager, [
    OpaqueDirLayerChange(path="subdir/old"),
    WriteLayerChange(
        path="subdir/old/new.txt",
        source_path=str(_source(c4_stack, "new", "new-only\n")),
    ),
])

expected_dest = c4_stack / "expected-projection"
manager.project(expected_dest)
expected_digest_c4 = _tree_digest_local(expected_dest)

c4_t0 = time.perf_counter()
commit_timings_c4 = {}
manager.commit_to_workspace(workspace_root=c4_workspace, timings=commit_timings_c4)
c4_elapsed = time.perf_counter() - c4_t0

after_digest_c4 = _tree_digest_local(c4_workspace)
assert not (c4_workspace / "data" / "drop.txt").exists()
assert (c4_workspace / "data" / "link-to-keep").is_symlink()
assert os.readlink(c4_workspace / "data" / "link-to-keep") == "keep.txt"
existing = sorted(p.name for p in (c4_workspace / "subdir" / "old").iterdir())
assert existing == ["new.txt"], existing
assert after_digest_c4 == expected_digest_c4

rows.append(_call_row(
    case, "c4_symlink_whiteout_opaque_survive_commit", True, c4_t0,
    timings={"layer_stack.commit_to_workspace.total_s": c4_elapsed, **commit_timings_c4, **timings_c4},
    extra={
        "expected_digest": expected_digest_c4,
        "workspace_digest_after": after_digest_c4,
    },
))

# ---------------------------------------------------------------------------
# C5. Large-fanout perf — N layers folded into base
# ---------------------------------------------------------------------------
fanout = int(__CFG__["fanout"])
c5_workspace = _phase01_root(label, "c5-workspace")
_provision_workspace(c5_workspace, {"seed.txt": "seed\n"})
c5_stack = _phase01_root(label, "c5-stack")
binding_c5, timings_c5 = _build_base(c5_stack, workspace_root=c5_workspace)
manager = LayerStack(c5_stack)
for index in range(fanout):
    _publish_changes(manager, [
        WriteLayerChange(
            path="bulk/%04d.txt" % index,
            source_path=str(_source(c5_stack, "bulk-%04d" % index, ("v%04d\n" % index).encode("utf-8"))),
        )
    ])

pre_depth = manager.read_active_manifest().depth
c5_t0 = time.perf_counter()
commit_timings_c5 = {}
manifest_c5 = manager.commit_to_workspace(workspace_root=c5_workspace, timings=commit_timings_c5)
c5_elapsed = time.perf_counter() - c5_t0

assert manifest_c5.depth == 1
file_count = sum(1 for _ in (c5_workspace / "bulk").iterdir())
assert file_count == fanout, file_count
for sample_index in (0, fanout // 2, fanout - 1):
    expected = "v%04d\n" % sample_index
    assert (c5_workspace / "bulk" / ("%04d.txt" % sample_index)).read_text(encoding="utf-8") == expected

rows.append(_call_row(
    case, "c5_large_fanout_perf", True, c5_t0,
    timings={
        "layer_stack.commit_to_workspace.total_s": c5_elapsed,
        **commit_timings_c5,
        **timings_c5,
    },
    extra={
        "fanout": fanout,
        "pre_commit_depth": pre_depth,
        "post_commit_depth": manifest_c5.depth,
        "workspace_bulk_files": file_count,
    },
))


if summary_binding is None:
    summary_binding = binding_c1
    summary_timings = dict(timings_c1)

commit_total_times = [
    float(row["timings"].get("layer_stack.commit_to_workspace.total_s", 0.0))
    for row in rows
]
summary_timings.update({
    "phase01.commit_to_workspace.p50_s": _percentile(commit_total_times, 50),
    "phase01.commit_to_workspace.p99_s": _percentile(commit_total_times, 99),
    "phase01.commit_to_workspace.max_s": max(commit_total_times or [0.0]),
})

summary = _base_summary(
    case,
    summary_binding,
    workspace_inv,
    summary_timings,
    pass_bars={
        "scenarios": ["c1", "c2", "c3", "c4", "c5"],
        "projection_byte_equivalence": True,
        "lease_refusal": True,
        "single_base_after_commit": True,
        "symlink_whiteout_opaque_survive": True,
        "fanout": int(__CFG__["fanout"]),
    },
)
_emit_workspace_payload(label, started, summary, rows)
"""


async def test_commit_to_workspace_correctness_perf(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    payload = await run_workspace_base_probe(
        workspace_base_sandbox,
        _COMMIT_BODY.replace("__CFG__", "json.loads(__CFG_JSON__)"),
        label="workspace_base.commit_to_workspace_correctness_perf",
        cfg={"fanout": 100},
        timeout=600,
    )
    rows = payload["rows"]
    assert len(rows) == 5
    assert all(row["success"] for row in rows), rows
    by_name = {row["name"]: row for row in rows}
    assert by_name["c1_byte_equivalent_with_projection"]["extra"]["workspace_digest_after"] == (
        by_name["c1_byte_equivalent_with_projection"]["extra"]["expected_digest"]
    )
    assert by_name["c2_refused_with_active_lease_then_succeeds"]["extra"]["refused_initially"]
    assert by_name["c3_post_commit_has_single_base_layer"]["extra"]["layer_dir_count"] == 1
    artifact = write_jsonl_artifact(
        case="commit_to_workspace_correctness_perf",
        summary=payload["summary"],
        rows=rows,
    )
    print(f"\n[phase01:commit_to_workspace_correctness_perf] artifact={artifact}")
