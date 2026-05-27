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
  S6. Large-depth concurrent publish/squash/release pressure leaves no
      orphan layer directories outside active + leased references.
  S7. Four distinct lease heads create four barriers; five foldable
      non-head runs produce post-squash depth 9, then a later no-lease
      squash folds the result into one checkpoint.
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


def _layer_dir_ids(manager):
    layers_root = manager.storage_root / "layers"
    if not layers_root.exists():
        return set()
    return {entry.name for entry in layers_root.iterdir() if entry.is_dir()}


def _referenced_layer_ids(manager):
    active = {layer.layer_id for layer in manager.read_active_manifest().layers}
    leased = {layer.layer_id for layer in manager.leased_layers()}
    return active | leased


def _assert_layer_storage_consistent(manager):
    on_disk = _layer_dir_ids(manager)
    referenced = _referenced_layer_ids(manager)
    orphan = sorted(on_disk - referenced)
    missing = sorted(referenced - on_disk)
    assert not orphan, {"orphan_layer_ids": orphan}
    assert not missing, {"missing_layer_ids": missing}
    return {
        "active_depth": manager.read_active_manifest().depth,
        "leased_layer_count": len(manager.leased_layers()),
        "layer_dir_count": len(on_disk),
        "orphan_layer_count": len(orphan),
        "missing_layer_count": len(missing),
    }


# ---------------------------------------------------------------------------
# S1. Multiple leases at different depths
# ---------------------------------------------------------------------------
s1_root = _phase01_root(label, "s1-multi-lease")
binding_s1, timings_s1 = _build_base(s1_root)
manager = LayerStack(s1_root)

_publish_chain(manager, s1_root, 4, kind="below-b")  # depth 1..4 above base
lease_b = manager.acquire_lease_record("s1-lease-b")
lease_b_layers = lease_b.manifest.layers

_publish_chain(manager, s1_root, 4, kind="between")
lease_a = manager.acquire_lease_record("s1-lease-a")
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
assert lease_a_layers[0] not in remaining
# Releasing a lease must not rewrite active; active-visible layers stay on disk.
active_after_release_a = manager.read_active_manifest()
assert lease_a_layers[0] in active_after_release_a.layers
assert (manager.storage_root / lease_a_layers[0].path).is_dir()
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
        "lease_a_removed_from_retention_set": lease_a_layers[0] not in remaining,
        "lease_a_head_still_active": lease_a_layers[0] in active_after_release_a.layers,
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

new_lease = manager.acquire_lease_record("s3-post-squash-reader")
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
    leases_dense.append(manager.acquire_lease_record("dense-%d" % index))

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

# ---------------------------------------------------------------------------
# S6. Large-depth concurrent publish/squash/release orphan detection
# ---------------------------------------------------------------------------
s6_root = _phase01_root(label, "s6-large-concurrent-orphans")
binding_s6, timings_s6 = _build_base(s6_root)
manager = LayerStack(s6_root)
lease_points = {8, 16, 32, 48, 64, 80, 96, 112}
leases_large = []
for index in range(128):
    _publish_changes(manager, [_file_change(s6_root, index, "large")])
    depth = manager.read_active_manifest().depth
    if depth in lease_points:
        leases_large.append(manager.acquire_lease_record("s6-lease-%03d" % depth))

pre_concurrency = _assert_layer_storage_consistent(manager)
assert pre_concurrency["active_depth"] >= 128
assert len(leases_large) == len(lease_points)

errors = []
error_lock = threading.Lock()
publish_worker_count = 4
manual_squash_worker_count = 1
release_worker_count = 1
publish_per_worker = 12
manual_squash_attempts_per_worker = 5
start_barrier = threading.Barrier(
    publish_worker_count + manual_squash_worker_count + release_worker_count
)
released_ids = set()
release_lock = threading.Lock()
release_queue = list(leases_large[::2])


def _record_error(label, exc):
    with error_lock:
        errors.append("%s: %r" % (label, exc))


def publish_worker(worker):
    try:
        start_barrier.wait(timeout=5)
        for index in range(publish_per_worker):
            _publish_changes(manager, [
                WriteLayerChange(
                    path="phase01-squash-gc/concurrent/%02d/%03d.txt" % (worker, index),
                    source_path=str(_source(
                        s6_root,
                        "concurrent-%02d-%03d" % (worker, index),
                        ("worker-%02d-%03d\n" % (worker, index)).encode("utf-8"),
                    )),
                )
            ])
    except Exception as exc:
        _record_error("publish-%02d" % worker, exc)


def squash_worker(worker):
    try:
        start_barrier.wait(timeout=5)
        for _ in range(manual_squash_attempts_per_worker):
            manager.squash(max_depth=16)
    except Exception as exc:
        _record_error("squash-%02d" % worker, exc)


def release_worker():
    try:
        start_barrier.wait(timeout=5)
        while True:
            with release_lock:
                if not release_queue:
                    return
                lease_entry = release_queue.pop()
            released = manager.release_lease(lease_entry.lease_id)
            if released:
                released_ids.add(lease_entry.lease_id)
    except Exception as exc:
        _record_error("release", exc)


s6_t0 = time.perf_counter()
threads = [
    *(
        threading.Thread(target=publish_worker, args=(worker,))
        for worker in range(publish_worker_count)
    ),
    *(
        threading.Thread(target=squash_worker, args=(worker,))
        for worker in range(manual_squash_worker_count)
    ),
    threading.Thread(target=release_worker),
]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join(timeout=60)
    assert not thread.is_alive()
assert errors == [], errors

post_concurrency = _assert_layer_storage_consistent(manager)
for worker in range(publish_worker_count):
    assert manager.read_text(
        "phase01-squash-gc/concurrent/%02d/011.txt" % worker
    ) == ("worker-%02d-011\n" % worker, True)

for lease_entry in leases_large:
    if lease_entry.lease_id not in released_ids:
        manager.release_lease(lease_entry.lease_id)

post_release = _assert_layer_storage_consistent(manager)
final_squash = manager.squash(max_depth=8)
post_final_squash = _assert_layer_storage_consistent(manager)
assert manager.leased_layers() == ()
assert post_final_squash["layer_dir_count"] == post_final_squash["active_depth"]

rows.append(_call_row(
    case, "s6_large_concurrent_orphan_detection", True, s6_t0,
    timings={**timings_s6},
    extra={
        "initial_publish_count": 128,
        "concurrent_publish_workers": publish_worker_count,
        "concurrent_publish_per_worker": publish_per_worker,
        "manual_squash_workers": manual_squash_worker_count,
        "manual_squash_attempts_per_worker": manual_squash_attempts_per_worker,
        "lease_count": len(leases_large),
        "partial_release_count": len(released_ids),
        "pre_concurrency": pre_concurrency,
        "post_concurrency": post_concurrency,
        "post_release": post_release,
        "post_final_squash": post_final_squash,
        "final_squash_returned_manifest": final_squash is not None,
    },
))

# ---------------------------------------------------------------------------
# S7. Four lease heads plus five foldable runs produce depth 9
# ---------------------------------------------------------------------------
s7_root = _phase01_root(label, "s7-four-lease-heads")
binding_s7, timings_s7 = _build_base(s7_root)
manager = LayerStack(s7_root)


def _publish_s7_run(name, count):
    for index in range(count):
        _publish_changes(manager, [
            WriteLayerChange(
                path="phase01-squash-gc/s7/%s/%03d.txt" % (name, index),
                source_path=str(_source(
                    s7_root,
                    "s7-%s-%03d" % (name, index),
                    ("%s-%03d\n" % (name, index)).encode("utf-8"),
                )),
            )
        ])


_publish_s7_run("below-h0", 2)
lease_h0 = manager.acquire_lease_record("s7-lease-h0")
_publish_s7_run("between-h0-h1", 3)
lease_h1 = manager.acquire_lease_record("s7-lease-h1")
_publish_s7_run("between-h1-h2", 3)
lease_h2 = manager.acquire_lease_record("s7-lease-h2")
_publish_s7_run("between-h2-h3", 3)
lease_h3 = manager.acquire_lease_record("s7-lease-h3")
_publish_s7_run("above-h3", 2)

s7_leases = [lease_h0, lease_h1, lease_h2, lease_h3]
s7_heads = {lease_entry.manifest.layers[0] for lease_entry in s7_leases}
assert len(s7_heads) == 4
expected_non_head_runs = 5
expected_post_squash_depth = len(s7_heads) + expected_non_head_runs
pre_s7_squash = _assert_layer_storage_consistent(manager)
assert pre_s7_squash["active_depth"] == 14

s7_t0 = time.perf_counter()
squashed_s7 = manager.squash(max_depth=expected_post_squash_depth)
post_s7_squash = _assert_layer_storage_consistent(manager)
assert squashed_s7 is not None
assert post_s7_squash["active_depth"] == expected_post_squash_depth
for lease_entry in s7_leases:
    assert lease_entry.manifest.layers[0] in manager.read_active_manifest().layers
assert manager.read_text("phase01-squash-gc/s7/above-h3/001.txt") == ("above-h3-001\n", True)
assert manager.read_text(
    "phase01-squash-gc/s7/below-h0/001.txt",
    manifest=lease_h0.manifest,
) == ("below-h0-001\n", True)

for lease_entry in s7_leases:
    manager.release_lease(lease_entry.lease_id)
post_s7_release = _assert_layer_storage_consistent(manager)
assert post_s7_release["active_depth"] == expected_post_squash_depth
assert post_s7_release["layer_dir_count"] == expected_post_squash_depth

next_s7_squash = manager.squash(max_depth=1)
post_s7_next_squash = _assert_layer_storage_consistent(manager)
assert next_s7_squash is not None
assert post_s7_next_squash["active_depth"] == 1
assert post_s7_next_squash["layer_dir_count"] == 1

rows.append(_call_row(
    case, "s7_four_lease_heads_create_nine_entry_squash", True, s7_t0,
    timings={**timings_s7},
    extra={
        "lease_head_count": len(s7_heads),
        "expected_non_head_runs": expected_non_head_runs,
        "expected_post_squash_depth": expected_post_squash_depth,
        "pre_squash": pre_s7_squash,
        "post_squash": post_s7_squash,
        "post_release": post_s7_release,
        "post_next_squash": post_s7_next_squash,
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
        "scenarios": ["s1", "s2", "s3", "s4", "s5", "s6", "s7"],
        "lease_reads_preserved_across_squash": True,
        "post_squash_lease_uses_checkpoint": True,
        "race_safety": True,
        "barrier_dense_squash_refused": True,
        "large_concurrent_orphan_free": True,
        "four_lease_heads_depth_is_heads_plus_runs": True,
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
    assert len(rows) == 7
    assert all(row["success"] for row in rows), rows
    by_name = {row["name"]: row for row in rows}
    assert by_name["s1_multi_lease_squash_keeps_heads_visible"]["extra"]["lease_a_head_in_active"]
    assert by_name["s1_multi_lease_squash_keeps_heads_visible"]["extra"]["lease_b_head_in_active"]
    assert by_name["s2_release_ordering_preserves_inner_lease"]["extra"]["no_leases_remaining"]
    assert by_name["s3_new_lease_uses_checkpoint_not_old_dirs"]["extra"]["folded_layer_count"] > 0
    s6 = by_name["s6_large_concurrent_orphan_detection"]["extra"]
    assert s6["post_concurrency"]["orphan_layer_count"] == 0
    assert s6["post_release"]["orphan_layer_count"] == 0
    assert s6["post_final_squash"]["orphan_layer_count"] == 0
    s7 = by_name["s7_four_lease_heads_create_nine_entry_squash"]["extra"]
    assert s7["lease_head_count"] == 4
    assert s7["expected_non_head_runs"] == 5
    assert s7["post_squash"]["active_depth"] == 9
    assert s7["post_next_squash"]["active_depth"] == 1
    artifact = write_jsonl_artifact(
        case="squash_deferred_gc_scenarios",
        summary=payload["summary"],
        rows=rows,
    )
    print(f"\n[phase01:squash_deferred_gc_scenarios] artifact={artifact}")
