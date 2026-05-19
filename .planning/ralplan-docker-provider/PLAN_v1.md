# PLAN v1 — Docker as Default Sandbox Provider

**Mode:** Deliberate consensus (RALPLAN-DR)
**Status:** Draft for Architect/Critic review
**Scope guard:** Only `backend/src/sandbox/provider/*` and the three startup-bootstrap call sites change. `layer_stack/`, `daemon/`, `execution/`, `host/`, `tools/` are off-limits.

---

## 1. Principles

1. **Provider seam is the only seam.** All Docker-specific knowledge lives inside `sandbox.provider.docker.*`. `sandbox.host.*` and `sandbox.daemon.*` already call `adapter.exec(...)` / `call_daemon_api(...)` only (verified: `host/lifecycle.py:1-83`, `host/bootstrap.py:1-316` — zero `daytona_sdk` imports).
2. **Both providers coexist.** Daytona adapter is preserved unchanged; provider choice is a runtime env-var flip with no DB migration.
3. **Behavioral parity over verb parity.** Daytona "snapshots" and Docker "images" stay as named-by-string. The Protocol's `create(snapshot=..., image=...)` already accommodates both (`provider/protocol.py:33-42`); we do NOT unify verbs.
4. **Capability-minimum container.** Default Docker run grants only the caps needed for `unshare -Urm` + overlayfs `mount(2)`; the existing `COPY_BACKED` fallback (`execution/strategies/namespace.py:113-134`) is the safety net, not a co-equal mode.
5. **Existing duck-typed call paths are formalized, not rewritten.** `sandbox/api/_sandbox_control.py:92-99` already calls `getattr(adapter, "context_preparer", None)`; we promote it to a Protocol method so static analysis catches missing implementations.

## 2. Decision Drivers (top 3)

1. **Minimize Daytona–Docker behavioral divergence at the host↔daemon seam.** The layer-stack perf story (overlayfs syscall + 199+ layer cap) depends on `unshare -Urm` working identically inside both providers; capability granting is the load-bearing detail.
2. **Preserve overlay-based layer-stack performance.** Per project memory, `mount(2)` direct-syscall path in `execution/overlay/kernel_mount.py` is what unlocks the layer count; the Docker container must allow it. `COPY_BACKED` fallback is acceptable correctness but unacceptable as the default-performance mode.
3. **Safe rollback.** A single env-var flip (`EOS_SANDBOX_PROVIDER=daytona`) reverts to pre-change behavior. No data migration, no Protocol breakage for callers, no agent-profile changes.

## 3. Viable Options

### Axis A — Docker container capabilities (the central question)

| Option | Flags | Pros | Cons | Verdict |
|---|---|---|---|---|
| **A.1 `--privileged`** | `--privileged` | Trivially works; covers any future kernel-touching path | Grants ALL caps + device access; oversized blast radius if untrusted code ever runs | **Reject as default.** Keep as `EOS_DOCKER_PRIVILEGED=1` escape hatch only. |
| **A.2 `--cap-add=SYS_ADMIN --security-opt seccomp=unconfined --security-opt apparmor=unconfined`** | minimum surface for user-namespace + overlay mount | Smallest cap surface that unblocks `unshare -Urm` and `mount(2)`; pairs with the already-wired `COPY_BACKED` fallback for hosts that still reject it | Still grants SYS_ADMIN — review required if the runtime ever executes untrusted code | **RECOMMENDED default.** |
| **A.3 `--userns=host` / rootless docker** | rootless or shared userns | Best multi-tenant isolation | Overlay-on-overlay collisions inside Docker Desktop's Linux VM; unverified perf impact on layer-stack; UID mapping divergence vs native Linux complicates SWE-EVO image expectations | **Reject for default.** Document as future hardening track. |
| **A.4 No-cap, COPY_BACKED only** | default Docker run | Maximum isolation; no caps granted | Forfeits overlay-mount performance — `detect_private_mount_namespace()` returns `False`, every exec falls through to copy-backed strategy. Violates Driver #2. | **Reject as default.** Available as `EOS_DOCKER_NO_PRIVILEGE=1` for hostile-multi-tenant. |

**Recommended:** Option A.2.

### Axis B — Provider selection mechanism

| Option | Mechanism | Pros | Cons | Verdict |
|---|---|---|---|---|
| **B.1 `EOS_SANDBOX_PROVIDER` env var** | `docker` (default) \| `daytona` | Matches existing `DAYTONA_API_KEY`/`DAYTONA_API_URL` convention (`provider/daytona/client.py:70-72`); zero new deps; works identically from CLI, pytest fixtures, and process startup | Process-global only (acceptable — `set_default_provider` is already process-local) | **RECOMMENDED.** |
| **B.2 Config file (`.eos/config.toml`)** | TOML key | Centralizes config | New surface, new parser, conflicts with existing env-var conventions | Reject — no existing config-file infrastructure to extend. |
| **B.3 CLI flag** | `--provider docker` | Explicit at invocation | Three+ entrypoints need flag wiring; pytest fixtures lack a CLI | Reject — env var subsumes this; entrypoints can still set the env var inline. |

**Recommended:** Option B.1.

## 4. Pre-Mortem (3 failure scenarios)

### Scenario 1 — macOS Docker Desktop UID mapping breaks overlay writes
**Failure:** Container runs in Docker Desktop's Linux VM with non-default UID mapping; `unshare -Urm` succeeds but `mount(2)` overlay returns EPERM despite CAP_SYS_ADMIN, OR overlay mount succeeds but writes silently land in unexpected uid namespace.
**Detection signal:** `detect_private_mount_namespace()` (`execution/strategies/namespace.py:137-152`) returns `False` on container probe, OR a namespace-strategy exec returns exit code 125 with `control_ref.error_kind == "mount_failed"`.
**Mitigation:**
- `COPY_BACKED` fallback in `should_fall_back()` (namespace.py:113-134) is already wired and triggers automatically — correctness preserved.
- Add explicit macOS CI verification step (see §6 step 6); document expected `mount_mode` ratio.
- If macOS perf is unacceptable, ship the Daytona switch as the documented macOS workflow (`EOS_SANDBOX_PROVIDER=daytona` on local dev).

### Scenario 2 — Host seccomp/apparmor blocks `mount(2)` despite CAP_SYS_ADMIN
**Failure:** Some hosts (corp-managed Docker, hardened nodes) enforce a seccomp profile that denies `mount(2)` regardless of caps; namespace_child.py exits 125 on first exec.
**Detection signal:** `exit_code == NAMESPACE_INFRA_EXIT_CODE (125)` in `ShellProcessResult` + `mount_mode == PRIVATE_NAMESPACE` + `control_ref.error_kind == "mount_failed"`.
**Mitigation:**
- `--security-opt seccomp=unconfined --security-opt apparmor=unconfined` is **mandatory in default flag set**, not optional. Plan codifies these in `provider/docker/client.py` constants.
- `COPY_BACKED` fallback still rescues correctness if the host blocks even unconfined seccomp.
- Emit a startup-time warning when `detect_private_mount_namespace()` returns `False` inside the container so operators see it before runtime failures.

### Scenario 3 — Daytona regression during Protocol refactor
**Failure:** Promoting `context_preparer` from duck-typed (`api/_sandbox_control.py:95`) to a Protocol method, or refactoring the three bootstrap call sites, silently breaks the Daytona path (e.g., adapter import side effects, wrong env-var precedence, missing context-preparer registration for sandboxes minted before the env var was read).
**Detection signal:** Existing Daytona unit/integration tests (`backend/tests/unit_test/test_sandbox/**` and `backend/tests/unit_test/test_task_center/...`) fail when invoked with `EOS_SANDBOX_PROVIDER=daytona`.
**Mitigation:**
- Run **the full existing test suite twice** in CI: once with `EOS_SANDBOX_PROVIDER=daytona` (regression gate), once with `EOS_SANDBOX_PROVIDER=docker` (new path).
- Daytona adapter file (`provider/daytona/adapter.py`) is **frozen** in this plan — zero edits. Only `provider/daytona/bootstrap.py` is touched indirectly via the new dispatcher (the existing function stays callable for backward compat).
- Each plan step has a regression checkpoint; if Daytona tests regress, halt and revert that step before continuing.

## 5. Expanded Test Plan

### 5.1 Unit
- **`tests/unit_test/test_sandbox/test_provider/test_protocol_conformance.py`** (new): runtime-typecheck both `DaytonaProviderAdapter` and `DockerProviderAdapter` against `ProviderAdapter` Protocol (use `isinstance(x, ProviderAdapter)` via `runtime_checkable`, or attribute-presence assert if the Protocol stays plain). Assert `context_preparer`, `get_signed_preview_url`, `get_build_logs_url` are all present (the last two may return `None` for Docker — assert that's allowed).
- **`tests/unit_test/test_sandbox/test_provider/test_dispatcher.py`** (new): table-driven test of `bootstrap_sandbox_provider()` against env-var matrix: `docker` (default when unset), `daytona`, unknown value (raises clear error), case-insensitive.
- **`tests/unit_test/test_sandbox/test_provider/test_docker_adapter.py`** (new): mock the local Docker client; verify `create`, `start`, `stop`, `delete`, `exec` translate to the right Docker SDK calls; verify `get_signed_preview_url` / `get_build_logs_url` return `None` and don't raise.

### 5.2 Integration
- **`tests/integration_test/test_sandbox/test_docker_post_lifecycle.py`** (new, gated by `EOS_SANDBOX_PROVIDER=docker` and `EOS_HAVE_DOCKER=1` env vars): exercise the full `setup_post_lifecycle("create")` path against a real local Docker container — runtime bundle upload via `ensure_runtime_uploaded`, `ensure_git`, `ensure_workspace_base`, `runtime.ready` probe.
- **`tests/integration_test/test_sandbox/test_provider_parity.py`** (new): same `adapter.exec("pwd")`, `adapter.exec("git --version")` against both providers — asserts equal `exit_code` and equivalent stdout shape. Skip Daytona path when credentials missing.

### 5.3 E2E
- **`tests/integration_test/test_benchmarks/test_sweevo_docker_smoke.py`** (new, gated by `EOS_SANDBOX_PROVIDER=docker` + `EOS_HAVE_DOCKER=1`): runs **one** SWE-EVO instance end-to-end on Docker; asserts at least one `attempt`-strategy exec returns `mount_mode == PRIVATE_NAMESPACE` in `ShellProcessResult` (proves overlay path works), and `COPY_BACKED` does not occur unless `detect_private_mount_namespace()` reported unavailable at startup.
- **Daytona regression gate:** existing `tests/unit_test/test_benchmarks/test_sweevo_sandbox.py` (currently modified per `git status`) must pass unchanged under `EOS_SANDBOX_PROVIDER=daytona`.

### 5.4 Observability
- **Mount-strategy logging:** confirm `ShellProcessResult.mount_mode` (set at `namespace.py:110`) is already emitted to whatever shipping log destination the integration tests inspect. Add one log line at `host/lifecycle.py` post-exec if not present today — `logger.info("provider=%s mount_mode=%s exit=%d", provider.name, result.mount_mode, result.exit_code)`. **This is the only `host/*.py` edit permitted by this plan, and only if not already logged.**
- **Provider identity log at startup:** `bootstrap_sandbox_provider()` logs `INFO: sandbox provider = <name>` once on first call. Required so operators can confirm which provider is active without inspecting env.
- **Container-side capability probe:** `DockerProviderAdapter.create()` runs `detect_private_mount_namespace()` inside the newly created container as part of `setup_post_lifecycle` and logs the boolean. Critical breadcrumb for diagnosing Scenarios 1 and 2.

## 6. File-by-File Work Breakdown

### Step 1 — Protocol formalization (zero behavior change)
**Edit** `backend/src/sandbox/provider/protocol.py`:
- Add to `ProviderAdapter`:
  ```python
  def context_preparer(self, sandbox_id: str) -> Any: ...
  ```
- Update docstring to note this method returns a provider-specific context-preparer object (already returned by Daytona at `provider/daytona/adapter.py:352-356`).
- No edit to `sandbox/api/_sandbox_control.py:92-99` needed — `getattr(adapter, "context_preparer", None)` keeps working; the Protocol method just gives static typing a fixed point.

**Verify:** `python -c "from sandbox.provider.protocol import ProviderAdapter; from sandbox.provider.daytona.adapter import DaytonaProviderAdapter; assert isinstance(DaytonaProviderAdapter(), ProviderAdapter)"` (assuming `@runtime_checkable`; otherwise structural attribute check).

### Step 2 — Create `provider/docker/` package
**New files (all under `backend/src/sandbox/provider/docker/`):**

- `__init__.py` — exports `DockerProviderAdapter`, `bootstrap_docker_provider`.
- `client.py` — thin wrapper over `docker` Python SDK; holds tuned `docker run` flag constants:
  ```python
  DEFAULT_RUN_FLAGS = [
      "--cap-add=SYS_ADMIN",
      "--security-opt", "seccomp=unconfined",
      "--security-opt", "apparmor=unconfined",
  ]
  ```
  Plus env-var hooks for escape hatches (`EOS_DOCKER_PRIVILEGED`, `EOS_DOCKER_NO_PRIVILEGE`).
- `adapter.py` — `DockerProviderAdapter` implementing every method on the Protocol:
  - `name = "docker"`
  - `get_health()` → calls `docker info`-equivalent SDK call
  - `list_snapshots()` → `docker images` (mapped to the same `[{"name": ..., "image": ...}, ...]` shape the existing Daytona adapter returns)
  - `create(...)` → `docker create` + `docker start`; uses `image` argument; if `snapshot` is passed, treats it as image-tag alias (we don't unify the verbs, but Docker accepts either parameter so SWE-EVO can keep using "snapshot" semantically)
  - `start/stop/delete/get/list` → SDK calls
  - `set_labels` → `docker container update --label` (or recreate-on-immutable; spec out in implementation)
  - `get_signed_preview_url()` → return `{"url": None, "reason": "docker provider has no signed preview"}` — `benchmarks/sweevo/sandbox.py:310` already wraps `get_build_logs_url` in try/except so callers tolerate this
  - `get_build_logs_url()` → return `None`
  - `async exec(sandbox_id, command, ...)` → `docker exec` via SDK, returning a `RawExecResult` with the same `exit_code/stdout/stderr/success` shape Daytona's `_normalize_exec_response` produces (see `provider/daytona/adapter.py:331-348`)
  - `context_preparer(sandbox_id)` → returns a `DockerContextPreparer` instance
- `exec_context.py` — `DockerContextPreparer` mirroring `DaytonaContextPreparer`'s public surface (`prepare_context`, `prepare_context_async`); it must:
  - fetch the Docker container handle (sync + async)
  - call `prepare_sandbox_runtime_context(context, sandbox=..., workspace_root=...)` (the **same** helper `provider/daytona/exec_context.py:91-95` calls — confirm this helper is provider-neutral; if not, lift to `sandbox/host/`)
  - register `DockerProviderAdapter` for the sandbox via `register_adapter(sandbox_id, ...)` if not already registered (mirror `provider/daytona/exec_context.py:97-106`)
- `bootstrap.py` — `bootstrap_docker_provider()` that calls `set_default_provider(DockerProviderAdapter())`. Symmetric with `provider/daytona/bootstrap.py:15-17`.
- `workspace.py` (only if needed) — Docker-specific `discover_workspace` implementation; investigate whether the daytona one (`provider/daytona/workspace.py`) is reusable. If reusable, lift to `sandbox/host/workspace.py`. Decision deferred to implementation.

**Verify:** Step 1's runtime-Protocol assertion passes for `DockerProviderAdapter` too.

### Step 3 — Provider dispatcher
**New file** `backend/src/sandbox/provider/bootstrap.py`:
```python
def bootstrap_sandbox_provider() -> None:
    """Select provider from EOS_SANDBOX_PROVIDER env var; default 'docker'."""
    name = (os.environ.get("EOS_SANDBOX_PROVIDER") or "docker").strip().lower()
    if name == "docker":
        from sandbox.provider.docker.bootstrap import bootstrap_docker_provider
        bootstrap_docker_provider()
    elif name == "daytona":
        from sandbox.provider.daytona.bootstrap import bootstrap_daytona_provider
        bootstrap_daytona_provider()
    else:
        raise RuntimeError(f"Unknown EOS_SANDBOX_PROVIDER={name!r}; expected 'docker' or 'daytona'")
    logger.info("sandbox provider = %s", name)
```
- Idempotent: re-calls overwrite the default; safe because `set_default_provider` is already idempotent.
- Exported from `backend/src/sandbox/provider/__init__.py` alongside `set_default_provider`.

**Verify:** unit test in §5.1 (`test_dispatcher.py`) passes for all three env-var cases.

### Step 4 — Switch the three bootstrap call sites
Replace `bootstrap_daytona_provider()` with `bootstrap_sandbox_provider()` at:
1. `backend/src/task_center_runner/core/bootstrap.py:44-46` — the runtime startup hook called by CLI and pytest fixtures.
2. `backend/src/task_center_runner/benchmarks/sweevo/fixtures.py:81-87` — SWE-EVO pytest fixture.
3. `backend/src/benchmarks/sweevo/__main__.py:58-63` — SWE-EVO CLI entrypoint.

**Daytona's existing `bootstrap_daytona_provider()` stays callable** (do NOT delete it). The dispatcher just selects which bootstrap to call.

**Verify:** existing Daytona tests pass with `EOS_SANDBOX_PROVIDER=daytona` set (regression gate); Docker tests pass with the env unset (default) or set to `docker`.

### Step 5 — SWE-EVO snapshot-creation provider branch
**Edit** `backend/src/benchmarks/sweevo/sandbox.py:498-540`:
- Inside `register_sweevo_snapshot`, branch on `get_default_provider().name`:
  - `"daytona"` path: existing `subprocess.run(["daytona", "snapshot", "create", ...])` (unchanged).
  - `"docker"` path: `subprocess.run(["docker", "pull", image_ref])` then `subprocess.run(["docker", "tag", image_ref, name])`. Returns `name` (same contract).
- Add a `register_sweevo_snapshot_docker` private helper to keep the diff readable.
- Investigate `benchmarks/sweevo/__main__.py:59` (further references to the `daytona` CLI in the same file) — apply identical branching where present.

**Rationale for not putting this on the Protocol:** snapshot creation from a Dockerfile/image is a benchmark setup concern, not a runtime container primitive. Keeping it as a provider-name branch inside the benchmark preserves the Protocol's shape (constraint: "do NOT modify Protocol beyond `context_preparer`").

**Verify:** `EOS_SANDBOX_PROVIDER=docker python -m benchmarks.sweevo --instance-id <id>` proceeds past snapshot registration.

### Step 6 — macOS verification + capability-probe logging
- Add a one-time startup probe inside `DockerProviderAdapter.create()` (post-`docker start`): run `detect_private_mount_namespace()` script (`namespace.py:137-152`) inside the container via `adapter.exec`, log result.
- Document macOS expectations in `provider/docker/__init__.py` module docstring: "On macOS Docker Desktop, expect `mount_mode=COPY_BACKED` for some execs until VM-side seccomp tuning is confirmed; switch to `EOS_SANDBOX_PROVIDER=daytona` for local macOS dev if PRIVATE_NAMESPACE coverage is required."
- Add a manual smoke-test entry to `backend/scripts/` (e.g., `smoke_docker_provider.sh`): runs `EOS_SANDBOX_PROVIDER=docker python -m benchmarks.sweevo` on a known instance and grep-checks the log for `mount_mode=PRIVATE_NAMESPACE`.

**Verify:** the smoke script exits 0 on Linux dev host; exits with documented warning on macOS Docker Desktop if PRIVATE_NAMESPACE is unavailable.

## 7. Acceptance Criteria

1. **Protocol conformance (static):** `isinstance(DockerProviderAdapter(), ProviderAdapter)` and `isinstance(DaytonaProviderAdapter(), ProviderAdapter)` both return `True` under `@runtime_checkable`, OR an attribute-presence assertion passes for every method listed in `protocol.py:21-64` plus the new `context_preparer`.
2. **Daytona regression gate:** the full existing test suite runs under `EOS_SANDBOX_PROVIDER=daytona python -m pytest backend/tests/unit_test/test_sandbox/ backend/tests/unit_test/test_benchmarks/ backend/tests/unit_test/test_task_center/` with **zero new failures vs. main**.
3. **Docker end-to-end:** `EOS_SANDBOX_PROVIDER=docker python -m benchmarks.sweevo --instance-id <known-id> --no-register-snapshot` completes the full lifecycle — `setup_post_lifecycle` → `ensure_git` → `ensure_runtime_uploaded` → `ensure_workspace_base` returns `ready=True` → at least one strategy-`attempt` exec returns `ShellProcessResult.mount_mode == MountMode.PRIVATE_NAMESPACE` (proving overlay works under the granted caps).
4. **Provider identity:** the new dispatcher logs `sandbox provider = docker` (or `daytona`) exactly once at startup; visible in stderr/stdout for both CLI entrypoints and pytest runs.
5. **Build-logs URL tolerance:** `EOS_SANDBOX_PROVIDER=docker` SWE-EVO run does not raise from `benchmarks/sweevo/sandbox.py:310` despite `get_build_logs_url()` returning `None` (already wrapped in try/except — verify path is exercised).
6. **No host/daemon edits beyond observability:** `git diff main --stat sandbox/host/ sandbox/daemon/ sandbox/execution/ sandbox/layer_stack/` shows at most one new log line in `host/lifecycle.py`. No other lines change.
7. **Documentation:** `provider/docker/__init__.py` docstring documents the `EOS_SANDBOX_PROVIDER`, `EOS_DOCKER_PRIVILEGED`, `EOS_DOCKER_NO_PRIVILEGE` env vars and the macOS caveat.

## 8. Rollback Plan

**Single-knob revert:** set `EOS_SANDBOX_PROVIDER=daytona` in the environment, no code change needed.

**Step-level rollback during implementation:**
- Each step (1, 2, 3, 4, 5, 6) lands as its own commit with the regression gate (criterion #2) as a precondition.
- Step 1 (Protocol method add) is safe to ship independently — adds a method already present on the live adapter.
- If Step 4 (call-site switch) regresses Daytona tests, revert that commit; Steps 1–3 are still useful prep.
- If Step 5 regresses SWE-EVO under Daytona, revert; Step 4 still works because the new dispatcher routes back to `bootstrap_daytona_provider()`.
- **No DB migration. No agent-profile change. No state file format change.** Rollback is `git revert <commit> && unset EOS_SANDBOX_PROVIDER`.

## 9. Out of Scope (explicit non-goals)

- **Do NOT** unify Daytona "snapshot" and Docker "image" verbs at the Protocol level. The Protocol's `create(snapshot=..., image=...)` already accepts both (`provider/protocol.py:33-42`); leave it asymmetric.
- **Do NOT** change anything in `sandbox/layer_stack/`, `sandbox/daemon/`, `sandbox/execution/` (except for the optional one-line log addition in `host/lifecycle.py` per §5.4).
- **Do NOT** introduce platform-specific code (macOS branches, Linux branches) into `sandbox.host.*` or `sandbox.daemon.*`. All platform handling lives inside `sandbox.provider.docker.*`.
- **Do NOT** delete `provider/daytona/bootstrap.py` or rename the Daytona adapter. Both providers remain first-class.
- **Do NOT** change `task_center_runner/core/bootstrap.py`'s agent-registry loading; only its provider-bootstrap line.
- **Do NOT** add a config-file mechanism for provider selection in this plan. Env-var only (Option B.1).
- **Do NOT** modify the `_BOOTSTRAPPED` sentinel pattern in `task_center_runner/core/bootstrap.py:16,68`. The dispatcher is idempotent the same way.
- **Do NOT** alter SWE-EVO image content, Dockerfile, or daemon scripts. Docker provider consumes whatever image SWE-EVO supplies, with the same daemon assets uploaded by `ensure_runtime_uploaded`.

---

## ADR (decision record for archival)

**Decision:** Make Docker the default `ProviderAdapter` while keeping Daytona as an `EOS_SANDBOX_PROVIDER=daytona`-selectable alternative.

**Drivers:**
1. Minimize Daytona–Docker behavioral divergence at the host↔daemon seam.
2. Preserve overlay-based layer-stack performance.
3. Safe rollback via single env-var flip.

**Alternatives considered:**
- Docker with `--privileged` (Option A.1): rejected — oversized blast radius.
- Docker with `--userns=host`/rootless (Option A.3): rejected — overlay-on-overlay risk in Docker Desktop VM.
- Docker with no caps, COPY_BACKED-only (Option A.4): rejected — forfeits layer-stack perf.
- Config-file provider selection (B.2): rejected — no existing config infrastructure.
- CLI-flag provider selection (B.3): rejected — env-var subsumes; multiple entrypoints simplify.
- Adding snapshot-creation to the Protocol: rejected — benchmark-local concern, keeps Protocol minimal.

**Why chosen:**
- Option A.2 is the minimum-cap surface that unblocks `unshare -Urm` + overlayfs `mount(2)`; pairs with the already-wired `COPY_BACKED` fallback for hosts that reject SYS_ADMIN.
- Option B.1 matches existing `DAYTONA_*` env-var conventions; works identically from CLI, pytest, and process startup.
- Promoting `context_preparer` to the Protocol formalizes existing duck-typed behavior (`api/_sandbox_control.py:95`) with zero caller change.

**Consequences:**
- New maintenance surface: `provider/docker/` package + Docker Python SDK dependency.
- macOS Docker Desktop may show degraded `mount_mode=COPY_BACKED` ratio until VM-side seccomp tuning is confirmed; documented workaround is `EOS_SANDBOX_PROVIDER=daytona`.
- SWE-EVO snapshot registration acquires a provider-branch in `benchmarks/sweevo/sandbox.py`; future provider additions must update this branch (acceptable — single benchmark file).
- Operators must learn the new env var; mitigation: startup log line announces active provider.

**Follow-ups:**
- Track macOS PRIVATE_NAMESPACE coverage; if persistently zero, investigate Docker Desktop VM seccomp profile tuning or formal rootless mode (revisit Option A.3).
- Investigate whether `provider/daytona/workspace.py:prepare_sandbox_runtime_context` is genuinely provider-neutral; if so, lift it to `sandbox/host/workspace.py` in a follow-up cleanup.
- Add Docker provider perf benchmarks parallel to existing Daytona perf tests (`backend/tests/unit_test/test_sandbox/...`).
- Consider promoting the Docker `register_sweevo_snapshot` branch to a separate `BenchmarkSnapshotRegistrar` Protocol in a future cleanup, if a third provider is added.
