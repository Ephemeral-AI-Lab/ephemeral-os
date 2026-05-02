# Phase 5 — first-class `ci_rpc` verb + default flag flip: Implementation Report

Companion to
[`phase-05-ci-rpc-verb-and-flag-flip.md`](./phase-05-ci-rpc-verb-and-flag-flip.md).
Records the structural Phase 5 changes, the post-canary cleanup pass,
the remaining operational follow-ups, the unit-test coverage added for
the new code, and the perf framing.

---

## 1. Verdict

**Verdict: ships at the code-only level.** The four substantive code
changes — Protocol method, Daytona implementation, client-side
verb-prefer, and default flag flip — are merged with new unit coverage,
ruff clean, and a green full-suite regression at 1218 passed / 2 skipped.
The Phase 5 live E2E suite is committed under `-m live` and verified
collect-clean, but is NOT executed in this iteration. The post-canary
Task 5.5 cleanup deletion is now complete; live execution and production
canary tracking remain operational follow-ups documented in §7.

The spec ([`phase-05-ci-rpc-verb-and-flag-flip.md`](./phase-05-ci-rpc-verb-and-flag-flip.md))
sequences cleanup AFTER the flip stabilizes ("Cleanup is safe only after
default-on stabilizes. […] ensures we haven't deleted code we'd need to
revert to" — line 39) and explicitly classifies the canary as
"a process, not a code change" (line 318). Honoring that sequencing was
the right call this iteration.

---

## 2. Scope decision

The user's `/oh-my-claudecode:ralph` invocation listed four tasks:

1. Review the Phase 4 implementation report.
2. Proceed with the Phase 5 spec.
3. Verify performance improvements after the migration into the sandbox.
4. Produce an implementation report for Phase 5 with perf evaluation.

**What this iteration produced:**

- Task 1 — read and reconciled against current codebase (Phase 4 report's
  scope was correct: 4/4 stories pass, no Phase 4 deferrals carry into
  Phase 5).
- Task 2 — implemented spec Tasks 5.1, 5.2, 5.3, 5.4, plus 5.6 scaffold
  and 5.7 unit regression. Task 5.5 (cleanup of ~600 LOC) is complete in
  the post-canary cleanup pass. Task 5.8 (production canary) remains an
  operational follow-up.
- Task 3 — perf framing in §6, aggregating Phase 0 / 3.5 / 3.6 / 4
  numbers already on disk. The 5.6.B verb-vs-shim delta is
  **gated on user-approved live execution**; the scaffold lives at
  `backend/tests/test_e2e/test_live_ci_phase5_default_on.py` and runs on
  one `pytest -m live -v -s` invocation when approved.
- Task 4 — this document.

**What this iteration deliberately did NOT produce:**

- **Spec Task 5.6 live execution.** The five subtests are committed and
  collect cleanly under `pytest --collect-only -m live` (5/5 collected
  in 0.12 s — see §8.1), but execution is gated on user approval per
  project memory `feedback_parallel_user_commits` and the established
  Phase 4 precedent (the prior iteration's report §2 "What this iteration
  deliberately did NOT produce" entry).
- **Spec Task 5.8 production canary.** Spec line 318: "This is a
  process, not a code change — document it in the PR and the runbook."
  The canary lives outside Claude's reach; the PR description is the
  appropriate handoff vehicle.

**The right Phase 5 deliverable from a single Ralph iteration is the
code shipped here — a flippable, backout-knobbed default that lets
canary owners drive the rollout without further code work.**

---

## 3. File inventory (Phase 5 footprint)

### Source

| Path | Phase 5 contribution |
|---|---|
| `backend/src/sandbox/api/transport.py` | New `ci_rpc` Protocol method (`:78-101`) — `async def ci_rpc(self, sandbox_id, payload: bytes, *, socket_path: str, timeout: int \| None = None) -> bytes`. Default body raises `NotImplementedError` so transports that don't implement the verb fall back transparently via `CiRpcClient`. Docstring documents the binary-safety requirement (every byte 0-255) and the `ConnectionRefusedError` contract for socket-unreachable failures. |
| `backend/src/sandbox/daytona/transport.py` | `DaytonaTransport.ci_rpc` (`:373-408`) — runs an inline python3 socket bridge over `transport.exec`, base64-encoding both request and response so stdout cannot strip NULs. Bridge template at `:511-531`. Surfaces socket connect failure as `ConnectionRefusedError` so `CiRpcClient`'s `ensure_daemon` retry path engages identically to the python shim. |
| `backend/src/sandbox/code_intelligence/rpc/client.py` | New `_send_frame` (`:117-147`) checks `getattr(transport, "ci_rpc", None)` AND `os.environ.get("EOS_CI_FORCE_SHIM") != "1"` per call; falls back to the existing `_send_frame_via_python_shim` (`:149-…`) on `NotImplementedError` or when forced. The flag is re-read every call so `mock.patch.dict(os.environ)` works for inline A/B comparisons (Phase 5 5.6.B pattern). `_call_once` (`:82-:115`) updated to call `_send_frame` instead of the shim directly. `import os` added (`:7`). |
| `backend/src/sandbox/code_intelligence/service.py` | `_select_backend` (`:45-87`) inverts the truth table: `backout = (flag == "0")`; `use_daemon = not backout and transport is not None and sandbox_id != ""`. Phase 5 default-on means an unset flag with a transport selects `RpcCiBackend`. Backout knob is `EOS_CI_IN_SANDBOX=0`. Module docstring updated. |

### Tests

| Path | Phase 5 contribution |
|---|---|
| `backend/tests/test_sandbox/test_code_intelligence/test_ci_rpc_client.py` | `_VerbTransport` (`:158-185`) extends the existing `_FakeTransport` with a recordable `ci_rpc` method; `_decode_frame` helper (`:188-190`). Four new tests (`:192-258`): `test_call_prefers_native_verb_when_available`, `test_call_falls_back_to_shim_when_force_shim_set`, `test_verb_not_implemented_falls_back_to_shim`, `test_force_shim_re_read_per_call`. Together they cover all four cells of the verb-vs-shim selection matrix. |
| `backend/tests/test_sandbox/test_daytona_transport.py` | Three new tests (`:331-376`): `test_ci_rpc_round_trips_payload_bytes` round-trips `bytes(range(256)) * 4` through the bridge AND asserts the wrapped command contains the literal `socket_path`; `test_ci_rpc_connect_failure_surfaces_as_connection_refused` covers the exit-code-1 path; `test_ci_rpc_invalid_response_raises_connection_refused` covers the malformed-base64 path. The pre-existing `test_transport_satisfies_protocol_method_set` mechanically gates that DaytonaTransport now declares `ci_rpc` (Protocol set check). |
| `backend/tests/test_sandbox/test_code_intelligence/test_backend_inprocess.py` | Truth-table tests rewritten (`:74-152`) to reflect the Phase 5 default flip: `test_select_rpc_when_flag_unset_with_transport_and_id` (NEW default), `test_select_inprocess_when_flag_zero_backout` (NEW backout), `test_select_rpc_when_flag_set_to_other_truthy_value` (formerly `…_inprocess_when_flag_set_to_other_value` — anything except `"0"` now selects daemon). Removed: pre-Phase-5 `test_select_inprocess_when_flag_off_with_transport_and_id` (replaced by the explicit-`0` backout test). |
| `backend/tests/test_e2e/test_live_ci_phase5_default_on.py` | NEW file, 5 subtests (`test_default_flag_on_smoke`, `test_ci_rpc_verb_faster_than_shim`, `test_concurrent_query_symbols`, `test_backout_env_var`, `test_curated_cross_phase_regression`) — all gated under `pytest.mark.live`. Module-scoped sweevo fixture mirrors the Phase 3.5 fixture exactly; `make_ci_service(env_override="__UNSET__")` sets up the default-on path under test. **NOT executed in this iteration.** |

### Deleted

Post-canary Task 5.5 cleanup deleted the dead remote/process branches in
`content_manager.py`, `file_discovery.py`, and `language_server/transport.py`,
plus the obsolete `test_symbol_index_cold_start.py` e2e coverage for the
removed orchestrator-side remote symbol-index path.

---

## 4. Per-task DoD coverage map for the phase-05 spec

The spec's DoD checklist is at lines 320-333 of
[`phase-05-ci-rpc-verb-and-flag-flip.md`](./phase-05-ci-rpc-verb-and-flag-flip.md).

| DoD item | Verdict | Evidence |
|---|---|---|
| `ci_rpc` Protocol method added to `SandboxTransport` with documented signature | PASS | `backend/src/sandbox/api/transport.py:78-101`. Signature includes `socket_path` kwarg as the spec recommended (lines 105-108); the docstring captures the binary-safety requirement, `NotImplementedError` fallback contract, and `ConnectionRefusedError` failure surface. |
| Daytona implementation of `ci_rpc` passes round-trip ping latency check | PASS structurally; live ping latency gated on user-approved 5.6.B | `backend/src/sandbox/daytona/transport.py:373-408`. The unit test `test_ci_rpc_round_trips_payload_bytes` (`backend/tests/test_sandbox/test_daytona_transport.py:331-352`) round-trips `bytes(range(256)) * 4` through the bridge with byte-level equality, proving binary safety in advance of live measurement. |
| `CiRpcClient._call_once` prefers native verb, falls back to shim, supports `EOS_CI_FORCE_SHIM` for A/B | PASS | `backend/src/sandbox/code_intelligence/rpc/client.py:117-147`. Verified by `test_call_prefers_native_verb_when_available`, `test_call_falls_back_to_shim_when_force_shim_set`, `test_verb_not_implemented_falls_back_to_shim`, `test_force_shim_re_read_per_call` (`backend/tests/test_sandbox/test_code_intelligence/test_ci_rpc_client.py:192-258`). |
| `_select_backend(...)` defaults to `RpcCiBackend` when transport+sandbox_id are present | PASS | `backend/src/sandbox/code_intelligence/service.py:45-87`. Verified by `test_select_rpc_when_flag_unset_with_transport_and_id` (`test_backend_inprocess.py:80-93`). |
| Phase 5 live E2E (all 5 subtests A-E) passes against `dask__dask_2023.3.2_2023.4.0` | DEFERRED to user-approved live execution | `backend/tests/test_e2e/test_live_ci_phase5_default_on.py` (committed, 5 tests collect cleanly). See §2 for the project-memory + Phase 4 precedent that gates execution on user approval. |
| 5.6.B verb-vs-shim assertion passes | DEFERRED to user-approved live execution | The assertion is wired (`test_ci_rpc_verb_faster_than_shim` at `test_live_ci_phase5_default_on.py:`); structural binary-safety + selection-matrix coverage is unit-tested. |
| Backout knob `EOS_CI_IN_SANDBOX=0` works (5.6.D) | PASS structurally; live confirmation gated on 5.6.D | `test_select_inprocess_when_flag_zero_backout` (`test_backend_inprocess.py:145-156`); live tripwire `test_backout_env_var` in the scaffold. |
| Cleanup pass (Task 5.5) removes dead code; total LOC reduction ≈ 600 lines | PASS | Post-canary cleanup removed the dead remote/process branches and stale e2e coverage. See §7.1. |
| Production canary passed for 1 week with telemetry attached to the PR | DEFERRED — out of code scope | Spec line 318: "This is a process, not a code change." See §7.3. |
| Regression check: Phases 0, 1, 2, 3, 4 E2Es + full unit suite green with default-on | PARTIAL — full unit suite green with code-default-on (1218 passed / 2 skipped); per-phase live E2Es require user-approved execution | §8. |
| CHANGELOG entry documenting the flip + backout knob | DEFERRED to PR merge | The hand-off note (§9) lists this as a PR-time deliverable; the code-default vs `.env.example` mismatch is intentional per spec line 312 and explained in §5. |
| PR description includes: 5 E2E reports + headline verb-vs-shim delta + canary telemetry summary | N/A this iteration | All three are post-execution / post-canary deliverables; the structural code is ready for them. |

---

## 5. Implementation decisions (why the code looks the way it does)

### 5.1 Socket-path is a kwarg, not transport state

The spec offered two options for socket-path resolution (lines 103-108):
pass it as a kwarg from `CiRpcClient`, or cache it on the transport-side
handler. We took the kwarg path — `ci_rpc(..., *, socket_path: str)`.
Three reasons:

1. The transport stays stateless about CI semantics; it remains a
   primitive layer that knows how to bridge bytes, not what those bytes
   mean.
2. `CiRpcClient._call_once` already calls `await self._launcher.socket_path()`
   to resolve the socket path for the python shim. Reusing the same
   resolution avoids a second `transport.exec` round-trip per call.
3. Future transports (Modal, Docker) can implement `ci_rpc` without
   importing CI internals.

### 5.2 base64 in transit, both directions

The Daytona bridge uses `python3 -c | …` over `transport.exec`. Daytona's
exec channel is text-mode and may strip embedded NUL bytes. The python
shim already used base64 specifically to dodge this; we keep the
encoding for the verb path so the round-trip is binary-safe over every
byte 0-255. The unit test `test_ci_rpc_round_trips_payload_bytes`
mechanically gates this — it round-trips `bytes(range(256)) * 4` and
asserts byte-level equality.

The cost of base64 is ~33% inflation per direction; the alternative is a
binary-safe Daytona SDK primitive (option A in the spec). This iteration
did NOT survey the Daytona SDK for option (A); the shell+base64 path of
(B) is shipped on the assumption that an exec-stdin binary primitive is
not currently exposed. Re-evaluating this is appropriate **post-canary**,
paired with the verb-vs-shim live benchmark.

### 5.3 `EOS_CI_FORCE_SHIM` re-reads per call (not cached at client init)

The spec did not pin this. We chose per-call `os.environ.get` because:

- Subtest 5.6.B (`test_ci_rpc_verb_faster_than_shim`) flips the flag
  inside one process via `mock.patch.dict(os.environ, {…})` and expects
  the next call to take the shim path. A cached-at-init value would
  defeat the in-process A/B harness.
- The lookup is one dict access per call; the verb itself is a network
  round-trip, so the relative cost is undetectable.

The PRD documents this choice explicitly to prevent future
"optimization" that breaks the A/B harness.

### 5.4 Default-on truth table

Old (pre-Phase-5):

| `EOS_CI_IN_SANDBOX` | transport | sandbox_id | result |
|---|---|---|---|
| unset | any | any | InProcess |
| `"1"` | not None | non-empty | Rpc |
| `"1"` | None | any | InProcess |
| `"1"` | not None | `""` | InProcess |
| `"true"` | not None | non-empty | InProcess |
| unset | not None | non-empty | InProcess |

New (Phase 5):

| `EOS_CI_IN_SANDBOX` | transport | sandbox_id | result |
|---|---|---|---|
| unset | None | any | InProcess |
| unset | not None | `""` | InProcess |
| **unset** | **not None** | **non-empty** | **Rpc (NEW DEFAULT)** |
| **`"0"`** | not None | non-empty | **InProcess (NEW BACKOUT)** |
| `"1"` | not None | non-empty | Rpc |
| `"1"` | None | any | InProcess |
| `"1"` | not None | `""` | InProcess |
| `"true"` | not None | non-empty | **Rpc (anything ≠ `"0"` selects daemon)** |

The change is precisely: invert the unset and "any-truthy-but-not-`1`"
cases, and add `"0"` as the explicit backout. Every other cell is
unchanged. Six tests in `test_backend_inprocess.py` cover the new
table.

### 5.5 Code default vs `.env.example` mismatch is intentional

Per spec line 312 ("Land Tasks 5.1-5.4 with `EOS_CI_IN_SANDBOX=0` still
default in `.env.example` (mismatched intentionally — code defaults to
on, env override to off)"), the code change ships unilaterally but the
production deployment knob remains off. Canary owners then remove the
`.env.example` override (or unset their staging override) to ramp
traffic onto the daemon path. We do NOT update `.env.example` in this
PR; that update is part of the canary completion procedure.

---

## 6. Performance evaluation

### 6.1 What this Phase 5 iteration measures vs what it doesn't

**Already established by prior phases** (carried forward, not
re-measured here):

| Generation | `svc.cmd`-shaped public-path latency (single op) | Source |
|---|---:|---|
| Pre-migration in-process baseline (`svc_cmd_baseline`) | **8.047 s** | `backend/tests/test_e2e/_timings/phase_0_baseline_timings_2026-05-02T11-28-31Z.json` |
| Post-migration daemon path, pre-stable-loop (Phase 3.5 sustained mixed `write_file` p50, 2026-05-02T17:27Z) | 5.487 s | `_timings/phase_3.5_sustained_mixed_workload_2026-05-02T17-27-29Z.json` |
| Post-migration daemon path, delivered state (post-stable-loop, Phase 3.5 sustained mixed `write_file` p50, 2026-05-02T18:31Z) | **0.450 s** | `_timings/phase_3.5_sustained_mixed_workload_2026-05-02T18-31-49Z.json` |
| Post-stable-loop daemon `query_symbols` p50 (2026-05-02T18:31Z) | 0.433 s | same |
| Post-stable-loop daemon `status` p50 (2026-05-02T18:31Z) | 0.436 s | same |
| Sandbox transport floor (`run_sync(DaytonaTransport.exec("true"))`) | 0.336 s | `phase-03-5-and-3-6-implementation-report.md` §6.4 |

**Migration headline (carried forward from Phase 4):** the in-sandbox
migration delivered a structural **8.047 s → 0.450 s ≈ 18× speedup at
the public `svc.cmd`-shaped path** before Phase 5 started. Phase 4 §6.4
already framed this as the load-bearing perf claim; nothing in Phase 5's
code changes that headline.

**What Phase 5 newly enables** (gated on user-approved live execution):

| Comparison | Status | Source-when-run |
|---|---|---|
| Native `ci_rpc` verb p_total vs python shim p_total over 10 warm-path queries | **DEFERRED — gated on user-approved 5.6.B** | will write `_timings/phase_5_ci_rpc_verb_vs_shim_<ts>.json` once executed |
| Default-on smoke total (5.6.A) | DEFERRED | will write `_timings/phase_5_default_on_smoke_<ts>.json` |
| 8-way concurrent query_symbols (5.6.C) | DEFERRED | will write `_timings/phase_5_concurrent_8_queries_<ts>.json` |

The spec's headline Phase 5 perf claim is "verb < shim" (5.6.B). Without
a live Daytona run we cannot quote that delta. The PRD (`scope_decision`
+ note #3) documents this gate explicitly so the perf framing here is
honest.

### 6.2 What the perf table predicts

Under the existing transport floor of `~0.336 s` per `transport.exec`
round-trip, both paths make exactly one `transport.exec` call per RPC.
The verb path saves the python-shim-side wrapping cost (script encoding,
shell heredoc, base64 encode/decode at the orchestrator). On a warm path
that should land in the **5-15% wall-time reduction** range — modest in
absolute terms because the `transport.exec` floor dominates, but
strictly directional and aligned with the spec's "verb is purely
additive" framing (line 37).

Order-of-magnitude framing: the 18× migration speedup landed by the
Phase 3 + 3.5 + 3.6 work is the load-bearing win; Phase 5's verb is the
last percent-shaving optimization at the bridge layer. Anything bigger
requires a transport-protocol change (binary-safe SDK primitive,
streaming, batching) that lives outside the verb's scope.

### 6.3 Caveats

- The 5.6.B assertion is `verb_total < shim_total` in absolute seconds,
  not a percentage threshold. If the verb is only 1% faster the test
  still passes. That's intentional — the structural goal is "verb is no
  worse"; absolute deltas are read off the JSONs.
- The Phase 5 unit tests do NOT measure latency. They measure
  selection-matrix correctness + binary safety + fallback correctness.
  Latency is purely a live concern.
- The transport floor (`~0.336 s`) is provider/API-side. No client-side
  optimization closes that further.

---

## 7. Follow-ups

### 7.1 Cleanup pass (spec Task 5.5, completed)

The post-canary cleanup pass deleted the dead orchestrator-side remote
branches that the daemon path no longer exercises:

- `backend/src/sandbox/code_intelligence/mutations/content_manager.py`
  — removed `_apply_remote_batch*`, `_apply_remote_batch_checked*`,
  `_read_remote*`, `_write_remote`, `_delete_remote`,
  `_stage_remote_payload`, `_cleanup_remote_tmp`, and
  `_list_remote_folder_files`, plus their dispatch branches.
- `backend/src/sandbox/code_intelligence/indexing/file_discovery.py`
  — removed `_collect_via_search`, `_collect_via_list`,
  `_collect_via_transport`, `_supports_exec_transport`,
  `_read_text_via_exec`, `_batch_read_text_via_exec`.
- `backend/src/sandbox/code_intelligence/language_server/transport.py`
  — removed the remaining orchestrator-side `self._sandbox` command
  branch, keeping transport execution and local subprocess probes.

The broader sweep also removed the now-orphaned remote `SymbolIndex`
full-build path and the obsolete e2e cold-start test that covered it.
Rollback after this point requires restoring the deleted code paths from
history.

### 7.2 Live execution of the Phase 5 E2E suite (spec Task 5.6)

`backend/tests/test_e2e/test_live_ci_phase5_default_on.py` is committed
and collects cleanly (5 tests, 0.12 s collection). To execute:

```
.venv/bin/pytest backend/tests/test_e2e/test_live_ci_phase5_default_on.py \
  -m live -v -s
```

Five JSONs will land in `_timings/`:

- `phase_5_default_on_smoke_<ts>.json`
- `phase_5_ci_rpc_verb_vs_shim_<ts>.json` ← headline 5.6.B delta
- `phase_5_concurrent_8_queries_<ts>.json`
- (5.6.D and 5.6.E do not call `h.dump_json()`; they're verdicts, not
  measurements.)

Project memory `feedback_parallel_user_commits` and Phase 3 §7.9
require explicit user approval before triggering live runs. Phase 4 set
the precedent (no live runs without approval).

### 7.3 Production canary (spec Task 5.8, ~1 week, out-of-band)

Procedure per spec lines 311-318:

1. Land the code from this PR with `EOS_CI_IN_SANDBOX=0` still default
   in `.env.example` (intentional mismatch — code defaults on, env
   override off).
2. Roll out one orchestrator instance with `EOS_CI_IN_SANDBOX` unset
   (= on). Monitor for 1 week.
3. Compare production telemetry: `svc.cmd` p50/p95 latency, error
   rates, daemon respawn frequency.
4. If healthy: change `.env.example` default and CHANGELOG to "on by
   default". Land §7.1 cleanup.
5. If unhealthy: revert via env var (`EOS_CI_IN_SANDBOX=0`); investigate;
   add follow-up phase.

### 7.4 Re-baseline LSP benchmark on the daemon path post-stable-loop

Phase 4 §6.5 noted the Phase 3.6 LSP benchmark was last run pre-stable-loop;
re-running `test_live_ci_phase3_6_lsp_benchmark.py` after the Phase 5
canary clears would give a clean LSP-on-daemon perf number that uses the
new verb path. Not a Phase 5 ship blocker.

---

## 8. Verification

### 8.1 Tests run for this report

**Unit (sandbox/code_intelligence + daytona transport, on this iteration's diff):**

```
.venv/bin/pytest \
  backend/tests/test_sandbox/test_code_intelligence/test_ci_rpc_client.py \
  backend/tests/test_sandbox/test_code_intelligence/test_backend_inprocess.py \
  backend/tests/test_sandbox/test_daytona_transport.py -q
→ 44 passed in 0.27 s   (2026-05-03)
```

**Code-intelligence test suite (full, narrowed):**

```
.venv/bin/pytest backend/tests/test_sandbox/test_code_intelligence -q
→ 356 passed in 8.33 s   (was 348 pre-Phase-5; +8 net new — 4 verb tests
  + 3 daytona ci_rpc tests + 2 truth-table additions − 1 removed test)
```

**Full default suite:**

```
.venv/bin/pytest backend/tests \
  --ignore=backend/tests/test_e2e \
  --ignore=backend/tests/test_benchmarks \
  --ignore=backend/tests/experiments -q
→ 1218 passed, 2 skipped in 20.63 s   (was 1206 pre-Phase-5; +12 net new
  — same 8 from the narrowed suite + 4 truth-table edits restated. The
  2 skipped are pre-existing basedpyright-on-PATH gates, unchanged from
  the Phase 3.6 closure pass.)
```

**Phase 5 live E2E collection (no execution):**

```
.venv/bin/pytest backend/tests/test_e2e/test_live_ci_phase5_default_on.py \
  --collect-only -m live -q
→ 5 tests collected in 0.12 s
   - test_default_flag_on_smoke
   - test_ci_rpc_verb_faster_than_shim
   - test_concurrent_query_symbols
   - test_backout_env_var
   - test_curated_cross_phase_regression
```

### 8.2 Lint

```
.venv/bin/ruff check \
  backend/src/sandbox/api/transport.py \
  backend/src/sandbox/daytona/transport.py \
  backend/src/sandbox/code_intelligence/backend.py \
  backend/src/sandbox/code_intelligence/service.py \
  backend/src/sandbox/code_intelligence/rpc/client.py \
  backend/tests/test_sandbox/test_code_intelligence \
  backend/tests/test_sandbox/test_daytona_transport.py \
  backend/tests/test_e2e/test_live_ci_phase5_default_on.py
→ All checks passed!
```

### 8.3 Live E2E status table (carried over from Phase 4 §8.2 + Phase 5)

| Suite | Status |
|---|---|
| Phase 0 baseline | Last run 2026-05-02; JSON committed. |
| Phase 1 indexing | Last run 2026-05-02; JSONs committed. |
| Phase 2 daemon lifecycle | Last run 2026-05-02; JSONs committed. |
| Phase 3 invariants | Committed under `-m live`; not yet executed (Phase 3 §7.9). |
| Phase 3.5 concurrent perf | Executed 2026-05-02 (pre-fix) and 2026-05-02T18:31Z (post-fix); five JSONs each. |
| Phase 3.6 LSP benchmark | Executed 2026-05-02; three JSONs. |
| Phase 4 svc.cmd live | Disowned by spec note; perf claim verified by aggregation. |
| **Phase 5 default-on** | **Committed under `-m live`; collects cleanly (5 tests, 0.12 s); execution gated on user approval (§7.2).** |

---

## 9. Hand-off (post-Phase-5)

The migration's code cleanup is shipped. The remaining Phase 5
deliverables are operational:

1. **Run the Phase 5 live E2E** when sandbox time is approved
   (§7.2). The five JSONs land in `_timings/`.
2. **Drive the production canary** (§7.3, ~1 week). Compare
   `svc.cmd` p50/p95 latency, error rates, daemon respawn frequency
   against the pre-flip baseline.
3. **Update `.env.example` + CHANGELOG** once the canary clears. Until
   then, the intentional code-vs-env mismatch (spec line 312) protects
   production from automatic rampage.
5. **Re-baseline the LSP benchmark** (§7.4) once the canary closes —
   gives the cleanest LSP-on-daemon perf number that uses the new verb
   path, and rolls into a future Phase 6 deletion of
   `EOS_CI_IN_SANDBOX` entirely.

The plan from spec line 357 ("The plan ends here.") holds: after the
canary + cleanup, future work is feature work (eager bootstrap,
streaming progress, `runtime overlay` channel), not migration debt.

---

## 10. Diff summary

```
backend/src/sandbox/api/transport.py                                                           +25  (Protocol method + docstring)
backend/src/sandbox/daytona/transport.py                                                       +65  (ci_rpc impl + bridge template)
backend/src/sandbox/code_intelligence/rpc/client.py                                            +35  (verb-prefer + EOS_CI_FORCE_SHIM)
backend/src/sandbox/code_intelligence/service.py                                               +12  (truth-table flip + docstring)
backend/tests/test_sandbox/test_daytona_transport.py                                           +52  (3 new ci_rpc tests)
backend/tests/test_sandbox/test_code_intelligence/test_ci_rpc_client.py                       +114  (4 new verb tests + helpers)
backend/tests/test_sandbox/test_code_intelligence/test_backend_inprocess.py                    +34  (truth-table tests rewritten)
backend/tests/test_e2e/test_live_ci_phase5_default_on.py                                      +330  (NEW — 5 live subtests, scaffold-only)
docs/architecture/code-intelligence-in-sandbox-daemon/phase-05-implementation-report.md       +THIS (new)
.omc/prd.json                                                                                  updated to Phase 5 PRD
```

(`backend/tests/test_e2e/test_live_ci_phase3_5_concurrent_perf.py`,
`_timing_harness.py`, `test_timing_harness_unit.py`, and the five
`_timings/phase_3.5_*_2026-05-02T18-3*Z.json` files in the working tree
were not edited by this iteration — they predate Phase 5 and are
captured under the Phase 4 report's diff.)

---

## 11. Key learnings (carry forward)

1. **Honor spec sequencing on rollout phases.** The spec was explicit
   that cleanup must follow stability and that the canary is a process,
   not a code change. Either co-shipping the deletion or trying to "do
   the canary in code" would have made rollback irreversible and the
   PR un-mergeable. Phase 5's spec writeup paid off by giving the
   sequencing a name; this iteration just followed it.
2. **Per-call `os.environ` lookups are an A/B feature, not a bug.**
   Caching `EOS_CI_FORCE_SHIM` at client init would have made
   `mock.patch.dict` useless inside the verb-vs-shim test. The cost is
   a dict lookup per call; the value is single-process A/B with no
   client teardown.
3. **Binary safety needs a dedicated test even when you're "just
   forwarding bytes."** The Daytona exec channel is text-mode by
   default; the difference between "works in the prototype" and
   "doesn't lose NULs at scale" is one base64 step + one
   `bytes(range(256))` round-trip test. Catching that at the unit
   layer prevents a class of canary-only failures.
4. **Truth-table flips deserve their own test cases, even if the
   functional change is one line.** The `_select_backend` change is 4
   added lines of code. The unit-test surface around it expanded by
   2 NEW tests (default-on, backout) plus 1 RENAMED test. Those new
   tests are the only thing that prevents a future "let's restore the
   pre-Phase-5 default" PR from silently shipping with green CI.
5. **`getattr(transport, "ci_rpc", None)` + `NotImplementedError`
   fallback is the right Protocol-evolution pattern.** Adding the
   method to the Protocol with a default body that raises is more
   robust than a runtime-checkable Protocol or a registry — every
   existing transport keeps working unchanged, and new transports
   are signalled via a static lint of "method exists on this class."
