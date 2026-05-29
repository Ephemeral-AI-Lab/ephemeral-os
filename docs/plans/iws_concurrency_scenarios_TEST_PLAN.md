# Plan (2/2): complex concurrency/correctness scenario tests (TESTS)

Status: APPROVED (ralplan consensus — Architect SOUND-WITH-CHANGES → folded; Critic APPROVE).
Owner: planner handoff
Mode: SHORT consensus
Scope: targeted sandbox-backed scenario + performance tests (scenarios 0–3). The capabilities
under test ALREADY EXIST; this is coverage, audit-then-fill.

> **Companion:** `docs/plans/iws_gates_prehooks_PLAN.md` covers the gate CODE (scenarios 4/5/6 —
> the `RequireNoInflightBackgroundTasks` and `BlockInIsolatedMode` prehooks + the daemon-status
> engine wrapper). The scenarios below assume those gates exist; the exit-gate's mock-test update
> (`test_background_exit_iws_drains_agent_tasks`) lives in the gates plan, NOT here.

---

## 0. Framing — what is actually missing

Every capability named is already implemented; only the sandbox-backed scenario + perf layer is
thin. Verified state:

| # | Request | Current state | Real gap |
|---|---------|---------------|----------|
| 0 | `replace_all` + `multi_edit` correctness/perf | Implemented; **unit** tests only (`docs/plans/replace_all_and_multi_edit_PLAN.md` = COMPLETE). | Sandbox-backed "complex scenario" + **performance** coverage. |
| 1 | Concurrent background tasks (pytest/pip/edit), conflict detection, perf, disk/cpu/mem | `BackgroundTaskSupervisor` + OCC exist; unit `test_multiple_concurrent_tasks`; live-e2e concurrency tests exist but exercise **direct** tool calls, not **background** tasks with mixed ops. | A mixed-op concurrent-**background** scenario with conflict + resource assertions. **Genuine gap.** |
| 2 | Concurrent isolated workspaces + concurrent ephemeral agents; conflict; disk O(1) at lowerdir; cpu/mem | Mock suite (real-kernel) covers FS isolation, cgroup-memory isolation, parallel conflicting upperdir writes, lowerdir pinning. | **Disk-O(1)-at-lowerdir as N grows** as an explicit invariant + perf/cpu/mem at scale (live-e2e). |
| 3 | 3 parallel workspaces, same port, real server, work discarded at lifecycle end | Mock `test_two_agents_same_port`, `test_5_concurrent_network_no_interference` (real `http.server` on 8080); `isolation/test_upperdir_discarded_on_exit`. | A single **combined** 3-workspace server-on-same-port + discard-on-teardown assertion (+ live-e2e variant). |

**Net:** the genuine gaps are scenario-1 (mixed-op concurrent background tasks), scenario-0
complex+perf, the disk-O(1)-at-lowerdir invariant, and the live-e2e perf cells. Scenarios 2 & 3
correctness is largely already covered — **audit, then fill only the gaps**.

### G3 (goal)
Add only the missing sandbox-backed scenario + performance tests; correctness in the mock suite
(real-kernel), perf/disk/cpu/mem in live-e2e, reusing existing harnesses (`resource_metrics.py`,
tiered runner, `gather_with_barrier`). No rebuild of IWS correctness already covered.

### Success criterion
- **SC4.** replace_all/multi_edit complex correctness holds under a real sandbox; mixed-op
  concurrent bg tasks show correct conflict detection (OCC `ABORTED_VERSION` where expected) and
  bounded resources; N concurrent isolated workspaces keep **lowerdir bytes/inodes constant as N
  grows** (only upper grows); 3 same-port servers run without `EADDRINUSE` and their work is gone
  after exit.

---

## 1. Existing coverage + harnesses (verified anchors)

- **IWS coverage (real-kernel mock suite,
  `backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/`).** Network same-port:
  `concurrency/test_two_agents_same_port.py`, `concurrency/test_5_concurrent_network_no_interference.py`;
  FS: `concurrency/test_5_concurrent_fs_no_interference.py`; memory:
  `concurrency/test_5_concurrent_cgroup_memory_isolated.py`; conflict:
  `concurrency/test_iws_parallel_conflicting_upperdir_writes.py`; overlap:
  `concurrency/test_same_agent_tool_calls_can_overlap.py`; discard:
  `isolation/test_upperdir_discarded_on_exit.py`; lowerdir pin:
  `isolation/test_lowerdir_pinned_against_peer_publish.py`; not-OCC-published:
  `isolation/test_full_cycle_never_calls_occ.py`. Real `unshare --net`; capability-guarded by
  `has_unshare_netns()` (`_iws_fixtures.py:79-89`). These are heavy,
  `live_e2e_heavy_enabled()`+`database_configured()`-gated, run real namespaces; they **skip on
  macOS** (Linux/CI-gated).
- **Live-e2e isolated path exists.** `live_e2e_test/.../test_auto_squash_edge_cases.py:751`
  `_prepare_isolated_workspace_runtime(handle)` (enables `EOS_ISOLATED_WORKSPACE_ENABLED`, calls
  `api.isolated_workspace.enter`). Reuse for scenario 2/3 perf tests.
- **Resource harness.** `backend/tests/live_e2e_test/sandbox/_harness/resource_metrics.py`
  (`/proc/self/status` VmRSS/VmHWM, mounts, fds, `df -i` inodes, cpu user/sys),
  `integrated_cases.py` `RuntimeCallMetric` + JSONL, `concurrency.py` `gather_with_barrier`,
  tiered runner `_tools/run_tiered.py` + `tiers.toml` (honors `EOS_TIER_RUN_ID`). `edit_file`
  semantics anchors live in `docs/plans/replace_all_and_multi_edit_PLAN.md`
  (`apply_search_replace` at `backend/src/sandbox/shared/edit_apply.py`).

---

## 2. Decision (locked)

| # | Decision | Source |
|---|----------|--------|
| D9 | Scenario tests: **mock for correctness** (real-kernel), **live-e2e for perf**; **hard invariants + recorded metrics w/ thresholds**. | user Q3+Q4 |

"mock" below = the heavy `live_e2e_heavy_enabled()`+`database_configured()`-gated **real-kernel**
IWS suite (real `unshare`/namespaces; §4 note), NOT fully-mocked unit tests. It skips on macOS dev
hosts and runs on Linux/CI.

---

## 3. Per-scenario test plan

### Scenario 0 — `replace_all` / `multi_edit` complex + perf (live-e2e)
New `backend/tests/live_e2e_test/sandbox/.../test_edit_replace_all_multi_edit_scenarios.py`:
- `replace_all` over many real occurrences in a real file → all replaced.
- default mode on >1 occurrence → `ABORTED_OVERLAP` / "anchor occurrence count mismatch".
- `multi_edit` sequential evolving (edit 2 anchors on edit 1's output) + all-or-nothing (one
  failing op → file unchanged); `old_str`-not-found aborts.
- Measure `api.edit.*` timings via `RuntimeCallMetric`; assert correctness + record timing JSONL
  (D9). One large-file (≥64 KiB, many anchors) perf cell with a regression threshold vs a
  single-edit baseline. (Unit semantics already covered — do NOT re-test those.)

### Scenario 1 — concurrent background tasks, mixed ops (mock correctness + live-e2e perf)
- **Mock/integration correctness:** N background tasks (mixed: a `pytest` invocation, a
  `pip install`, and a `python` edit-loop) launched via `BackgroundTaskSupervisor`; assert each
  terminal status; overlapping same-file edits → exactly **one** OCC `ACCEPTED` and the rest
  `ABORTED_VERSION`; non-overlapping edits all land.
- **Live-e2e perf:** same workload at concurrency {1,5,10}; sample `resource_metrics`
  before/after (rss/peak, threads, fds, mounts, cpu) and `/dev/shm` run-dir bounds (phase08
  pattern); record JSONL; gate cpu/mem/latency on `max(3×c1 baseline, floor)`.

### Scenario 2 — concurrent isolated workspaces + disk-O(1) (audit + add invariant; live-e2e perf)
- **Audit (cite existing):** `test_5_concurrent_fs_no_interference`, `_cgroup_memory_isolated`,
  `iws_parallel_conflicting_upperdir_writes`, `lowerdir_pinned_against_peer_publish`.
- **Add (mock or live-e2e):** N∈{1,3,5} concurrent isolated workspaces each writing M bytes to
  its upper; assert **lowerdir bytes + inode count constant across N** (only upper ≈ N×M). Probes
  must be **lowerdir-subtree-scoped, not filesystem-scoped**: bytes via `du -sb <lowerdir>`;
  inodes via `du --inodes -s <lowerdir>` (or `find <lowerdir> -xdev | wc -l`) — **NOT `df -i`**,
  which reports the whole backing filesystem (upper+lower typically share one fs, so `df -i`
  climbs with upper and would not isolate the lowerdir invariant). If the executor confirms the
  lowerdir is its own mount in the image, `df -i` on that mount is acceptable; else use the
  subtree count. Record cpu/mem via `resource_metrics`.

### Scenario 3 — 3 workspaces, same-port server, discard-on-teardown (combine + live-e2e variant)
- **New mock test:** 3 concurrent isolated workspaces each start the **same** real server
  (`python3 -m http.server 8000`) → all three `bind` succeed (no `EADDRINUSE`); each reachable
  only on its own loopback; after `exit_isolated_workspace`, assert the server's on-disk
  artifacts (and any writes) are gone (compose with the `test_upperdir_discarded_on_exit`
  assertion). Guard with `has_unshare_netns()`.
- **Live-e2e variant:** the same on a real provider sandbox (reuse
  `_prepare_isolated_workspace_runtime`).

---

## 4. Considerations / risks

### Live-e2e isolated perf harness + "mock" ≠ fully mocked
`_prepare_isolated_workspace_runtime` exists but live-e2e isolated entry needs
`EOS_ISOLATED_WORKSPACE_ENABLED` and `unshare --net` capability inside the image; gate live-e2e
isolated perf cells behind the capability probe and skip-with-recorded-note (no silent cap) when
unavailable. The IWS "mock" suite runs **real** Linux namespaces (probes `has_unshare_netns()`),
so it is the honest home for namespace/port correctness and can sample `/proc` on the host; it
**skips on macOS** dev hosts (CI/Linux-gated, matching today's suite).

### Dependency on the gates plan
Scenario tests that enter/exit isolated mode or submit terminals run on top of the gates from
`iws_gates_prehooks_PLAN.md`. Scenario tests here run as **non-gated workloads** (no in-flight
background tasks at enter/exit boundaries) so they exercise the scenario under test, not the
gates — except where a scenario deliberately overlaps background work, in which case follow the
gate contract (cancel before exit).

---

## 5. Verification (run with `.venv/bin/pytest`, never global pytest)
- Mock correctness scenarios (Linux/CI, real-kernel) → SC4 correctness (scenarios 1 mock, 3 mock).
- Live-e2e perf cells via tiered runner (`EOS_TIER_RUN_ID`), capability-guarded → SC4 perf/disk;
  the disk-O(1) inode invariant is measured lowerdir-subtree-scoped (`du --inodes -s <lowerdir>`),
  NOT `df -i` (Scenario 2).
- Scenario 0 live-e2e edit scenarios with recorded timing JSONL → SC4 edit correctness + perf.
- `ruff check` + `mypy` clean on changed lines.

## 6. Open questions (for user override)
1. Should scenario 1's mixed-op bg test live in `backend/tests/integration_test/` or extend the
   mock task-center-runner suite? (Default: `integration_test/` for the bg-supervisor path; the
   mock suite already owns IWS.)
