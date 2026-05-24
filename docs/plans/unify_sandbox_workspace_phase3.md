# Phase 3 — Test migration & documentation

**Type:** Test reshape + docs. No production behavior change.
**Scope:** Update the iws test suite to exercise the unified per-call lifecycle. Add new test tiers for tool wrappers, plugin policy, O_NOFOLLOW security (per-component walk against intermediate symlinks), pipeline lifecycle, OCC concurrency, **iws behavior upgrade** (new typed-shape verbs), **unit-level** coverage (per-module surface), and a **deployment pre-flight** CI step (`scripts/verify_overlay_preconditions.py`). Validate Tier 8 soak against a re-baselined baseline. Ship the new API surface doc + CHANGELOG.
**Depends on:** Phase 2 (per-call ephemeral pipeline, persistent isolated pipeline, lifecycle host API, agent-level tools, plugin block, iws-op deletion, host-side `sandbox/lifecycle/` package, OCC source-tag plumbing, OverlayHandle idempotency, O_NOFOLLOW per-component walk enforcement).
**Blocks:** nothing — this is the closing phase.
**Atomic commit plan:** ≤5 logical commits. Suggested split: (a) happy_path + tool_wrappers reshape; (b) policy + security tiers; (c) pipeline_lifecycle + concurrency tiers; (d) behavior_upgrade + unit tiers + observability assertions; (e) Tier 8 soak re-baseline + docs/CHANGELOG. Each commit runs full mock suite on parent SHA before landing; rollback is `git revert <sha>` per commit.

See [`unify_sandbox_workspace.md`](unify_sandbox_workspace.md) for the overview and ADR.

---

## Goals

After Phase 3 lands:
- The iws test suite drives lifecycle through the agent-level tools (`tools/isolated_workspace/{enter,exit}_isolated_workspace`) instead of raw `isolated_workspace/handlers.py` RPC calls.
- New test tiers cover the unified per-call pipeline, OCC source-tag coalescing, OverlayHandle idempotency, O_NOFOLLOW symlink-escape security, plugin-block policy, and the daemon-side workspace-dispatch concurrency invariant.
- Tier 8 soak passes against a re-baselined baseline (per-call mount cost factored in; ≤10% per-phase median drift from the new baseline).
- `docs/sandbox/api_surface.md` documents the trichotomy + tool surface + R3 fence + Intent classification + two-tier verb dispatch.
- `docs/isolated_workspace_runtime_source_blast_radius.md` reflects the new module set.
- `tests/mock/sandbox/isolated_workspace/PLAN.md` describes the new test layout.
- CHANGELOG entry records the changes from Phases 1 + 2.

---

## Step 1 — Reshape existing happy-path tests

**1.1.** Update `tests/mock/sandbox/isolated_workspace/happy_path/test_enter_then_shell_then_exit.py` to drive lifecycle through the new agent-level tools (`tools/isolated_workspace/enter_isolated_workspace`, then `tools/sandbox/shell/`, then `tools/isolated_workspace/exit_isolated_workspace`). Assert the audit sequence:
```
workspace_lifecycle_started(enter_isolated_workspace)
workspace_lifecycle_completed(enter_isolated_workspace)
sandbox_operation_started(shell)
sandbox_operation_completed(shell, workspace="isolated")
workspace_lifecycle_started(exit_isolated_workspace)
workspace_lifecycle_completed(exit_isolated_workspace)
```
The daemon-side JSONL mirror (`EOS_ISOLATED_WORKSPACE_AUDIT_PATH`) continues to receive `sandbox_isolated_workspace_{enter,exit,tool_call,evicted,gc_orphan}` events for backstop diagnostics — unchanged.

**1.2.** Update other happy-path tests that today call `isolated_workspace.handlers.enter()` / `exit_()` directly — migrate to the host-side coroutines in the new `sandbox/lifecycle/` package: `sandbox.lifecycle.enter_isolated_workspace` / `exit_isolated_workspace`. Tool-op calls migrate to `sandbox.api.<verb>` (daemon resolves workspace via `resolve_pipeline`). The `WorkspaceSession` async-CM is deferred to a test-fixture (`tests/mock/sandbox/_fixtures/workspace_session.py`) per Phase 2 §12 scope reduction; tests use the explicit pair, not the CM, unless a production caller materializes.

→ **Verify:** existing happy-path tests pass with the new sequences.

---

## Step 2 — New `tool_wrappers/` tier

**2.1.** `tests/mock/sandbox/isolated_workspace/tool_wrappers/test_enter_isolated_workspace_tool.py`:
- Drives the full path: Pydantic Input → `sandbox.lifecycle.enter_isolated_workspace` → ToolResult JSON.
- Asserts: `manifest_version` populated; `manifest_root_hash` populated; lifecycle audit pair emitted; NO tool-op `SandboxOperation` events.

**2.2.** `tests/mock/sandbox/isolated_workspace/tool_wrappers/test_exit_isolated_workspace_tool.py`:
- Asserts `evicted_upperdir_bytes`, `lifetime_s`, `phases_ms`; lifecycle audit pair.

**2.3.** `tests/mock/sandbox/isolated_workspace/tool_wrappers/test_tool_dispatch_routes_iws_after_enter.py`:
- Enter iws → call `tools/sandbox/edit_file` with a mutation → assert `workspace == "isolated"` AND the file remains in iws upperdir (NOT visible in main workspace after exit).

**2.4.** `tests/mock/sandbox/isolated_workspace/tool_wrappers/test_tool_dispatch_routes_ephemeral_after_exit.py`:
- Enter iws → exit iws → call `tools/sandbox/edit_file` → assert `workspace == "ephemeral"` AND change IS visible in main workspace (OCC committed).

**2.5.** `tests/mock/sandbox/isolated_workspace/tool_wrappers/test_iws_shell_reports_changed_paths.py`:
- Enter iws → `touch /testbed/foo` via shell → assert `changed_paths == ["/testbed/foo"]` with kind `regular`.
- Enter iws (with existing `/testbed/existing.txt`) → `rm /testbed/existing.txt` → assert kind `whiteout`.
- Exit iws → confirm neither file appears in main workspace.

**2.6.** `tests/mock/sandbox/tool_wrappers/test_unified_workspace_handles_etc_paths.py` (NEW — Principle 5):
- Call `sandbox.api.read_file` with `path="/etc/hosts"` → assert success (overlay pass-through to host fs works).
- Call `sandbox.api.write_file` with `path="/tmp/scratch_test"` → assert success.
- These were previously the "out_of_workspace" branch; now they go through the same unified pipeline.

→ **Verify:** all `tool_wrappers/` tests pass.

---

## Step 3 — New `policy/` tier

**3.1.** `tests/mock/sandbox/isolated_workspace/policy/test_destructive_pre_hook_fires_in_iws_mode.py` (Principle 7):
- Enter iws → call `tools/sandbox/shell` with `rm -rf /testbed/foo` → assert destructive pre-hook BLOCKS the call BEFORE daemon, regardless of mode.
- Positive control: benign `ls /testbed` succeeds in iws.

**3.2.** `tests/mock/sandbox/isolated_workspace/policy/test_plugin_blocked_in_isolated_workspace.py` (Principle 10):
- Enter iws → invoke `api.plugin.ensure` → assert `{"success": false, "error": {"kind": "forbidden_in_isolated_workspace", ...}}`.
- Same test with `plugin.foo.bar` to confirm block extends to dynamically-registered plugin handlers.

**3.3.** `tests/mock/sandbox/isolated_workspace/policy/test_plugin_allowed_when_no_iws_open.py`:
- Positive control without entering iws — plugin ops succeed normally.

**3.4.** `tests/mock/sandbox/isolated_workspace/policy/test_plugin_block_fails_open_when_pipeline_not_bootstrapped.py`:
- `isolated_workspace.get_active_pipeline()` returns `None` → plugin ops succeed (fail-OPEN per Principle 10).

**3.5.** `tests/mock/sandbox/isolated_workspace/policy/test_network_outbound_in_iws.py`:
- Confirm network egress works in iws (existing test — migrate to new lifecycle API).

**3.6.** `tests/mock/sandbox/isolated_workspace/policy/test_network_no_inbound_in_iws.py`:
- Confirm no inbound network (existing test — migrate).

**3.7.** `tests/mock/sandbox/security/test_namespace_denylist_protects_host_etc.py` (NEW — Phase 2 §7.5 / Architect F.5 SECURITY question):
- Enter iws (root-in-namespace can otherwise write `/etc/hosts`).
- Call `sandbox.api.write_file` with `path="/etc/hosts"` → assert refused with `forbidden_host_path` error BEFORE the kernel call (namespace-child denylist check).
- Repeat for `/var/foo`, `/proc/sysrq-trigger`, `/sys/kernel/printk`, `/boot/grub.cfg` → assert all refused.
- Positive control: write to `/tmp/scratch_iws` succeeds (overlay upperdir capture; not on the denylist).

**3.8.** `tests/mock/sandbox/policy/test_plugin_block_fail_open_emits_audit.py` (NEW — Planner F.20 / Critic Sec E.4):
- Force `isolated_workspace.get_active_pipeline()` to return `None` (un-bootstrapped).
- Invoke a plugin op.
- Assert (a) op succeeds (fail-OPEN), AND (b) one audit event `workspace_lifecycle.plugin_check_unbootstrapped` is emitted with `{op, agent_id}` payload.

→ **Verify:** all `policy/` and new `security/` denylist tests pass.

---

## Step 4 — New `security/` tier (O_NOFOLLOW)

**4.1.** `tests/mock/sandbox/security/test_namespace_symlink_escape.py` (NEW — Principle 8):
```python
def test_read_refuses_symlink_to_host(workspace_session):
    # Create /testbed/evil -> /etc/passwd inside the workspace
    workspace_session.write_file("/testbed/setup.sh",
        "ln -s /etc/passwd /testbed/evil")
    workspace_session.shell("bash /testbed/setup.sh")
    # Now attempt read
    with pytest.raises(ValueError, match="refusing to follow symlink"):
        workspace_session.read_file("/testbed/evil")
```

**4.2.** `tests/mock/sandbox/security/test_write_refuses_symlink_target.py`:
- Pre-existing symlink in workspace → `write_file` against it raises (no silent overwrite of symlink target).

**4.3.** `tests/mock/sandbox/security/test_edit_refuses_symlink_target.py`:
- Pre-existing symlink → `edit_file` raises.

**4.4.** `tests/static/test_tool_primitives_o_nofollow.py` (NEW — static AST lint):
- Walks `sandbox/_shared/tool_primitives/{read,write,edit,grep,glob,file_ops}.py` AST.
- For every call to `os.open(...)`, asserts `O_NOFOLLOW` appears in the flags.
- ALSO verifies that `tool_primitives.file_ops.open_no_follow` is the chokepoint: every `os.open` outside `file_ops.py` must call `open_no_follow` instead, OR the lint asserts the per-component walk pattern (root open with `O_DIRECTORY`; per-segment open with `O_DIRECTORY|O_NOFOLLOW|dir_fd`; final open with `flags|O_NOFOLLOW`) OR `openat2(RESOLVE_NO_SYMLINKS)`. A naive one-liner `os.open(path, flags|O_NOFOLLOW)` against a multi-segment path FAILS the lint because intermediate symlinks still resolve.
- Fails the build if any caller bypasses the chokepoint.

**4.5.** `tests/mock/sandbox/security/test_intermediate_symlink_refused.py` (NEW — Architect F.6 / Critic must-fix #15 / Principle 8):
- Create `/testbed/dir → /etc` (intermediate-component symlink).
- Attempt `sandbox.api.read_file(path="/testbed/dir/passwd")` → assert raises `ELOOP` / `ValueError("refusing to follow symlink")` because the per-component walk refuses to traverse the symlink.
- Counter-test: a single trailing-component symlink (`/testbed/evil → /etc/passwd`) — already covered by §4.1 — must continue to fail.
- This test exists because `O_NOFOLLOW` alone only protects the LAST component; the per-component walk is what defends against `/testbed/<symlink-to-host>/passwd` paths.

→ **Verify:** security tests pass; static lint catches a deliberately-broken commit (smoke-test the lint itself); intermediate-symlink test fails BEFORE Phase 1 §6.8 lands the per-component walk.

---

## Step 5 — New `pipeline_lifecycle/` tier

**5.1.** `tests/mock/sandbox/pipeline_lifecycle/test_ephemeral_upperdir_gc_after_each_call.py`:
- Make 3 sequential `write_file` calls in ephemeral mode.
- Assert each call's upperdir is destroyed before the next call starts (check filesystem state).
- Assert total upperdir disk usage stays bounded (no leak across calls).

**5.2.** `tests/mock/sandbox/pipeline_lifecycle/test_isolated_upperdir_persists_across_calls.py`:
- Enter iws.
- Call `write_file` 3 times to different paths.
- Confirm all 3 writes are visible in subsequent `read_file` calls within the session.
- Exit iws → confirm all 3 writes are GONE (upperdir discarded).

**5.3.** `tests/mock/sandbox/pipeline_lifecycle/test_overlay_handle_idempotency.py`:
- `lifecycle.create(...)` → spawn two threads → both call `lifecycle.destroy(handle)` concurrently.
- Assert exactly ONE `release_lease` syscall observed (via mock).
- Assert handle._destroyed is True.

**5.4.** `tests/mock/sandbox/pipeline_lifecycle/test_lowerdir_disk_is_o1.py`:
- Run 100 sequential ephemeral tool calls.
- Assert total scratch_root/runtime/transient-lowerdir disk usage stays bounded (no per-call accumulation).

**5.5.** `tests/mock/sandbox/pipeline_lifecycle/test_isolated_upperdir_scales_with_mutations.py` (NEW — Planner F.16 / B.5 gap / Principle 11):
- Enter iws. Make N writes of M bytes each to disjoint paths.
- Assert upperdir size ≈ N * M (within filesystem block-overhead tolerance).
- Exit iws. Assert `evicted_upperdir_bytes` matches the measured upperdir bytes.
- Counter-asserts the iws side of Principle 11: "lowerdir O(1); upperdir O(mutations-per-session) in isolated; O(parallel calls) in ephemeral."

→ **Verify:** all `pipeline_lifecycle/` tests pass.

---

## Step 6 — New `concurrency/` tier

**6.1.** `tests/mock/sandbox/concurrency/test_typed_write_coalesces_with_overlay_capture.py` (the critical OCC test):
- Launch two concurrent ephemeral `write_file` calls to disjoint paths.
- Assert both commits land in ONE `_disjoint_batches` batch (verifying `source="api_write"` is preserved through the overlay capture path).
- Counter-test: launch two concurrent `shell` calls that each touch one file → assert they do NOT coalesce (source="overlay_capture", cross-path atomicity required).
- Field-level assertion: inspect the resulting `Change` objects from each path. Single-path typed write → `c.source == "api_write"` for the lone Change. Multi-path shell write → every `c.source == "overlay_capture"`. (Asserts the 4-helper threading from Phase 2 §6.1 landed on every constructor — write, delete, symlink, opaque_dir.)
- Coverage of all 4 helper sites: include one write, one delete (`rm`), one symlink (`ln -s`), and one opaque_dir (delete a non-empty dir via shell) per source mode.

**6.2.** `tests/mock/sandbox/concurrency/test_get_handle_returns_none_during_wire_and_teardown.py`:
- Real `IsolatedPipeline` (not mocked).
- Use `asyncio.Event` barriers to interleave enter/exit with concurrent `get_handle` calls.
- Assert `get_handle` returns `None` BEFORE `_wire_handle` completes and AFTER `del self._sessions[agent_id]`.
- Preserves `manager.py:671,679` and `:775-786` ordering invariant.

**6.3.** `tests/mock/sandbox/concurrency/test_concurrent_ephemeral_writes_disjoint_paths.py`:
- 8 concurrent ephemeral `write_file` calls to disjoint paths → all 8 commit successfully.
- Assert OCC published version advanced by N (all changes landed).

**6.4.** `tests/mock/sandbox/concurrency/test_concurrent_ephemeral_writes_same_path.py`:
- 4 concurrent ephemeral `write_file` calls to the SAME path → exactly one commits successfully; others return conflict.
- Asserts CAS validation still works under the new capture-then-commit model.

**6.5.** `tests/mock/sandbox/concurrency/test_destroy_under_asyncio_interleaving.py` (NEW — Planner F.18 / Critic must-fix #5 / Scenario D.2):
- Mock `overlay.destroy` to await a barrier mid-execution.
- Construct an `EphemeralPipeline`; obtain a single `OverlayHandle`.
- Launch two `asyncio.create_task` invocations of `_destroy_with_lease_guard(handle)` against the same handle.
- Release the barrier; await both tasks.
- Assert exactly ONE `overlay.destroy` invocation completed (the other waited on the per-handle lock then early-returned via `_destroyed`).
- Assert `release_lease` is called exactly ONCE (no double-release).
- Asserts the Phase 2 §3.1 per-handle `asyncio.Lock` fix landed correctly.

**6.6.** Background tool lifecycle test tier is owned by **Phase 2.5 §11** (sub-tests A–N covering engine-wrapped asyncio.Task lifecycle, wire-cancel propagation, terminal-status precedence, engine-death TTL reap, timeout enforcement, cancel-ordering invariant, wire-cancel failure tolerance, and multi-engine split-brain). See [`unify_sandbox_workspace_phase2_5.md`](unify_sandbox_workspace_phase2_5.md) §11. Phase 3 ships nothing under this number.

**6.7.** `tests/mock/sandbox/concurrency/test_e2e_10_step_interleaved.py` (NEW — Planner E.3 gap):
- Drives a 10-step sequence interleaving lifecycle + tool ops + workspace transitions:
  1. ephemeral `read_file` `/etc/hosts` (pass-through)
  2. ephemeral `write_file` `/testbed/foo.txt`
  3. `enter_isolated_workspace`
  4. iws `write_file` `/testbed/iws_only.txt`
  5. iws `read_file` `/testbed/foo.txt` (should see Step 2's commit via lowerdir merge)
  6. iws `edit_file` `/testbed/foo.txt` (mutates only iws upperdir)
  7. iws `grep` `'pattern'` `/testbed/`
  8. `exit_isolated_workspace` (iws upperdir discarded)
  9. ephemeral `read_file` `/testbed/foo.txt` (should see Step 2's content, NOT Step 6's edit)
  10. ephemeral `read_file` `/testbed/iws_only.txt` (should fail — never committed)
- Asserts the isolation boundary at exit and the lowerdir-merge visibility at enter.

→ **Verify:** all `concurrency/` tests pass; specifically `test_destroy_under_asyncio_interleaving.py` fails BEFORE Phase 2 §3.1's lock lands.

---

## Step 6A — New `behavior_upgrade/` tier (iws verb migration is a functional upgrade)

**Critic must-fix #2 / Architect F.1 — discriminating finding:** `sandbox/isolated_workspace/ops_handlers.py` (98 lines) is a thin shell-out wrapper (`/bin/cat`, `/usr/bin/grep`, `in_ns_write.py`) returning `subprocess.run` shape (`stdout`/`stderr`/`exit_code`). It does NOT honor the typed-verb semantics (real search/replace, grep modes, 16MB cap, OCC conflict tracking, etc.). Phase 2 §4.2's `IsolatedPipeline.run_tool_call → tool_primitives.<verb>.compute` is a **behavior rewrite**, not a refactor. Parity corpus does not protect this side. A dedicated test tier asserts the NEW behavior is correct.

**6A.1.** `tests/mock/sandbox/isolated_workspace/behavior_upgrade/test_read_file_typed_shape.py`:
- Enter iws; write `/testbed/sample.txt` with known UTF-8 and binary content.
- Call `sandbox.api.read_file` (now routed through iws via `resolve_pipeline`).
- Assert response shape is `ReadResult` (`success`, `exists`, `content`, `encoding`, `timings`, `changed_paths`) — NOT the old `subprocess.run` shape (`stdout`, `stderr`, `exit_code`, `duration_s`).
- Assert 16MB cap is enforced (write a 17MB file; assert `exists=True` but a size-cap error).
- Assert `O_NOFOLLOW` blocks symlink-to-host reads.

**6A.2.** `tests/mock/sandbox/isolated_workspace/behavior_upgrade/test_write_file_typed_shape.py`:
- Assert `WriteResult` shape; assert OCC `conflict`/`conflict_reason` fields populated under contention.
- Assert atomic-overwrite-via-temp-file semantics (write to existing path doesn't leave partial state on failure).

**6A.3.** `tests/mock/sandbox/isolated_workspace/behavior_upgrade/test_edit_file_typed_shape.py`:
- Critical: iws `edit_file` historically dispatched to the same body as `write_file` (full body overwrite). After Phase 2, it must perform real search/replace.
- Assert `EditResult` shape; assert anchor-match success, anchor-miss loud `ValueError`, count-mismatch loud `ValueError`.
- This test explicitly captures the iws behavior upgrade.

**6A.4.** `tests/mock/sandbox/isolated_workspace/behavior_upgrade/test_grep_typed_shape.py`:
- Assert `GrepResult` shape with `mode` honored: `"content"` returns match lines; `"files_with_matches"` returns paths only; `"count"` returns per-file counts.
- Assert `case_insensitive`, `include_pattern`, `multiline` options honored.
- Today's iws shells out to `/usr/bin/grep -r -n` and ignores all three options → this test would FAIL today.

**6A.5.** `tests/mock/sandbox/isolated_workspace/behavior_upgrade/test_glob_typed_shape.py`:
- Assert `GlobResult` shape; assert pattern matching honors gitignore filtering and the same option set as ephemeral `glob`.

**6A.6.** `tests/mock/sandbox/isolated_workspace/behavior_upgrade/test_shell_changed_paths.py`:
- iws `shell` historically returned only `subprocess.run` shape with no `changed_paths`. After Phase 2 (which routes through `overlay.capture_changes` on the iws side too — even though no commit happens, the field gets populated for observability), `changed_paths` is populated.
- Assert `changed_paths == ["/testbed/foo"]` after `touch /testbed/foo` inside iws.

→ **Verify:** all `behavior_upgrade/` tests pass. These tests would FAIL against today's `ops_handlers.py` — they are NOT parity assertions.

---

## Step 6B — New `unit/` tier (per-module surface)

**Planner E.1 gap — MISSING unit-level coverage.** Today's plan has no `tests/sandbox/unit/` tier; integration tests cover unit-level branches by accident.

**6B.1.** `tests/sandbox/unit/test_overlay_handle.py` — `OverlayHandle` field constraints; `_destroyed` guard semantics; `namespace_pid` populated for iws and `None` for ephemeral (per Phase 1 §4.10 docstring).

**6B.2.** `tests/sandbox/unit/test_overlay_lifecycle.py` — `create` failure rollback (mount fails → no lease leaked); `capture_changes` empty-upperdir returns empty sequence; `destroy` idempotency.

**6B.3.** `tests/sandbox/unit/test_overlay_namespace.py` — `run_in_namespace` host-side error propagation; child-crash handling (SIGKILL'd child → host raises specific error, doesn't deadlock).

**6B.4.** `tests/sandbox/unit/test_overlay_namespace_child.py` — Two-tier dispatcher: VERB_TABLE lookup for `read/write/edit/grep/glob`; `if verb == "shell"` branch for shell; unknown verb raises.

**6B.5.** `tests/sandbox/unit/test_tool_primitives_file_ops.py` — `open_no_follow` per-component walk: each segment opened with `O_DIRECTORY|O_NOFOLLOW|dir_fd`; ELOOP raised on intermediate symlink; final open with caller-supplied flags.

**6B.6.** `tests/sandbox/unit/test_tool_primitives_grep.py` / `_glob.py` — per-verb pure compute (no overlay context); options matrix.

**6B.7.** `tests/sandbox/unit/test_occ_overlay_change_conversion.py` — `source` parameter pass-through; default value preserves `"overlay_capture"`; all 4 helpers (write, delete, symlink, opaque_dir) honor the kwarg.

**6B.8.** `tests/sandbox/unit/test_ephemeral_pipeline_lease_accounting.py` — `_destroy_with_lease_guard` lease accounting; per-handle lock acquisition; lock cleanup after destroy.

**6B.9.** `tests/sandbox/unit/test_isolated_pipeline_errors.py` — `enter` rejects re-entry with `LifecycleError(kind="already_open")`; `exit` rejects not-open with `LifecycleError(kind="not_open")`; `get_handle` returns `None` for unknown agent.

**6B.10.** `tests/sandbox/unit/test_dispatch_resolve_pipeline.py` — Routes to iws if agent has open handle; routes to ephemeral otherwise; fail-OPEN when iws pipeline not bootstrapped.

**6B.11.** `tests/sandbox/unit/test_lifecycle_error_kind_enumeration.py` — Asserts all 4 `LifecycleError.kind` values are exercised by at least one production code path (`already_open`, `not_open`, `quota_exceeded`, `host_ram_pressure`).

→ **Verify:** all `unit/` tier tests pass. Coverage report shows ≥90% line coverage on the new modules (`overlay/`, `_shared/tool_primitives/`, both pipelines, `dispatch.py`).

---

## Step 6C — Deployment pre-flight CI

**Planner F.10 / Critic must-fix #8 — Phase 1 §4.5 makes new mount API a hard precondition. Without a CI guard, services refuse to boot in untested environments.**

**6C.1.** `scripts/verify_overlay_preconditions.py` (NEW — landed in Phase 1 §4.5.1; tested here):
- Probes kernel for `fsopen`/`fsconfig`/`fsmount` availability (new mount API).
- Probes for private user namespace support.
- Exits non-zero with a diagnostic message if any precondition is missing.

**6C.2.** Add CI step `verify-overlay-preconditions` to `.github/workflows/sandbox-ci.yml` (or equivalent):
- Runs `scripts/verify_overlay_preconditions.py` BEFORE `pytest`.
- Build fails on non-zero exit.

**6C.3.** `tests/sandbox/unit/test_verify_overlay_preconditions_script.py`:
- Mock kernel probes; assert script exits 0 when both present; exits 1 with diagnostic when either missing.

**6C.4.** Document the tombstone flag `EOS_REQUIRE_NEW_MOUNT_API=1` (Phase 1 §4.5.3): tests must assert that setting `EOS_REQUIRE_NEW_MOUNT_API=0` permits sandbox boot on environments lacking the new mount API ONLY during the documented rollout window, and that Phase 3's final CHANGELOG entry deletes the flag.

→ **Verify:** CI step passes on prod-shaped runners; fails (correctly) when run on an artificially-degraded kernel.

---

## Step 6D — Observability assertions

**Planner E.4 — Observability gap. Tests check counts but not payload shapes.**

**6D.1.** `tests/mock/sandbox/observability/test_per_call_mount_cost_recorded.py`:
- Make 5 ephemeral tool calls.
- Assert each call's `timings` dict contains `"mount_ms"` with value > 0.
- Asserts Phase 3 §7.1's baseline-reshape claim is observable in production payloads.

**6D.2.** `tests/mock/sandbox/observability/test_iws_upperdir_realtime_gauge.py`:
- Enter iws; make N writes.
- During the session, periodically read a gauge endpoint (or audit event) reporting `upperdir_bytes`.
- Assert the gauge advances monotonically.
- Today only `evicted_upperdir_bytes` is emitted at exit; this asserts mid-session visibility.

**6D.3.** `tests/mock/sandbox/observability/test_audit_event_payload_shapes.py`:
- Round-trip enter + 1 tool op + exit.
- Assert each audit-event payload schema (4 events: lifecycle_started, sandbox_op_started/completed pair, lifecycle_completed) — no missing fields, no extra fields.

→ **Verify:** observability tier tests pass.

---

## Step 7 — Tier 8 soak baseline reshape

**7.1.** Re-baseline Tier 8 soak (`tests/live_e2e/tier_8_soak/`) against the new per-call mount cost.
- Run baseline against Phase 2 head with the new fixtures.
- Capture p50/p99 latencies for read/write/edit/grep/glob/shell + lifecycle enter/exit.
- Commit baseline as `tests/live_e2e/tier_8_soak/baseline_post_unify.json`.

**7.2.** Update soak assertion: ≤10% per-phase median drift from `baseline_post_unify.json`. Keep the original baseline as `baseline_pre_unify.json` for reference (delete after 30 days).

**7.3.** Document the expected latency changes in `docs/sandbox/api_surface.md`:
- read/grep/glob: ~5ms → ~50–150ms (per-call namespace+mount cost; accepted in user judgment for LLM workflows).
- write/edit: ~10ms → ~60–180ms.
- shell: unchanged (was already overlay-mounted).

**7.4.** Per-call latency escalation threshold (Critic D.10 — perf budget must be falsifiable):
- If `read_file` p50 in `baseline_post_unify.json` exceeds **200ms**, OR `read_file` p99 exceeds **500ms**, the Tier 8 soak job fails AND a follow-up issue is auto-filed: "Revisit Option Z vs Option Y verb-level asymmetry — read latency exceeded escalation threshold."
- This converts "accepted per user judgment" into an enforceable budget. Without this threshold the ADR's perf claim is unfalsifiable.

→ **Verify:** Tier 8 soak passes with new baseline; escalation guard exercised by an intentionally-slow CI smoke run.

---

## Step 8 — Documentation updates

**8.1.** Create `docs/sandbox/api_surface.md` (canonical user-facing doc):
- §1 Three-workspace trichotomy (with table from overview).
- §2 Agent-callable surface: 6 tool ops + 2 lifecycle ops + Intent metadata. Includes per-call latency notes (read/grep/glob: ~50–150ms; write/edit: ~60–180ms; shell unchanged).
- §3 Three-layer architecture diagram (pipelines / namespace runner / overlay primitives) WITH a visible NAMESPACE-CHILD BOUNDARY marker per Planner F.22: clearly mark which code runs in the host vs the child process.
- §4 Two-tier verb dispatch inside namespace child.
- §5 R3 import fence (per-module deny-list).
- §6 OCC source-tag semantics (api_write vs overlay_capture) — enumerate the 4 helper sites that thread the tag.
- §7 O_NOFOLLOW security model — explain that `open_no_follow` does a per-component walk (defense against intermediate symlinks), not a single-call `O_NOFOLLOW` flag.
- §8 New mount API requirement; Docker-only deployment; reference `scripts/verify_overlay_preconditions.py`.
- §9 NEW — Pass-through write semantics (Architect F.5 / Critic must-fix #9): 2×3 table for {/testbed/*, /etc/*, /tmp/*} × {ephemeral, isolated} listing read+write disposition. Document the denylist (`/etc/`, `/var/`, `/proc/`, `/sys/`, `/boot/` rejected before kernel call).
- §10 — Background tool policy: see Phase 2.5 §1 + §2 (coroutine-bound overlay; engine asyncio.Task wrapper; `ToolCallRequest.background` flag; `api.v1.cancel(request_id)` wire RPC; `api.v1.heartbeat`; engine-layer Q4 + iws-exit drain). The api_surface.md doc references Phase 2.5 for the canonical design; nothing about ShellJob / shell_launch / shell_reap appears in the new doc.
- §11 NEW — `WorkspaceSession` status: deferred to `tests/mock/sandbox/_fixtures/` until a production caller materializes (Critic must-fix #11). NOT part of the public API surface in Phase 2.

**8.2.** Update `docs/isolated_workspace_runtime_source_blast_radius.md`:
- New module set: `sandbox/overlay/*`, `sandbox/_shared/tool_primitives/*`, `sandbox/ephemeral_workspace/pipeline.py` (+ extracted helper modules per Phase 1 §3.1), `sandbox/isolated_workspace/pipeline.py` + `_lifecycle.py` + `_gc.py` + `_ttl.py` + `_quota.py` + `_runtime.py` + `_types.py`, `sandbox/lifecycle/*` (host-side coroutines + WorkspaceSession test-fixture pointer).
- Removed modules: `sandbox/execution/`, `sandbox/plugin/` (relocated), `sandbox/daemon/service/{sandbox_overlay,shell_*,overlay_*}.py`, `sandbox/daemon/handler/overlay.py`, `sandbox/isolated_workspace/ops_handlers.py`.
- Note: `sandbox/api/` continues to house CLIENT-side artifacts only (`_raw_exec.py`, `_sandbox_control.py`, `protocol.py`, `transport.py`, `tool/`). Host-side lifecycle coroutines moved to `sandbox/lifecycle/` (Critic must-fix #6).

**8.3.** Create / update `tests/mock/sandbox/isolated_workspace/PLAN.md`:
- New test layout: `happy_path/`, `tool_wrappers/`, `policy/`, `pipeline_lifecycle/`, `concurrency/`, `security/`, `behavior_upgrade/`, `unit/`, `observability/`.
- Migration notes from old `handlers.py`/`ops_handlers.py`-driven tests (the latter is being deleted).

**8.4.** Append CHANGELOG entry under `CHANGELOG.md` (or equivalent):
```
## Unify sandbox workspace API

- Refactored sandbox around three sibling workspace packages: main_workspace, ephemeral_workspace, isolated_workspace.
- Unified tool execution: all 6 verbs (read/write/edit/grep/glob/shell) flow through a kernel-overlay path in both modes.
  - Ephemeral mode mounts a fresh overlay per tool call; OCC-merges the upperdir.
  - Isolated mode mounts one overlay at enter; upperdir discarded at exit.
- Dropped copy_backed execution strategy entirely; new mount API + private mount namespaces are now hard preconditions (Docker-only).
- Deleted in-workspace / out-of-workspace branching from daemon handlers; overlay's natural pass-through handles non-workspace paths.
- Added Intent enum (READ_ONLY, WRITE_ALLOWED, LIFECYCLE) as static per-verb metadata.
- Preserved OCC disjoint-batch coalescing for single-path typed writes via source="api_write" tag.
- Added O_NOFOLLOW enforcement in tool_primitives.{read,write,edit} (security: namespace runs as root).
- New agent-level tools: tools/isolated_workspace/{enter,exit}_isolated_workspace.
- New host-side lifecycle coroutines at sandbox.lifecycle.{enter,exit}_isolated_workspace; WorkspaceSession DEFERRED to test fixture (no public API).
- Latency change: typed verbs (read/write/edit/grep/glob) gain ~50–200ms per-call namespace+mount cost.
```

→ **Verify:** docs render; CHANGELOG entry merged.

---

## Acceptance criteria

- ✅ Happy-path tests use agent-level lifecycle tools (`tools/isolated_workspace/{enter,exit}_isolated_workspace`).
- ✅ `tool_wrappers/` tier covers enter/exit tools, dispatch routing (iws after enter / ephemeral after exit), iws shell changed-paths reporting, and unified handling of `/etc/*` + `/tmp/*` paths.
- ✅ `policy/` tier covers destructive pre-hook (mode-agnostic), plugin-block (in iws + allowed when no iws + fail-open when pipeline not bootstrapped + audit event emitted on fail-open), network policy, and **host-path denylist** for `/etc/*`, `/var/*`, `/proc/*`, `/sys/*`, `/boot/*` (Critic must-fix #9).
- ✅ `security/` tier covers O_NOFOLLOW symlink-escape for read/write/edit (last-component AND intermediate-component); static AST lint enforces the chokepoint pattern (no naive `os.open(path, flags|O_NOFOLLOW)` bypassing `file_ops.open_no_follow`).
- ✅ `pipeline_lifecycle/` tier covers ephemeral per-call upperdir GC, isolated per-session persistence, OverlayHandle idempotency, O(1) lowerdir disk, AND isolated upperdir scaling-with-mutations (Planner F.16).
- ✅ `concurrency/` tier covers OCC source-tag coalescing on all 4 helper sites (api_write batches, overlay_capture doesn't), `_wire_handle` ordering invariant, concurrent disjoint-path writes, same-path conflict resolution, destroy-under-asyncio-interleaving (D.2), AND a 10-step interleaved E2E sequence. Background tool lifetime tests are owned by Phase 2.5 §11.
- ✅ `behavior_upgrade/` tier (NEW) covers the iws verb upgrade: typed-shape `ReadResult`/`WriteResult`/`EditResult` (real search/replace)/`GrepResult` (modes + options honored)/`GlobResult`/shell `changed_paths`. These tests do NOT preserve byte-equivalence with today's iws `ops_handlers.py` — they assert the upgraded behavior.
- ✅ `unit/` tier (NEW) covers per-module surface: `OverlayHandle`, `lifecycle`, `namespace`, `namespace_child`, `tool_primitives.file_ops` (per-component walk), `overlay_change_conversion` (all 4 helpers), pipeline lease accounting, lifecycle error enumeration (`already_open`, `not_open`, `quota_exceeded`, `host_ram_pressure`), `resolve_pipeline`.
- ✅ `observability/` tier (NEW) asserts `timings["mount_ms"]` populated, mid-session upperdir gauge advances monotonically, audit-event payload shapes are stable.
- ✅ Deployment pre-flight CI step `verify-overlay-preconditions` runs `scripts/verify_overlay_preconditions.py` and fails the build on kernel-degraded runners. `EOS_REQUIRE_NEW_MOUNT_API` flag tested.
- ✅ Tier 8 soak re-baselined to `baseline_post_unify.json`; ≤10% median drift assertion enforced; perf escalation threshold (read p50 > 200ms or p99 > 500ms) auto-files follow-up issue.
- ✅ `docs/sandbox/api_surface.md` exists with the 11 sections (pass-through table, `WorkspaceSession` deferral note, etc.). The §10 background tool policy section defers to Phase 2.5 for the canonical design.
- ✅ Blast-radius doc reflects new module set including extracted `manager.py` modules + `sandbox/lifecycle/`.
- ✅ `tests/mock/sandbox/isolated_workspace/PLAN.md` updated (9 tiers).
- ✅ CHANGELOG entry merged.
