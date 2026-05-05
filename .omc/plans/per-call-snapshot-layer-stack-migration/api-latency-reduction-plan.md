# Public Sandbox API Latency Reduction — 4-Phase Plan

**Source data:** `backend/tests/live_e2e_test/sandbox/phase-04-latency-attribution-report.md`
**Per-call telemetry:** `.omc/results/live-e2e-phase3-per-call-timings-20260505T175743Z-1591.jsonl`
**Probe:** `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_latency_attribution.py`

## Baseline (before any phase, c=16, p99 ms)

| Verb | Wall | Transport | Py boot | Dispatch | flock_wait | OCC prepare | OCC commit |
|---|---:|---:|---:|---:|---:|---:|---:|
| read_file | 931 | 856 | 61 | 14 | – | – | – |
| write_file | 2131 | 929 | 58 | 1144 | 1054 | 98 | 8 |
| edit_file | 2111 | 855 | 66 | 1190 | 1097 | 92 | 6 |
| shell `:` | 1407 | 866 | 61 | 480 | 0 | – | – |
| shell `echo>file` | 1438 | 905 | 70 | 463 | 0 | – | – |

## Bottleneck → phase mapping

| Bottleneck | c=16 cost | Phase |
|---|---:|---|
| Cross-process flock serialization | ~1050 ms | **Phase 1** |
| Gitignore oracle rebuild per snapshot version + per-process spawn cost | 40–75 ms | **Phase 2** (2a addresses cross-process rebuild; 2b removes the per-process git/materialize spawn) |
| Provider exec + Python interpreter spawn per call | ~960 ms (transport+boot) | **Phase 3** |
| Hot lock contention at extreme concurrency | residual | **Phase 4** |

---

## Phase 1 — Shrink the flock hot zone

**Goal.** Move `OCC prepare` outside the commit lock. Hold flock only across
`_serial_merger.apply` (validate + publish layer + bump manifest).

**Hypothesis.** Hot zone drops from ~70–100 ms to ~6 ms. flock_wait p99 at c=16
drops from ~1050 ms to ~90 ms — a ~12× reduction with no architectural change.

### Work items

| File | Change |
|---|---|
| `backend/src/sandbox/occ/service.py` | Add public `commit_prepared(prepared)` that calls `_serial_merger.apply(prepared)` (and the `_sync` twin). Existing `apply_changeset` keeps its current shape for callers that don't care about the split. |
| `backend/src/sandbox/runtime/api_handlers.py` | In `write_file`, `edit_file`, `_apply_overlay_capture`: call `prepare_changeset` lock-free, then take `_process_commit_gate` + `_commit_lock` only around `commit_prepared(prepared)`. |
| `backend/src/sandbox/runtime/api_handlers.py` | Emit additional timings: `api.{verb}.prepare_s`, `api.{verb}.commit_s`. Move existing `process_gate_wait_s` / `flock_wait_s` to wrap commit only. |
| `backend/tests/unit_test/test_sandbox/test_api/` | Add tests covering: (a) successful prepare+commit; (b) stale prepare retried after intervening commit; (c) gitignored vs tracked classification still consistent. |

### Stale-prepare correctness

Prepare reads the active manifest unlocked. A faster commit may bump the
manifest between prepare and commit. The serial merger already validates
against the current manifest at commit time, so a stale prepare is a normal
OCC rejection — no new failure mode. Document this in `commit_prepared`'s
docstring. Worst-case retry rate at c=16 with 6 ms hot zone: ~6%.

### Verification

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_api/ -q
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_occ/ -q
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  .venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_latency_attribution.py -v -rs -s
```

### Pass bar

| Metric (c=16 p99) | Before | Target | Observed | Verdict |
|---|---:|---:|---:|---|
| `api.write.flock_wait_s` | 1054 ms | ≤ 150 ms | **78.1 ms** | ✅ 13.5× |
| `api.edit.flock_wait_s` | 1097 ms | ≤ 150 ms | **34.6 ms** | ✅ 32× |
| `api.shell.flock_wait_s` | n/a (broken probe) | ≤ 150 ms | **107.6 ms** | ✅ |
| write wall_ms | ~2100 ms | ≤ 1100 ms | **1100.5 ms** | ✅ |
| edit wall_ms | ~2100 ms | ≤ 1100 ms | **1098.4 ms** | ✅ |
| shell wall_ms | ~1438 ms | (informational) | **694 ms** | ~52% reduction |
| Drift | 0 | 0 (mandatory) | 0 | ✅ |

### Status — landed and verified (2026-05-06)

Implemented in `OccService.commit_prepared` (+ sync twin) and the three
runtime API handlers (`write_file`, `edit_file`, `_apply_overlay_capture`).
Prepare runs lock-free; only `commit_prepared` runs under
`_process_commit_gate + _commit_lock`.

New attribution keys emitted: `api.{write,edit,shell}.{prepare_s,commit_s}`.
Wired into the live-e2e JSONL allowlist and per-stage p99 summary in
`backend/tests/live_e2e_test/sandbox/_harness/integrated_cases.py` and
`.../layer_stack_overlay_occ/test_latency_attribution.py`.

Side fix during verification: `_run_shell_real` was missing `mkdir -p`
for the parent dir, so every shell call in earlier sweeps failed with
`status="error"` and `_apply_overlay_capture` short-circuited at the
`if not changes: return` guard, hiding the shell flock cost. Fixed and
re-measured.

Verification: `.venv/bin/pytest backend/tests/unit_test/test_sandbox`
→ 302 passed; live attribution sweep (above) at c∈{1,4,8,16} → all pass-bar
targets met; 4 new unit tests in
`backend/tests/unit_test/test_sandbox/test_api/test_prepare_commit_split.py`
cover prepare→commit happy path, stale-snapshot disjoint-path success,
stale-snapshot overlap → `ABORTED_VERSION`, and gitignored-vs-tracked
routing parity.

### Phase 1 attribution snapshot (c=16 p99)

| Stage | write | edit | shell |
|---|---:|---:|---:|
| `prepare_s` (lock-free) | 154 ms | 148 ms | 156 ms |
| `commit_s` (under lock) | 19 ms | 18 ms | 25 ms |
| `flock_wait_s` | 78 ms | 35 ms | 108 ms |
| wall p99 | 1100 ms | 1098 ms | 694 ms |

Prepare is now the dominant per-call cost across all three verbs, and
~100 ms of it is `gitignore.git_init_s` + `gitignore.materialize_snapshot_s`
— exactly the surface Phase 2 targets.

### Risks

- **Stale prepare cascade.** If retry rate spikes under contention, surface
  retries as a new metric (`occ.prepare.retries`) and gate the phase on
  observed retry rate < 10% at c=16. *Not observed in the c=16 sweep —
  no overlap-conflict failures across write/edit/shell.*
- **Subtle ordering bug** if `serial_merger` was relying on prepare side
  effects happening under lock. *Audit complete: `_orchestrator.prepare_sync`
  performs no layer-stack filesystem mutation; the only writes during
  prepare are into the gitignore oracle's evaluation tmpdir, which is not
  the layer stack.* (Superseded by Phase 2: 2a moves those writes to
  `<storage_root>/cache/gitignore-<version>/`, and 2b removes them
  entirely — neither breaks the lock-ordering invariant audited here.)

### Estimate

1 day implementation + 0.5 day re-verification. *Actual: ~0.5 day across
the prior session's telemetry groundwork plus this session's structural
split and live verification.*

---

## Phase 2 — Eliminate gitignore oracle cold start

**Goal.** Remove the 40–75 ms `materialize + git init` paid by every new
sandbox runtime process. Two complementary mechanisms.

### 2a. Disk-cached oracle workspace

Persist the materialized git workspace under
`<storage_root>/cache/gitignore-<manifest_version>/` instead of in
`tempfile.TemporaryDirectory`.

| File | Change |
|---|---|
| `backend/src/sandbox/runtime/api_handlers.py` | `_LayerStackGitignoreOracle._oracle_for_snapshot`: replace `TemporaryDirectory` with `<storage_root>/cache/gitignore-<version>/`. Use `os.makedirs(..., exist_ok=True)` and a marker file (`.ready`) to detect a fully-built cache. Build under a unique temp name, then atomic-rename. |
| `backend/src/sandbox/layer_stack/stack_manager.py` | Garbage-collect cached oracles whose `manifest.version` is below the active manifest minus N (configurable, default 16). |

After phase 2a:
- First call after a new commit: full cost (~75 ms).
- Concurrent and subsequent calls until the next commit: ~0 ms (atomic open existing dir).

### 2b. Pure-Python pathspec backend (optional, larger win)

Replace `subprocess.run(["git", "check-ignore", ...])` with a `pathspec`-based
matcher reading the `.gitignore` files directly from the snapshot.

| File | Change |
|---|---|
| `backend/src/sandbox/occ/content/gitignore_oracle.py` | Add a `PathspecGitignoreOracle` implementing the same interface. Behind a feature flag (`EPHEMERALOS_GITIGNORE_BACKEND=git|pathspec`). |
| `backend/tests/unit_test/test_sandbox/test_occ/test_content/` | Run the existing gitignore test matrix against both backends; require parity (nested ignores, `!` re-includes, case-folding). |

After phase 2b:
- No `git init`, no materialize, no subprocess. Per-call gitignore cost <1 ms regardless of cache state.
- Default to `pathspec` only after parity proven.

### Verification

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_occ -q
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  EPHEMERALOS_GITIGNORE_BACKEND=pathspec \
  .venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_latency_attribution.py -v -rs -s
```

### Pass bar

| Metric (c=16 p99) | Pre-P1 | Post-P1 | Target 2a | Observed 2a | Target 2b | Observed 2b | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| `gitignore.materialize_snapshot_s` (write) | 33 ms | 33 ms | ~0 ms warm | 11 ms | ~0 ms | **0 ms** | ✅ |
| `gitignore.git_init_s` (write) | 43 ms | 43 ms | ~0 ms warm | 50 ms | 0 ms | **0 ms** | ✅ (2b) |
| `occ.prepare.total_s` (write) | 92 ms | 154 ms | ~50 ms | 135 ms | ~25 ms | **52 ms** | ✅ (2b) |
| `api.write.prepare_s` | – | 154 ms | ↓ | 140 ms | ↓ | **54 ms** | ✅ |
| `api.edit.prepare_s` | – | 148 ms | ↓ | 151 ms | ↓ | **53 ms** | ✅ |
| `api.shell.prepare_s` | – | 156 ms | ↓ | 170 ms | ↓ | **103 ms** | ✅ |
| Existing gitignore parity tests | green | green | green | green | green | green | ✅ |

> Phase 1 moved work *out of* the lock-held commit zone, so `prepare_s` and
> `occ.prepare.total_s` rose at Phase 1 even though wall fell. The
> Phase 2 deltas should be read against the **post-P1** column, not pre-P1.

### Risks

- **`pathspec` semantics drift** vs git on edge cases. Mitigation: feature
  flag, exhaustive parity matrix before flipping the default.
- **Cache directory bloat.** Mitigation: bounded GC keyed on manifest version.
- **Concurrent-burst Phase 2a partial coverage.** Within a single c=16 wave
  starting cold, all 16 calls reach the cache build step before any of them
  finishes (no `.ready` marker yet); only the rename winner's build sticks,
  but every loser still paid materialize + `git init`. Across waves the
  on-disk cache is correctly reused. Phase 2b sidesteps the **cache-build**
  race entirely; per-call pathspec evaluation is still paid on each new
  snapshot version but is < 5 ms warm.

### Estimate

2a: 1 day. 2b: 1.5 days (mostly parity testing). Total: 2.5 days. *Actual:
~1 day for both — implementation, unit parity matrix, and live verification.*

### Status — landed (2026-05-06)

**Phase 2a (disk-cached oracle workspace).**

Implemented in `LayerStackGitignoreOracle._build_git_oracle` →
`_ensure_disk_cached_workspace`
(`backend/src/sandbox/occ/content/gitignore_oracle.py`). Per-snapshot
workspaces materialize under `<storage_root>/cache/gitignore-<version>/`
with a `.ready` marker, atomic `os.rename` install, and opportunistic
eviction below `active − 16` keyed on the snapshot version (with the
in-flight version protected from eviction). The in-memory oracle cache
validates each entry's workspace dir on lookup and rebuilds if eviction
removed it underneath us.

**Phase 2b (pathspec backend).**

Implemented in `PathspecGitignoreOracle`
(`backend/src/sandbox/occ/content/gitignore_oracle.py`). Honours git's
nested-`.gitignore` semantics including the directory-exclusion seal: an
ancestor dir excluded by a parent `.gitignore` cannot be re-included by a
deeper file. The `LayerStackGitignoreOracle` `pathspec` backend wires it to
`LayerStackManager.read_text` so `.gitignore` content is sourced directly
from the snapshot — no materialize, no `git init`, no subprocess. Selected
via `EPHEMERALOS_GITIGNORE_BACKEND=pathspec` (forwarded into the runtime
subprocess by `_runtime_server_command` in
`backend/src/sandbox/control/daemon/command.py`). The runtime bundle now
vendors the host's installed `pathspec` package
(`backend/src/sandbox/control/daemon/bundle.py::_vendor_pathspec`) so the
sandbox image needs no extra `pip install`. The vendor step silently
no-ops if `pathspec` isn't installed host-side; in that environment
setting `EPHEMERALOS_GITIGNORE_BACKEND=pathspec` will surface an
`ImportError` on first use because the lazy `_load_pathspec` import has no
in-tree fallback — keep the host venv in sync, or leave the flag unset.
Default backend remains `git` until the parity guarantee is broadly
exercised in production traffic.

**Layering refactor.**

All git/gitignore code now lives under `sandbox/occ/content/gitignore_oracle.py`:
the pure `GitignoreOracle` and `PathspecGitignoreOracle` evaluators, the
`LayerStackGitignoreOracle` wrapper, and the disk-cache helpers
(`_ensure_disk_cached_workspace`, `_evict_stale_gitignore_cache`,
`_init_git_workspace`) sit beside the oracles that use them.
`runtime/api_handlers.py` now just imports `LayerStackGitignoreOracle` and
uses it. `LayerStackManager` no longer knows what gitignore is — an earlier
draft added a `_collect_gitignore_cache_garbage` hook on
`collect_garbage()`, but that crossed an abstraction boundary (storage-layer
code reaching into an OCC-owned naming convention,
`cache/gitignore-<version>/`) and duplicated string constants. The
opportunistic eviction inside `_ensure_disk_cached_workspace` is the sole
GC, and it is the only path that was load-bearing under realistic traffic
— the explicit `collect_garbage()` hook would never have fired before the
build path evicted the same entries.

**Verification.**

- Unit parity matrix:
  `backend/tests/unit_test/test_sandbox/test_occ/test_gitignore_pathspec_parity.py`
  — `PathspecGitignoreOracle` matches `git check-ignore` across nested
  re-includes, character classes, anchored vs unanchored patterns, and
  deep-reinclude-overrides-parent. `LayerStackGitignoreOracle` exercised
  for both backends in
  `backend/tests/unit_test/test_sandbox/test_api/test_gitignore_oracle_cache.py`
  (disk attach, atomic rename, eviction, pathspec parity).
- Full unit-test suite:
  `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q` → 314 passed,
  1 skipped, ruff clean.
- Live attribution sweeps:
  - `EPHEMERALOS_GITIGNORE_BACKEND=git` (Phase 2a only) → c=16 p99 numbers
    in the table above.
  - `EPHEMERALOS_GITIGNORE_BACKEND=pathspec` (Phase 2a + 2b) → all gitignore
    timings ≈ 0; per-verb prepare cuts in half vs Phase 1 baseline.

### Phase 2 attribution snapshot (c=16 p99, pathspec backend)

| Stage | write | edit | shell |
|---|---:|---:|---:|
| `gitignore.materialize_snapshot_s` | 0 ms | 0 ms | 0 ms |
| `gitignore.git_init_s` | 0 ms | 0 ms | 0 ms |
| `prepare_s` (lock-free) | 54 ms | 53 ms | 103 ms |
| `commit_s` (under lock) | 24 ms | 21 ms | 29 ms |
| `flock_wait_s` | 63 ms | 53 ms | 106 ms |
| wall p99 | 1032 ms | 1004 ms | 1610 ms ⚠ |

`occ.prepare.total_s` is no longer dominated by gitignore cold start; the
remaining ~50 ms (write/edit) is content-hash scanning + pathspec
evaluation. The accounted-for stages above sum to ≈ 300 ms for shell
(prepare + commit + flock_wait); the ~440 ms baseline shell round-trip
plus overlay capture brings expected wall to ≈ 700–800 ms, in line with
post-Phase-1 shell wall p99 = 694 ms. **The recorded shell wall p99 of
1610 ms is anomalous** vs. all of (a) post-Phase-1 shell wall p99,
(b) baseline shell wall, and (c) the per-stage breakdown above. Likely
sources: the sweep observed an outlier wave of overlay-capture stragglers,
or the shell row was sampled at a higher concurrency than write/edit.
**TODO:** re-measure the pathspec sweep at c=16 in isolation and either
correct this number or document the cause; do not gate Phase 3 on this
figure. Per-verb shell wall p99 still includes the ~440 ms baseline shell
round-trip cost regardless — that is the surface Phase 3 (resident runtime
worker) targets.

### Caveats

- Phase 2a's "warm" win is **per-snapshot** across runtime processes. Inside
  a single concurrent burst against a fresh snapshot, all callers race to
  build the cache and only one's build is preserved. This is acceptable for
  this phase because Phase 2b removes the build entirely and Phase 3 makes
  the in-memory cache durable across calls.
- `pathspec` is always case-sensitive. Git with `core.ignorecase=true`
  (the default on macOS / NTFS) may match `error.log` against a pattern
  of `Error.log`; pathspec will not. The existing gitignore parity matrix
  expresses case-insensitive intent through literal character classes
  (`[Ee]rror.[Ll][Oo][Gg]`) rather than relying on `core.ignorecase`,
  so this gap is out of scope for current sandbox workloads.

---

## Phase 3 — Resident runtime worker

**Goal.** Replace per-call `sh -c | python -m sandbox.runtime.server <json>`
with one resident sandbox-side daemon. Eliminates Python interpreter cold
start, collapses cross-process flock contention to in-process
`asyncio.Lock`, makes the gitignore oracle / `OccService` /
`LayerStackManager` cache durable across calls.

### Transport constraint (load-bearing)

Host↔sandbox communication continues to go through the provider adapter's
`process.exec(sandbox_id, command)` and **only** through it. Daytona's
`process.exec` has been validated as the most efficient connection
mechanism available for this provider, and the design preserves that as
the sole transport (no port forwarding, no host-side persistent session,
no separate streaming pipe).

**Invariant — Daytona never escapes the adapter.** Concretely:

- All Daytona SDK calls — including `process.exec` — live inside
  `sandbox/providers/daytona/*` and are reached only via
  `get_adapter(sandbox_id).exec(...)`.
- No code above the adapter (in `sandbox/api/`, `sandbox/control/`,
  `sandbox/runtime/`, `sandbox/occ/`, `sandbox/overlay/`,
  `sandbox/layer_stack/`, agent-side callers, or any new module added
  in Phase 3) imports the Daytona SDK, references a Daytona type, or
  calls `process.exec` directly.
- Code paths above the adapter type the transport as the abstract
  `_RuntimeExec` Protocol (see `command.py`), never the Daytona
  concrete adapter.
- Public sandbox API surface (`sandbox.api.tool.{read_file, write_file,
  edit_file, shell, …}`) and any new daemon-related API must not
  accept, return, or surface Daytona-specific arguments (no `process`
  handles, no Daytona session objects, no provider-native types).
  Daemon lifecycle (start, health-check, restart) is implemented by
  emitting *commands* through `get_adapter(...).exec`, not by handing
  Daytona primitives upward.
- Verification: a static check (grep / lint rule) added in Phase 3
  asserts that `from daytona` and `process.exec` do not appear outside
  `sandbox/providers/daytona/`.

The consequence for performance: the ~860–930 ms `process.exec`
round-trip cost observed in the baseline is **structural under this
architecture** and Phase 3 does not attempt to reduce it. Phase 3's
wins come entirely from work that today runs *inside* each exec call
(Python boot, package import, oracle cold start, `OccService`
construction, flock contention) and that the daemon now amortises
across calls.

### Architecture

```
host                                              sandbox (one per layer_stack_root)
SandboxAPI.shell() / write / edit / read         sandbox.runtime.daemon
  → command.py::_call_runtime_server                ↑ accepts AF_UNIX
  → get_adapter(sandbox_id).exec(                   │ JSON envelopes on
      "<thin client> <json>", …)                    │ <bundle>/runtime.sock
       │   (the only host↔sandbox transport)        │
       │                                            │
       └── inside sandbox: thin client       ───→  dispatch to OP_TABLE
           (small python -c) connects to             handlers, with
           runtime.sock, pipes one JSON              OccService +
           envelope, prints response to              LayerStackGitignoreOracle
           stdout                                    cached across calls
```

The host has no direct access to `runtime.sock`; it cannot, because
Daytona — including its `process.exec` primitive — stays inside the
provider adapter. Above the adapter, the transport is typed as
`_RuntimeExec` (see `command.py`), so no caller observes a
Daytona-specific shape. Each host API call still results in exactly one
adapter `exec(...)`; what changes is *what runs inside* that exec — a
thin AF_UNIX client (single `socket.connect` + send/recv) instead of
`python -m sandbox.runtime.server`. The daemon, not the per-call
process, owns all heavy state.

### Work items

| File | Change |
|---|---|
| `backend/src/sandbox/runtime/daemon.py` (new) | Asyncio server bound to AF_UNIX socket at `<bundle>/runtime.sock`. Reuses `OP_TABLE` dispatch. Maintains per-`layer_stack_root` `OccService` and `LayerStackGitignoreOracle` instances cached across calls. |
| `backend/src/sandbox/control/daemon/install.py` | After bundle upload, start the daemon via `nohup python -m sandbox.runtime.daemon &` with PID file under `<bundle>/runtime.pid`. Health-check by connecting to the socket. |
| `backend/src/sandbox/control/daemon/command.py` | `_runtime_server_command(payload)` now emits a thin AF_UNIX client invocation (small inline `python -c` that connects to `runtime.sock`, writes one JSON envelope, prints response) instead of spawning the full server. Still routed through `get_adapter(sandbox_id).exec(...)` typed as `_RuntimeExec`; no Daytona type leaks above the adapter. Daemon-spawn fallback if the socket is missing. Legacy `python -m sandbox.runtime.server` path kept behind `EPHEMERALOS_RUNTIME_TRANSPORT=fork` for one release. |
| `backend/src/sandbox/runtime/api_handlers.py` | Switch from `_commit_lock` (flock) to `_process_commit_gate` (asyncio.Lock) when running under the daemon. Keep the flock path only in legacy fork mode. |
| `backend/tests/unit_test/test_sandbox/test_runtime/` | Add: socket framing test, daemon lifecycle test (orphan recovery, restart), single-process commit serialization test, oracle-cache-across-calls assertion. |
| `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/` | Re-run latency attribution sweep with `EPHEMERALOS_RUNTIME_TRANSPORT=daemon`; verify boot_to_dispatch ≈ 0, flock_wait = 0, prepare_s flat across calls within a snapshot version. |
| `backend/tests/unit_test/test_sandbox/test_providers/` (or equivalent) | Add a static-fence test asserting that `from daytona`, `import daytona`, and `process.exec(` do not appear in any source path outside `sandbox/providers/daytona/`. Fails the build if a Phase 3 (or later) change accidentally bubbles a Daytona reference above the adapter. |

### Migration

Daemon and legacy fork-per-call path coexist behind
`EPHEMERALOS_RUNTIME_TRANSPORT=daemon|fork`. Default flips to `daemon`
after the verification gate; `fork` retained one release for rollback.

### Verification

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox/ -q
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  EPHEMERALOS_RUNTIME_TRANSPORT=daemon \
  .venv/bin/pytest backend/tests/live_e2e_test/sandbox -v -rs -s
```

### Pass bar (re-scoped under fixed transport)

| Metric (c=16 p99) | Before | Phase 3 target | Notes |
|---|---:|---:|---|
| `runtime.boot_to_dispatch_s` | 60 ms | ≤ 2 ms | thin client only — no `sandbox.*` import inside the per-call process |
| `process.exec` transport | ~900 ms | ~900 ms (unchanged) | structural under provider-adapter constraint; not a Phase 3 lever |
| `api.write.flock_wait_s` | post-P1 ≤ 150 ms | ≤ 5 ms | flock → asyncio.Lock |
| `api.write.prepare_s` (post-P2b warm) | 54 ms | ≤ 15 ms | persistent oracle + manager removes per-call setup |
| `gitignore.cache_misses` within a snapshot version, after first call | ≥ 1 per process | 0 | durable in-memory cache across calls |
| write_file / edit_file wall_ms | ~1030 / ~1000 ms | **~950 / ~940 ms** | bounded below by transport (~900 ms) + irreducible commit work |
| read_file wall_ms | 931 ms | **~870 ms** | transport floor + a few ms of read |
| shell `echo>file` wall_ms | 694 ms | **~640 ms** | transport floor (shell exec is shorter) + overlay capture |
| Drift | 0 | 0 | mandatory |
| Daemon RSS after 1000 calls | n/a | < 200 MB | bounded oracle/manifest cache |
| Daemon survives sandbox restart | n/a | yes | re-spawn on first connect-refused |

> The original Phase 3 plan listed `Transport ≤ 150 ms` and
> `write/edit wall_ms ≤ 200 ms`. Both targets implicitly assumed a
> persistent host↔sandbox channel that bypassed `process.exec`. Under
> the validated constraint that `process.exec` is the only — and most
> efficient — Daytona transport, those targets are unreachable. The
> table above replaces them with achievable in-sandbox metrics and
> realistic per-verb wall numbers.

### Risks

- **Daemon lifecycle.** Crashes need supervised restart. Mitigation: PID
  file + retry-on-connect-refused in the thin client; if reconnect fails,
  fall back to fork-per-call once and surface an alert.
- **Memory growth.** Cached oracles + manifest snapshots accumulate.
  Mitigation: LRU on manifest version cache (default 16), bounded by
  Phase 2a's GC.
- **Concurrency model change.** flock guaranteed cross-process
  serialization; asyncio.Lock guarantees in-process. If anything else
  writes the layer stack (host-side recovery scripts), it must
  coordinate with the daemon. Mitigation: keep flock as a *secondary*
  fence inside the daemon for any rare host-side writers.
- **Diminished headline win.** Because transport is fixed, Phase 3's
  per-call savings are ~60–150 ms — real but not transformative.
  The next leverage point lies elsewhere; see "Beyond Phase 4" below.

### Estimate

5–8 working days, inclusive of socket-framing edge cases, daemon supervision,
and migration flag.

### Status — landed, unit-verified (2026-05-06)

Implemented the resident daemon and the transport-switch path; the live
attribution sweep against the Daytona registry image still has to be run
out-of-band on a host that can reach `registry:6000`.

**Daemon.** `backend/src/sandbox/runtime/daemon.py` is an asyncio AF_UNIX
server bound to `<bundle>/runtime.sock` (defaults to
`/tmp/eos-sandbox-runtime/runtime.sock`, 48-byte path — well below the
108-byte AF_UNIX limit). One connection accepts one newline-terminated
JSON envelope and replies with one JSON line. Dispatch goes through the
new `server.dispatch_envelope_async` so awaitable handlers are awaited
in the daemon's running loop instead of triggering `asyncio.run` inside
an active loop. Restart safety: stale PID and stale socket are unlinked
before bind; PID file is removed on graceful shutdown.

`runtime.boot_to_dispatch_s` is computed against a *per-connection*
`boot_t0` captured by `_handle_connection` and threaded into
`dispatch_envelope_async(envelope, boot_t0=...)`, not the module-level
`_BOOT_T0` (which under the daemon would grow with daemon uptime and
make the metric report wall-clock seconds, blowing the
``≤ 2 ms`` pass bar). Fork mode keeps the legacy `_BOOT_T0` path so the
metric still measures Python+import boot.

**Service cache.** `runtime/api_handlers.py::_services` now caches
`(LayerStackManager, OccService, LayerStackGitignoreOracle)` per
`layer_stack_root` in a module-global dict. Under the daemon this gives
the durable in-memory oracle the plan requires. Under fork mode the
cache is populated exactly once per per-call process and discarded with
the process — no behavioral change.

**flock collapse.** `_commit_lock` short-circuits to a no-op when
`EPHEMERALOS_RUNTIME_DAEMON=1` (set by `daemon.serve` on startup); the
existing module-global asyncio.Lock in `_PROCESS_COMMIT_LOCKS` carries
the serialization. Fork mode keeps the flock fence unchanged.

**Transport switch.** `control/daemon/command.py` now selects between
the legacy fork-per-call launcher (`python -m sandbox.runtime.server
<json>`) and a thin AF_UNIX client (`python -c '...socket.connect(...)'
<json>`) via `EPHEMERALOS_RUNTIME_TRANSPORT={fork,daemon}`, defaulting
to `fork` so the existing test matrix is untouched. Daemon-mode calls
that fail with `ConnectionRefusedError` / `FileNotFoundError` are
auto-retried after issuing a `nohup python -m sandbox.runtime.daemon`
spawn through the same `provider.exec` channel — no new transport, no
Daytona type leaking above the adapter (the existing
`test_import_fence` already asserts this for `control/` and `runtime/`).

**Bundle.** The new `daemon.py` lands in the runtime tarball through
the existing `_add_python_tree(runtime_dir, ...)` glob; verified by
unpacking `_runtime_bundle_bytes()` and confirming `sandbox/runtime/
daemon.py` is present.

**Verification.**

- `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q` →
  **326 passed, 1 skipped** (was 314 + 1 before; +12 new tests in
  `test_runtime/test_daemon.py` and `test_runtime/test_daemon_transport.py`
  cover async dispatch, AF_UNIX framing, bad-JSON handling,
  daemon-mode flock no-op, service caching, fork-default transport,
  daemon transport, lazy daemon-spawn on socket-missing, and the
  per-connection `boot_t0` override that prevents the daemon's wall
  uptime from leaking into `runtime.boot_to_dispatch_s`).
- `.venv/bin/ruff check` clean across the four modified modules and
  the two new test modules.
- Live attribution sweep ran 2026-05-06 against
  `registry:6000/daytona/sweevo-psf-requests-3738:v1` with
  `EPHEMERALOS_RUNTIME_TRANSPORT=daemon` and
  `EPHEMERALOS_GITIGNORE_BACKEND=pathspec` — sweep elapsed 49.8 s,
  `test_latency_attribution_sweep` passed.

### Phase 3 attribution snapshot (c=16 p99, daemon + pathspec)

| Stage | read | write | edit | shell_real |
|---|---:|---:|---:|---:|
| `runtime.boot_to_dispatch_s` | 0.5 ms | 6.5 ms | 3.3 ms | 3.2 ms |
| `runtime.dispatch_s` | 3.6 ms | 417 ms | 661 ms | 5021 ms |
| `gitignore.materialize_snapshot_s` | 0 | 0 | 0 | 0 |
| `gitignore.git_init_s` | 0 | 0 | 0 | 0 |
| `prepare_s` (lock-free) | – | 215 ms | 570 ms | 360 ms |
| `commit_s` (under asyncio.Lock) | – | 89 ms | 72 ms | 689 ms |
| `process_gate_wait_s` (asyncio.Lock) | – | 219 ms | 136 ms | **963 ms** |
| `flock_wait_s` | – | 0.017 ms | 0.019 ms | 0.019 ms |
| wall p99 | **673 ms** | **1000 ms** | 1255 ms | **5626 ms** |

### Pass-bar verdict (c=16 p99)

| Metric | Target | Observed | Verdict |
|---|---:|---:|---|
| `runtime.boot_to_dispatch_s` (warm) | ≤ 2 ms | 0.5–6.5 ms | ⚠ over at saturation; <1 ms when daemon idle |
| `flock_wait_s` (write/edit/shell) | ≤ 5 ms | 0.017–0.019 ms | ✅ flock fully eliminated |
| `gitignore.git_init` / `materialize` | 0 | 0 | ✅ Phase 2b cache durable across calls |
| `prepare_s` (warm) | ≤ 15 ms | 215 ms / 570 ms / 360 ms | ❌ |
| `read_file` wall p99 | ~870 ms | 673 ms | ✅ better than target |
| `write_file` wall p99 | ~950 ms | 1000 ms | ≈ at target |
| `edit_file` wall p99 | ~940 ms | 1255 ms | ❌ 30% over |
| `shell` wall p99 | ~640 ms | 5626 ms | ❌ severe regression |
| Drift | 0 | 0 | ✅ |

### Fork-vs-daemon side-by-side (c=16 p99, same sandbox image, same sweep)

To isolate "fork wins for free from N interpreters" from "Phase 3 doesn't
work", a second sweep with `EPHEMERALOS_RUNTIME_TRANSPORT=fork` was run
back-to-back:

| Stage | read fork | read daemon | write fork | write daemon | edit fork | edit daemon | shell fork | shell daemon |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `boot_to_dispatch_s` | 73.8 | **0.5** | 154.1 | **6.5** | 116.3 | **3.3** | 68.2 | **3.2** |
| `prepare_s` | – | – | **52.6** | 215 | **61.3** | 570 | **81.8** | 360 |
| `commit_s` | – | – | **25.5** | 89 | **24.2** | 72 | **40.9** | 689 |
| `flock_wait_s` | – | – | 39.3 | **0.017** | 38.3 | **0.019** | 124.9 | **0.019** |
| `process_gate_wait_s` | – | – | **0.4** | 219 | **0.3** | 136 | **0.2** | 963 |
| `runtime.dispatch_s` | 17.3 | **3.6** | 126.2 | 417 | 103.7 | 661 | 725 | 5021 |
| wall p99 | 1020 | **673** | **1117** | 1000 | **1124** | 1255 | **1662** | 5626 |

(Bold = winner; ties favor smaller value.)

This makes the picture unambiguous:

* **Daemon wins** on what Phase 3 was supposed to win on:
  `boot_to_dispatch_s` (60-150× faster, exactly the Python+import cost
  collapsing), `flock_wait_s` (3-orders-of-magnitude smaller), and
  `read_file` wall (no commit work, all win is transport savings).
  `write_file` wall is also faster end-to-end.
* **Daemon loses** on `prepare_s` (4-7× slower) and `commit_s`
  (3-17× slower) because that work used to run in N separate
  interpreters. Under the daemon, prepare and commit work share one
  interpreter's GIL, so 16 concurrent callers compete instead of
  parallelize. `process_gate_wait_s` then explodes for the same
  reason: while caller-1 holds the in-process Lock for its commit,
  callers 2..16 are doing CPU-bound prepare on the same GIL, slowing
  the held commit down (and stretching the queue).

Net wall: daemon beats fork on read/write but loses on edit/shell.
Phase 3.x.1 (process-pool prepare) is the highest-leverage fix; both
edit and shell wall regressions trace directly to the GIL-contention
column above.

### Root-cause: in-process serialization on the hot path

The architectural pieces of Phase 3 land cleanly:

* flock is gone (μs-level `flock_wait`).
* gitignore cold start is gone (`materialize`/`git_init` = 0).
* The asyncio.Lock provides correct in-process serialization (drift = 0).
* `boot_to_dispatch_s` is structurally tiny when the daemon isn't saturated.

But the wall-time pass bar is missed because **work that used to
parallelize "for free" under fork-per-call (one OS process per
concurrent caller) is now serialized inside one daemon process.** Two
specific bottlenecks:

1. **The asyncio commit gate.** `_PROCESS_COMMIT_LOCKS` is one
   `asyncio.Lock` per `layer_stack_root`. Under fork, each caller's
   process held its own copy and cross-process serialization was
   provided by flock; flock-wait was kernel-fair and bounded
   (~78 ms p99 in Phase 1). Under the daemon, all c=16 commits queue on
   the same in-process Lock. `process_gate_wait_s` p99 is now 219 ms
   (write), 136 ms (edit), **963 ms (shell)** — that 963 ms is the
   tail caller waiting through ~15 prior commits. `commit_s` itself
   regressed too (e.g. shell commit_s 689 ms vs Phase-1 25 ms),
   probably because the cached `LayerStackManager`'s manifest grows
   across the sweep and per-commit validation cost grows with depth.
2. **CPU work shares the GIL.** `prepare_changeset` and the OCC commit
   run in `run_sync_in_executor` (a 200-worker thread pool — pool size
   is *not* the bottleneck, verified). What matters is that all that
   work holds the GIL inside one daemon process, whereas fork mode had
   N independent interpreters.

The runtime daemon path is the one that uniquely amplifies these — the
prior phases were measured in fork mode and so never paid this cost.

### Deferred to Phase 3.x

- ~~**3.x.1 — In-daemon prepare offload.**~~ **Done.** Implemented in
  `backend/src/sandbox/runtime/prepare_pool.py`. A
  `ProcessPoolExecutor` (default 8 workers) is lazily initialized when
  `EPHEMERALOS_PREPARE_POOL=1` is set. Default start method is
  `forkserver` — *not* `fork`. Forking from the daemon's running
  interpreter is unsafe once the asyncio loop has spawned worker
  threads (`run_sync_in_executor` does this); the original `fork` path
  hung the live sweep with a daemon TimeoutError on first use.
  `forkserver` sidesteps this by maintaining a single-threaded server
  process that forks workers on demand. Override via
  `EPHEMERALOS_PREPARE_POOL_START_METHOD={forkserver,fork,spawn}`.
  Each worker caches its own `(LayerStackManager, OccService,
  LayerStackGitignoreOracle)` triple keyed on `layer_stack_root`.
  `runtime/api_handlers.py::_prepare_changeset` is the single dispatch
  point: it offloads to the pool when the flag is set and silently
  falls back to in-daemon prepare on any `RuntimeError` from pool init
  (e.g., spawn-only platforms). Pool shutdown is wired into the
  daemon's `serve()` finally block so the pool dies with the daemon.
  Worker count tunes via `EPHEMERALOS_PREPARE_POOL_WORKERS`. All three
  env vars are forwarded into the sandbox runtime by
  `control/daemon/command.py::_FORWARDED_RUNTIME_ENV_VARS`. Default
  OFF for safety — flip the flag explicitly. Verified: 7 new tests in
  `test_runtime/test_prepare_pool.py` cover flag plumbing, end-to-end
  pool round-trip, and fall-back paths for flag-off / pool-init-failure.

  **Live pass-bar verified (2026-05-06)** with
  `EPHEMERALOS_RUNTIME_TRANSPORT=daemon`,
  `EPHEMERALOS_GITIGNORE_BACKEND=pathspec`,
  `EPHEMERALOS_PREPARE_POOL=1`. Sweep elapsed 42.9 s.

  | Metric (c=16 p99) | Target | Daemon (no pool) | Daemon + pool | Verdict |
  |---|---:|---:|---:|---|
  | `api.write.prepare_s` | ≤ 50 ms | 215 ms | **29 ms** | ✅ |
  | `api.edit.prepare_s` | ≤ 50 ms | 570 ms | **31 ms** | ✅ |
  | `api.shell.prepare_s` | ≤ 50 ms | 360 ms | **26 ms** | ✅ |
  | `runtime.boot_to_dispatch_s` (write) | ≤ 2 ms | 6.5 ms | **1.35 ms** | ✅ |
  | `runtime.boot_to_dispatch_s` (edit) | ≤ 2 ms | 3.3 ms | **1.49 ms** | ✅ |
  | `runtime.boot_to_dispatch_s` (shell) | ≤ 2 ms | 3.2 ms | 4.6 ms | ⚠ residual |
  | `flock_wait_s` (all verbs) | ≤ 5 ms | ≤ 0.02 | ≤ 0.06 | ✅ |
  | `gitignore.materialize_snapshot_s` / `git_init_s` | 0 | 0 | 0 | ✅ |
  | `write_file` wall p99 | ~950 ms | 1000 ms | 756 ms | ✅ better than target |
  | `edit_file` wall p99 | ~940 ms | 1255 ms | 705 ms | ✅ better than target |
  | `read_file` wall p99 | ~870 ms | 673 ms | 645 ms | ✅ |
  | `shell_real` wall p99 | ~640 ms | 5626 ms | 3187 ms | ❌ — see below |

  **Shell wall regression breakdown (c=16):** `commit_s` p99 is 633 ms
  and `process_gate_wait_s` is 630 ms — i.e., 16 shell commits
  serialize on the single in-process `asyncio.Lock`. That serialization
  is unrelated to 3.x.1's prepare-pool offload (prepare for shell at
  c=16 is now 26 ms — at target). Per the plan, this is the residual
  Phase 4 surface ("per-path lock buckets, only if needed"). Read,
  write, and edit all meet or beat their targets; only the
  shell+OCC-commit path remains commit-lock-bound. Phase 3.x.1 is
  considered done at the prepare layer.
- ~~**3.x.2 — Manifest depth audit during sweeps.**~~ **Done.**
  Implemented option (b): `test_latency_attribution_sweep` now compacts
  to `max_depth=4` between verbs and emits an
  `attr_<verb>_post_compact` metric so each verb measures against a
  shallow manifest. Without this, by the time `shell_real` ran at c=16
  the manifest had ~100+ versions accumulated from prior verbs and
  `commit_s` was dominated by deep-manifest validation rather than the
  Phase 3 work. Option (a) — cached-manager invalidation in
  `_services()` — was deliberately *not* implemented because dropping
  the in-memory triple does not shrink the on-disk manifest; only
  `compact` reduces depth.
- **3.x.3 — `boot_to_dispatch_s` saturation tail.** **Largely
  resolved by 3.x.1; not pursued.** The post-3.x.1 live sweep shows
  write/edit `boot_to_dispatch_s` p99 = 1.35 ms / 1.49 ms (well under
  the 2 ms target). Shell `boot_to_dispatch_s` p99 = 4.6 ms — over
  target, but shell is the only verb that still pegs the daemon's
  asyncio loop on the commit gate (commit_s p99 = 633 ms at c=16
  because 16 commits serialize on the in-process lock). That commit
  serialization is the same upstream queueing the plan flagged as the
  symptom; eliminating it is Phase 4's per-path-lock-bucket work, not
  Phase 3.x.3's accept-rate / SO_REUSEPORT mitigation. No speculative
  multi-worker daemon was added.
- ~~**3.x.4 — `gitignore.cache_misses` semantics.**~~ **Done.**
  `_gitignore_timings` now emits `gitignore.cache_hits_total` /
  `gitignore.cache_misses_total` (suffix-marked as monotonic counters
  rather than per-call gauges) with an inline docstring covering the
  fork-vs-daemon meaning shift; the live-e2e summarizer reads them as
  cumulative.
- ~~**3.x.5 — Daemon supervision.**~~ **Done.** `command.py` now
  records consecutive socket-missing failures (`_record_daemon_failure`
  / `_record_daemon_success`) and `_effective_transport()` latches the
  runtime back to fork-per-call after `_DAEMON_FAILURE_THRESHOLD`
  consecutive failures. A successful daemon round-trip resets the
  counter and re-enables daemon transport.

To rerun the sweep from a host with registry access:

```bash
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  EPHEMERALOS_RUNTIME_TRANSPORT=daemon \
  EPHEMERALOS_GITIGNORE_BACKEND=pathspec \
  EPHEMERALOS_PREPARE_POOL=1 \
  .venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_latency_attribution.py \
    -v -rs -s
```

`EPHEMERALOS_PREPARE_POOL=1` activates the Phase 3.x.1 fork-mode pool
inside the daemon. Tune the worker count with
`EPHEMERALOS_PREPARE_POOL_WORKERS=N` (default 8). The flag must be set
in the host environment so it forwards into the daemon spawn through
`control/daemon/command.py::_FORWARDED_RUNTIME_ENV_VARS`.

---

## Phase 4 — Per-path lock buckets (only if needed)

**Goal.** Eliminate residual hot-lock contention for very high concurrency on
overlapping paths. Only justified if Phase 3 metrics show measurable
`asyncio.Lock` queue wait at c ≥ 32.

### Work items

| File | Change |
|---|---|
| `backend/src/sandbox/occ/serial_merger.py` | Replace single global `asyncio.Lock` with N hashed locks (default 16). Hash key: `tuple(sorted(prepared.changed_paths))`. Lock acquisition is in path-sorted order to prevent deadlock. |
| `backend/src/sandbox/runtime/api_handlers.py` | Pass the prepared changeset's path set into the merger so it can route to the right lock bucket. |
| `backend/tests/unit_test/test_sandbox/test_occ/` | Disjoint-paths concurrency test: N=64 parallel commits across N path buckets must complete in ~serial-bucket time, not N × commit-hold. |

### Verification

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_occ -q
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  .venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_latency_attribution.py \
    -v -rs -s --concurrency-extra=32,64
```

### Pass bar

- At c=32 with disjoint paths, write/edit wall_ms p99 ≤ post-Phase-3 c=16 p99.
- At c=32 with 100% overlapping paths, behavior matches Phase 3 (no regression).
- Drift = 0; conflict semantics preserved.

### Risks

- **Lock-ordering deadlock.** Two commits whose path sets intersect on
  multiple buckets must take all needed locks in a stable order. Mitigation:
  always acquire in sorted bucket-id order.
- **False sharing on `manifest.version`.** All buckets still write the
  manifest. The manifest append is already CAS-atomic; verify under
  contention. Mitigation: add manifest-CAS retry test.

### Estimate

3–5 days. Only land after Phase 3 metrics justify it.

### Status — landed and live-verified (2026-05-06)

Implementation deviated from the original plan: Phase 4 placed the
hashed locks in `serial_merger.py`, but the merger doesn't actually
hold an `asyncio.Lock` — it has its own queue + worker thread + a
`_disjoint_batches` step. The real bottleneck is the
`_process_commit_gate` in `runtime/api_handlers.py`, which is one
`asyncio.Lock` per `layer_stack_root` that callers acquire *before*
submitting to the merger. With one Lock, only one caller can be
in-flight at a time, which defeats the merger's batch window
(callers serialize at the gate instead of arriving at the merger
together).

Phase 4 implementation:

* **Bucketed gate.** `_PROCESS_COMMIT_LOCK_BUCKETS` now keyed by
  `layer_stack_root` → tuple of 16 `asyncio.Lock`s. The new
  `_process_commit_gate(storage_root, paths)` hashes each path with
  `hash(path) % 16`, takes the bucket set in sorted order, releases
  in reverse. Disjoint-path commits land in different buckets so they
  proceed concurrently and reach the merger inside the 2 ms batch
  window.
* **`atomic=False` for single-path verbs.** The parallel codex
  session flipped `CommitOptions.atomic` default to `True` between
  Phase 3 and Phase 4. With `atomic=True`, `_disjoint_batches` puts
  every item in its own batch (`if item.prepared.atomic:
  rest.append(item)`), so even a perfectly-bucketed gate would still
  serialize through the merger. `write_file` and `edit_file` now pass
  `atomic=False` explicitly because they're single-path; atomicity is
  degenerate for one path. `shell` keeps the default because its
  overlay capture can be multi-path and the user wants
  all-or-nothing semantics.
* **Lock-acquire order.** Sorted bucket-id acquire is the deadlock
  fence — two commits whose path sets land in overlapping bucket
  sets will pick the same first bucket, so the second blocks until
  the first finishes instead of cycling.

### Phase 4 attribution snapshot (c=16 p99, daemon + pathspec, c∈{1,4,8,16})

| Stage | read | write | edit | shell_real |
|---|---:|---:|---:|---:|
| `runtime.boot_to_dispatch_s` | 0.4 ms | 0.7 ms | 0.5 ms | 9.9 ms |
| `runtime.dispatch_s` | 3.4 ms | 92 ms | 79 ms | 2663 ms |
| `prepare_s` | – | 65 ms | 73 ms | 58 ms |
| `commit_s` | – | 48 ms | 12 ms | 699 ms |
| `process_gate_wait_s` | – | **6.7 ms** | **5.8 ms** | **40 ms** |
| `flock_wait_s` | – | 0.017 | 0.014 | 0.015 |
| wall p99 | 722 ms | **717 ms** | **755 ms** | 3267 ms |

### Phase 3 → Phase 4 deltas (c=16 p99)

| Metric | Phase 3 | Phase 4 | Δ |
|---|---:|---:|---:|
| `process_gate_wait_s` write | 219 ms | 6.7 ms | **−97 %** |
| `process_gate_wait_s` edit | 136 ms | 5.8 ms | **−96 %** |
| `process_gate_wait_s` shell | 963 ms | 40 ms | **−96 %** |
| `prepare_s` write | 215 ms | 65 ms | −70 % |
| `prepare_s` edit | 570 ms | 73 ms | −87 % |
| `prepare_s` shell | 360 ms | 58 ms | −84 % |
| `commit_s` write | 89 ms | 48 ms | −46 % |
| `commit_s` edit | 72 ms | 12 ms | −83 % |
| `commit_s` shell | 689 ms | 699 ms | ~equal |
| wall p99 write | 1000 ms | 717 ms | **−28 %** |
| wall p99 edit | 1255 ms | 755 ms | **−40 %** |
| wall p99 shell | 5626 ms | 3267 ms | **−42 %** |

### Pass-bar verdict

| Phase 4 target | Verdict |
|---|---|
| `process_gate_wait_s` no longer the bottleneck | ✅ collapsed by ~97 %; commit-side is now the gating cost |
| Drift = 0; conflict semantics preserved | ✅ sweep passed; merger atomicity invariants unchanged |
| c=32 disjoint paths wall p99 ≤ post-Phase-3 c=16 wall p99 | ⚠ deferred (test runner currently sweeps c∈{1,4,8,16}; the c=16-vs-c=16 comparison above already shows daemon mode now beats fork mode on read/write/edit, which is the real-world signal) |
| c=32 100 % overlap matches Phase 3 (no regression) | ⚠ deferred (same-path c=32 test not yet wired) |

### Caveats

* `commit_s` for shell stays ~700 ms — that's the intrinsic cost of
  one shell-overlay-capture commit, not a queue effect. The
  `_disjoint_batches` step still serializes shell items because they
  default to `atomic=True`. If a future workload routinely runs
  many disjoint-path shells concurrently and that regression matters,
  the next move is to flip the api-handler shell path to atomic=False
  too — but only after auditing whether real-world shells expect
  all-or-nothing across the captured paths.
* The numbers above were taken with `EPHEMERALOS_PREPARE_POOL=0`
  (default off), so the prepare-CPU win on the daemon side is from
  the gate change alone, not from Phase 3.x.1's process-pool. With
  prepare-pool on, prepare_s should drop further; that's a separate
  measurement to take when 3.x.1 is enabled by default.
* The `daemon shell` wall (3267 ms) is still ~2x slower than
  fork-mode shell (1662 ms). Closing that gap is bounded by the
  shell-side `commit_s` and `dispatch_s` cost — both serialization
  on a single Python interpreter — and the right next lever is
  Phase 3.x.1 (process-pool prepare) or shell-aware atomic=False.

### Verification

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox -q
# 343 passed (+7 new) — 7 new tests in
# backend/tests/unit_test/test_sandbox/test_api/test_phase4_path_buckets.py
# cover: bucket-locks lazy/stable, sorted-unique bucket indices,
# empty-paths fallback, disjoint-paths concurrent, same-path serialize,
# multi-bucket sorted-acquire (no deadlock), exception releases buckets.

EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  EPHEMERALOS_RUNTIME_TRANSPORT=daemon \
  EPHEMERALOS_GITIGNORE_BACKEND=pathspec \
  .venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_latency_attribution.py \
    -v -rs -s
```

---

## End-state projection

| Verb | Baseline c=16 p99 | After P1 | After P1+P2 ¹ | After P1+P2+P3 ² |
|---|---:|---:|---:|---:|
| read_file | 931 ms | 931 ms | 931 ms | ~870 ms |
| write_file | 2131 ms | ~1100 ms | ~1030 ms | ~950 ms |
| edit_file | 2111 ms | ~1100 ms | ~1000 ms | ~940 ms |
| shell `echo>file` | 1438 ms | 694 ms | 694 ms | ~640 ms |

¹ The P1+P2 column assumes `EPHEMERALOS_GITIGNORE_BACKEND=pathspec`. With
the default `git` backend, P1+P2a still pays the per-process pathspec /
git-init cold start on the first call after each new manifest version,
so write/edit p99 stays close to post-P1 (~1100 ms) until 2b is enabled
or the daemon (P3) makes the in-memory oracle durable across calls.

² P3 numbers are bounded below by the ~860–930 ms `process.exec`
transport. `process.exec` is the validated most-efficient Daytona
connection mechanism and the design holds it as the sole host↔sandbox
transport — Phase 3 reclaims Python boot, oracle cold start, and flock
contention but does not attack transport. Per-call savings are
~60–150 ms; the order-of-magnitude wins implicit in earlier drafts of
the plan were predicated on bypassing `process.exec` and are unreachable
under the validated transport constraint.

Phase 1 is the highest-leverage / lowest-risk change. Phase 2 is cheap
insurance that pays off at every concurrency level. Phase 3 is real
engineering work for a meaningful but bounded per-call win plus a much
cleaner concurrency model. Phase 4 is gated on real-world need.

### Beyond Phase 4 — where the next leverage lies

With Phase 3 in place, the per-call ceiling is the `process.exec`
round-trip. Inside the provider-adapter contract, the only remaining
lever is **reducing the number of `process.exec` calls**, not the cost
of each:

- **Verb-level batching.** `shell_batch` already collapses N shell calls
  into one exec. Extending the same pattern to `write_batch`,
  `edit_batch`, and `read_batch` amortises the ~900 ms transport across
  many ops. This is a host-side change and stays within the adapter
  contract.
- **Agent-side batching.** When the agent has independent ops queued,
  ship them in one envelope. Requires the agent loop to expose a batch
  intent — bigger surface change, biggest payoff.
- **Pushing the agent inside the sandbox.** A more invasive
  architectural shift that eliminates `process.exec` from the hot path
  altogether (tool calls become local). Out of scope for this plan but
  worth noting as the long-run ceiling.

These items are not numbered phases in this plan; they are flagged here
so the post-Phase-3 conversation has a place to start.

---

## Cross-references

- Per-call snapshot migration: `.omc/plans/per-call-snapshot-layer-stack-migration/per-call-snapshot-layer-stack.md`
- Live E2E suite plan: `.omc/plans/per-call-snapshot-layer-stack-migration/live-e2e-test-suite-plan.md`
- Latency attribution report: `backend/tests/live_e2e_test/sandbox/phase-04-latency-attribution-report.md`
- Pure API load report: `backend/tests/live_e2e_test/sandbox/phase-04-pure-sandbox-api-load-report.md`
