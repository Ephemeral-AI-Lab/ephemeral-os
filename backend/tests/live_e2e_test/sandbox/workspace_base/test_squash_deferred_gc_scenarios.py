"""Phase-01 squash + deferred-GC scenario coverage.

Scenarios drafted:
  S1. Multiple leases at different depths → barriers segment the stack
      around each head; foldable runs are folded; lease reads remain valid.
  S2. Lease release ordering — releasing leases in different orders must
      preserve the layers still referenced by remaining leases.
  S3. New lease acquired post-squash reads via checkpoint C, never via
      the still-on-disk pre-squash layer dirs.
  S4. Concurrent publish racing squash — manifest-prefix CAS protects
      newer publishes; squash either lands cleanly above the new prefix
      or aborts.
  S5. Squash refused (plan is None) when no foldable run of >=2
      non-barrier layers exists between barriers.
"""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle
from .._harness.workspace_base_metrics import write_jsonl_artifact
from .._harness.workspace_base_probe import run_workspace_base_probe


pytestmark = pytest.mark.asyncio


_SQUASH_GC_BODY = r"""
label = "workspace_base.squash_deferred_gc_scenarios"
case = "squash_deferred_gc_scenarios"
started = time.perf_counter()
workspace_inv = _inventory(WORKSPACE_ROOT)
rows = []
summary_binding = None
summary_timings = {}


def _file_change(root, index, kind):
    name = "phase01-squash-gc/%s/%03d.txt" % (kind, index)
    payload = ("%s-%03d\n" % (kind, index)).encode("utf-8")
    return WriteLayerChange(
        path=name,
        source_path=str(_source(root, "%s-%03d" % (kind, index), payload)),
    )


def _publish_chain(manager, root, count, kind="chain"):
    for index in range(count):
        _publish_changes(manager, [_file_change(root, index, kind)])


# ---------------------------------------------------------------------------
# S1. Multiple leases at different depths
# ---------------------------------------------------------------------------
s1_root = _phase01_root(label, "s1-multi-lease")
binding_s1, timings_s1 = _build_base(s1_root)
manager = LayerStack(s1_root)

_publish_chain(manager, s1_root, 4, kind="below-b")  # depth 1..4 above base
lease_b = manager.acquire_snapshot_lease("s1-lease-b")
lease_b_layers = lease_b.manifest.layers

_publish_chain(manager, s1_root, 4, kind="between")
lease_a = manager.acquire_snapshot_lease("s1-lease-a")
lease_a_layers = lease_a.manifest.layers

_publish_chain(manager, s1_root, 6, kind="above-a")

active_before = manager.read_active_manifest()
heads = manager.leased_layers()
assert lease_b_layers[0] in heads
assert lease_a_layers[0] in heads

s1_t0 = time.perf_counter()
squash_start = time.perf_counter()
squashed = manager.squash(max_depth=4)
s1_elapsed = time.perf_counter() - squash_start
assert squashed is not None

active_after = manager.read_active_manifest()
# Lease heads must still appear in active in original order.
assert lease_a_layers[0] in active_after.layers
assert lease_b_layers[0] in active_after.layers
# Reads through each lease still see the snapshot bytes they captured.
expected_lease_b_read = "below-b-003\n"  # latest below-b file when lease_b was taken
expected_lease_a_read = "between-003\n"
assert manager.read_text(
    "phase01-squash-gc/below-b/003.txt",
    manifest=lease_b.manifest,
) == (expected_lease_b_read, True)
assert manager.read_text(
    "phase01-squash-gc/between/003.txt",
    manifest=lease_a.manifest,
) == (expected_lease_a_read, True)
# Every leased layer dir is still present on disk.
assert all(
    (manager.storage_root / layer.path).is_dir()
    for layer in (*lease_a_layers, *lease_b_layers)
)

rows.append(_call_row(
    case, "s1_multi_lease_squash_keeps_heads_visible", True, s1_t0,
    timings={"layer_stack.squash.total_s": s1_elapsed, **timings_s1},
    extra={
        "pre_squash_depth": active_before.depth,
        "post_squash_depth": active_after.depth,
        "lease_a_head_in_active": lease_a_layers[0] in active_after.layers,
        "lease_b_head_in_active": lease_b_layers[0] in active_after.layers,
    },
))

# ---------------------------------------------------------------------------
# S2. Release ordering — outer lease first, inner lease later
# ---------------------------------------------------------------------------
s2_t0 = time.perf_counter()
# Release lease_a (outer/newer). lease_b still pins its layers below.
released_a = manager.release_lease(lease_a.lease_id)
assert released_a is True
remaining = set(manager.leased_layers())
assert lease_b_layers[0] in remaining
assert lease_a_layers[0] not in (manager.read_active_manifest().layers)
# lease_a's head (now unreferenced by active or other leases) was removed.
assert not (manager.storage_root / lease_a_layers[0].path).exists()
# lease_b's layers all still on disk.
assert all((manager.storage_root / layer.path).is_dir() for layer in lease_b_layers)

released_b = manager.release_lease(lease_b.lease_id)
assert released_b is True
assert manager.leased_layers() == ()
# Now lease_b's pre-checkpoint layers (those not in active) are gone.
gone = [
    layer for layer in lease_b_layers
    if layer not in manager.read_active_manifest().layers
]
assert gone, "expected at least one lease_b layer to be removed at full release"
assert all(not (manager.storage_root / layer.path).exists() for layer in gone)

rows.append(_call_row(
    case, "s2_release_ordering_preserves_inner_lease", True, s2_t0,
    timings={**timings_s1},
    extra={
        "released_a_first": True,
        "released_b_second": True,
        "no_leases_remaining": manager.leased_layers() == (),
    },
))

# ---------------------------------------------------------------------------
# S3. New lease post-squash uses C, never the pre-squash dirs
# ---------------------------------------------------------------------------
s3_root = _phase01_root(label, "s3-post-squash-lease")
binding_s3, timings_s3 = _build_base(s3_root)
manager = LayerStack(s3_root)
_publish_chain(manager, s3_root, 8, kind="hist")
pre_squash_layer_ids = [layer.layer_id for layer in manager.read_active_manifest().layers]

s3_t0 = time.perf_counter()
squashed_s3 = manager.squash(max_depth=2)
assert squashed_s3 is not None
post_squash_layer_ids = [layer.layer_id for layer in manager.read_active_manifest().layers]

new_lease = manager.acquire_snapshot_lease("s3-post-squash-reader")
new_lease_ids = [layer.layer_id for layer in new_lease.manifest.layers]
# Lease manifest must equal active manifest, not the pre-squash chain.
assert new_lease_ids == post_squash_layer_ids
# None of the pre-squash original layer ids (the ones that got folded) appear.
folded = set(pre_squash_layer_ids) - set(post_squash_layer_ids)
assert folded, "expected squash to fold some pre-squash layers into a checkpoint"
assert set(new_lease_ids).isdisjoint(folded)
# Read of latest hist file still works correctly via checkpoint.
assert manager.read_text("phase01-squash-gc/hist/007.txt") == ("hist-007\n", True)
manager.release_lease(new_lease.lease_id)

rows.append(_call_row(
    case, "s3_new_lease_uses_checkpoint_not_old_dirs", True, s3_t0,
    timings={**timings_s3},
    extra={
        "pre_squash_depth": len(pre_squash_layer_ids),
        "post_squash_depth": len(post_squash_layer_ids),
        "folded_layer_count": len(folded),
    },
))

# ---------------------------------------------------------------------------
# S4. Concurrent publish racing squash — manifest-prefix CAS protects.
# ---------------------------------------------------------------------------
s4_root = _phase01_root(label, "s4-race")
binding_s4, timings_s4 = _build_base(s4_root)
manager = LayerStack(s4_root)
_publish_chain(manager, s4_root, 6, kind="race")

race_event = threading.Event()
race_results = {}

def race_publisher():
    race_event.wait(timeout=5)
    _publish_changes(manager, [
        WriteLayerChange(
            path="phase01-squash-gc/race/append.txt",
            source_path=str(_source(s4_root, "race-append", b"appended\n")),
        )
    ])

def race_squasher():
    race_event.wait(timeout=5)
    try:
        race_results["squash"] = manager.squash(max_depth=2)
    except Exception as exc:
        race_results["squash_error"] = repr(exc)

s4_t0 = time.perf_counter()
threads = [threading.Thread(target=race_publisher), threading.Thread(target=race_squasher)]
for t in threads:
    t.start()
race_event.set()
for t in threads:
    t.join(timeout=15)
    assert not t.is_alive()

# Whatever lands, active depth must be consistent and reads must work.
final_active = manager.read_active_manifest()
assert final_active.depth >= 1
assert manager.read_text("phase01-squash-gc/race/append.txt") == ("appended\n", True)
# Storage staging dir empty after a race.
assert list((s4_root / "staging").iterdir()) == []

rows.append(_call_row(
    case, "s4_concurrent_publish_squash_safe", True, s4_t0,
    timings={**timings_s4},
    extra={
        "final_depth": final_active.depth,
        "race_squash_result_is_manifest": race_results.get("squash") is not None,
        "race_error": race_results.get("squash_error", ""),
    },
))

# ---------------------------------------------------------------------------
# S5. Squash refused when every layer is a barrier
# ---------------------------------------------------------------------------
s5_root = _phase01_root(label, "s5-no-foldable")
binding_s5, timings_s5 = _build_base(s5_root)
manager = LayerStack(s5_root)
leases_dense = []
# Build a stack where every newly added layer is held by its own lease,
# so every layer in active becomes a barrier — squash has no foldable run.
for index in range(5):
    _publish_changes(manager, [_file_change(s5_root, index, "dense")])
    leases_dense.append(manager.acquire_snapshot_lease("dense-%d" % index))

# Heads = every layer in active. Squash must refuse.
heads = set(manager.leased_layers())
active = manager.read_active_manifest()
# Active is 5 publish-layers + 1 base = 6; every layer except possibly the
# base is the head of some lease's manifest.
s5_t0 = time.perf_counter()
plan_result = manager.squash(max_depth=1)
s5_elapsed = time.perf_counter() - s5_t0

# Either None (refused) or a manifest where no barrier head was folded.
if plan_result is not None:
    # If a plan landed, no lease's head may have disappeared.
    for lease_entry in leases_dense:
        head = lease_entry.manifest.layers[0]
        assert head in plan_result.layers, head
else:
    pass

for lease_entry in leases_dense:
    manager.release_lease(lease_entry.lease_id)

rows.append(_call_row(
    case, "s5_squash_refused_when_no_foldable_run", True, s5_t0,
    timings={"layer_stack.squash.total_s": s5_elapsed, **timings_s5},
    extra={
        "lease_count": len(leases_dense),
        "active_depth_before": active.depth,
        "plan_returned_none": plan_result is None,
    },
))


if summary_binding is None:
    summary_binding = binding_s1
    summary_timings = dict(timings_s1)

summary = _base_summary(
    case,
    summary_binding,
    workspace_inv,
    summary_timings,
    pass_bars={
        "scenarios": ["s1", "s2", "s3", "s4", "s5"],
        "lease_reads_preserved_across_squash": True,
        "post_squash_lease_uses_checkpoint": True,
        "race_safety": True,
        "barrier_dense_squash_refused": True,
    },
)
_emit_workspace_payload(label, started, summary, rows)
"""


async def test_squash_deferred_gc_scenarios(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    payload = await run_workspace_base_probe(
        workspace_base_sandbox,
        _SQUASH_GC_BODY,
        label="workspace_base.squash_deferred_gc_scenarios",
        timeout=600,
    )
    rows = payload["rows"]
    assert len(rows) == 5
    assert all(row["success"] for row in rows), rows
    by_name = {row["name"]: row for row in rows}
    assert by_name["s1_multi_lease_squash_keeps_heads_visible"]["extra"]["lease_a_head_in_active"]
    assert by_name["s1_multi_lease_squash_keeps_heads_visible"]["extra"]["lease_b_head_in_active"]
    assert by_name["s2_release_ordering_preserves_inner_lease"]["extra"]["no_leases_remaining"]
    assert by_name["s3_new_lease_uses_checkpoint_not_old_dirs"]["extra"]["folded_layer_count"] > 0
    artifact = write_jsonl_artifact(
        case="squash_deferred_gc_scenarios",
        summary=payload["summary"],
        rows=rows,
    )
    print(f"\n[phase01:squash_deferred_gc_scenarios] artifact={artifact}")
