# PLAN v3 — Docker as Default Sandbox Provider

**Mode:** Deliberate consensus (RALPLAN-DR)
**Status:** Revised after ARCHITECT_REVIEW_v2.md (applies the one blocking fix + 4 cheap optional fixes)
**Scope guard:** Only `backend/src/sandbox/provider/*` and the three startup-bootstrap call sites change. `layer_stack/`, `daemon/`, `execution/`, `host/`, `tools/` are off-limits.

---

## 1. Principles

1. **Provider seam is the only seam.** All Docker-specific knowledge lives inside `sandbox.provider.docker.*`. `sandbox.host.*` and `sandbox.daemon.*` already call `adapter.exec(...)` / `call_daemon_api(...)` only (verified: `host/lifecycle.py:1-83`, `host/bootstrap.py:1-316` — zero `daytona_sdk` imports).
2. **Both providers coexist.** Daytona adapter is preserved unchanged; provider choice is a **process-startup env-var flip** with no DB migration. (Updated from v1's "runtime env-var flip" — see §8 for the `_BOOTSTRAPPED` sentinel constraint.)
3. **Behavioral parity over verb parity.** Daytona "snapshots" and Docker "images" stay as named-by-string. The Protocol's `create(snapshot=..., image=...)` already accommodates both (`provider/protocol.py:33-42`); we do NOT unify verbs.
4. **Capability-minimum container.** Default Docker run grants only the caps needed for `unshare -Urm` + overlayfs `mount(2)`; the existing `COPY_BACKED` fallback (`execution/strategies/namespace.py:113-134`) is the safety net, not a co-equal mode.
5. **Existing duck-typed call paths are formalized, not rewritten.** `sandbox/api/_sandbox_control.py:92-99` already calls `getattr(adapter, "context_preparer", None)`; we promote it to a Protocol method so static analysis catches missing implementations.

## 2. Decision Drivers (top 3)

1. **Minimize Daytona–Docker behavioral divergence at the host↔daemon seam.** The layer-stack perf story (overlayfs syscall + 199+ layer cap) depends on `unshare -Urm` working identically inside both providers; capability granting is the load-bearing detail.
2. **Preserve overlay-based layer-stack performance.** Per project memory, `mount(2)` direct-syscall path in `execution/overlay/kernel_mount.py` is what unlocks the layer count; the Docker container must allow it. `COPY_BACKED` fallback is acceptable correctness but unacceptable as the default-performance mode.
3. **Safe rollback.** A single env-var flip (`EOS_SANDBOX_PROVIDER=daytona`) plus process restart reverts to pre-change behavior. No data migration, no Protocol breakage for callers, no agent-profile changes.

## 3. Viable Options

### Axis A — Docker container capabilities (the central question)

| Option | Flags | Pros | Cons | Verdict |
|---|---|---|---|---|
| **A.1 `--privileged`** | `--privileged` | Trivially works; covers any future kernel-touching path | Grants ALL caps + device access; oversized blast radius if untrusted code ever runs | **Reject as default.** Keep as `EOS_DOCKER_PRIVILEGED=1` escape hatch only. |
| **A.2 `--cap-add=SYS_ADMIN --security-opt seccomp=unconfined --security-opt apparmor=unconfined`** | minimum surface for user-namespace + overlay mount | Smallest cap surface that unblocks `unshare -Urm` and `mount(2)`; pairs with the already-wired `COPY_BACKED` fallback for hosts that still reject it | Still grants SYS_ADMIN — review required if the runtime ever executes untrusted code. **Sufficiency is verified by §6 Step 0 against the live `mount(8)` code path (`execution/overlay/kernel_mount.py:44-50`), which is what the runtime uses today.** Direct `mount(2)` syscall coverage (the 199-layer regime documented in `~/.claude/projects/.../memory/overlay_depth_cap_root_cause.md`) is NOT in scope for this gate — it would require migrating `kernel_mount.py` to ctypes/libc, a follow-up tracked in ADR §Follow-ups. | **RECOMMENDED default, conditional on Step 0 pre-flight passing.** |
| **A.3 `--userns=host` / rootless docker** | rootless or shared userns | Best multi-tenant isolation | Overlay-on-overlay collisions inside Docker Desktop's Linux VM; unverified perf impact on layer-stack; UID mapping divergence vs native Linux complicates SWE-EVO image expectations | **Reject for default.** Document as future hardening track. |
| **A.4 No-cap, COPY_BACKED only** | default Docker run | Maximum isolation; no caps granted | Forfeits overlay-mount performance — `detect_private_mount_namespace()` returns `False`, every exec falls through to copy-backed strategy. Violates Driver #2. | **Reject as default.** Available as `EOS_DOCKER_NO_PRIVILEGE=1` for hostile-multi-tenant. |

**Recommended:** Option A.2 — conditional on the §6 Step 0 pre-flight CI experiment proving sufficiency for the layer-stack hottest paths on a Linux CI host. If the pre-flight reveals A.2 is insufficient (e.g., `mount_mode == COPY_BACKED` ratio falls below the §7.3 threshold for the smoke run), the plan says **"demote A.2; expand cap set or escalate to A.1"** — re-trigger consensus loop with the demoted-A.2 finding documented.

### Axis B — Provider selection mechanism

| Option | Mechanism | Pros | Cons | Verdict |
|---|---|---|---|---|
| **B.1 `EOS_SANDBOX_PROVIDER` env var** | `docker` (default on linux) \| `daytona` (default on darwin) \| explicit override on either platform | Matches existing `DAYTONA_API_KEY`/`DAYTONA_API_URL` convention (`provider/daytona/client.py:70-72`); zero new deps; works identically from CLI, pytest fixtures, and process startup | Process-global only (acceptable — see §8 rollback constraint) | **RECOMMENDED.** |
| **B.2 Config file (`.eos/config.toml`)** | TOML key | Centralizes config | New surface, new parser, conflicts with existing env-var conventions | Reject — no existing config-file infrastructure to extend. |
| **B.3 CLI flag** | `--provider docker` | Explicit at invocation | Three+ entrypoints need flag wiring; pytest fixtures lack a CLI | Reject — env var subsumes this; entrypoints can still set the env var inline. |

**Recommended:** Option B.1, with **per-platform defaults** when `EOS_SANDBOX_PROVIDER` is unset:

```python
if sys.platform == "darwin":
    default = "daytona"
elif sys.platform.startswith("linux"):
    default = "docker"
else:
    raise RuntimeError(f"unsupported platform {sys.platform}")
```

**Env-var precedence (authoritative rule):** `EOS_SANDBOX_PROVIDER` is the single source of truth for provider selection. Presence of `DAYTONA_API_KEY` does **NOT** auto-select Daytona. If `EOS_SANDBOX_PROVIDER=docker` AND `DAYTONA_API_KEY` is set, the dispatcher logs **once at startup**: `INFO: Daytona credentials detected but provider=docker; ignoring DAYTONA_*`. This prevents the common-dev-workflow footgun where a developer has `DAYTONA_API_KEY` in `.env` from previous work and gets surprised by silent provider swap when shell env adds `EOS_SANDBOX_PROVIDER=docker`.

## 4. Pre-Mortem (3 failure scenarios)

### Scenario 1 — macOS Docker Desktop UID mapping breaks overlay writes
**Failure:** Container runs in Docker Desktop's Linux VM with non-default UID mapping; `unshare -Urm` succeeds but `mount(2)` overlay returns EPERM despite CAP_SYS_ADMIN, OR overlay mount succeeds but writes silently land in unexpected uid namespace.
**Detection signal:** `detect_private_mount_namespace()` (`execution/strategies/namespace.py:137-152`) returns `False` on container probe, OR a namespace-strategy exec returns exit code 125 with `control_ref.error_kind == "mount_failed"`.
**Mitigation:**
- **Primary:** dispatcher defaults to `daytona` on `darwin` (§3 Axis B). macOS Docker Desktop is not the supported default configuration.
- `COPY_BACKED` fallback in `should_fall_back()` (namespace.py:113-134) is still wired and triggers automatically if a user explicitly sets `EOS_SANDBOX_PROVIDER=docker` on darwin — correctness preserved.
- macOS-Docker is documented in `provider/docker/__init__.py` as "unsupported as default; use Daytona on darwin or set EOS_SANDBOX_PROVIDER=docker explicitly." (See §6 Step 6.)

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
- **`tests/unit_test/test_sandbox/test_provider/test_protocol_conformance.py`** (new): runtime-typecheck both `DaytonaProviderAdapter` and `DockerProviderAdapter` against `ProviderAdapter` Protocol (use `isinstance(x, ProviderAdapter)` via `runtime_checkable`, or attribute-presence assert if the Protocol stays plain). Assert `context_preparer`, `get_signed_preview_url`, `get_build_logs_url` are all present (the last two may return `None`-shaped dicts for Docker — assert that's allowed).
- **`tests/unit_test/test_sandbox/test_provider/test_dispatcher.py`** (new): table-driven test of `bootstrap_sandbox_provider()` against env-var matrix:
  - `EOS_SANDBOX_PROVIDER=docker` → DockerProviderAdapter selected.
  - `EOS_SANDBOX_PROVIDER=daytona` → DaytonaProviderAdapter selected.
  - `EOS_SANDBOX_PROVIDER` unset on `sys.platform == "linux"` → DockerProviderAdapter selected.
  - `EOS_SANDBOX_PROVIDER` unset on `sys.platform == "darwin"` → DaytonaProviderAdapter selected.
  - `EOS_SANDBOX_PROVIDER` unset on unsupported platform → raises clear error.
  - `EOS_SANDBOX_PROVIDER=DOCKER` (uppercase) / `Docker` (mixed) → case-insensitive accept.
  - `EOS_SANDBOX_PROVIDER=foo` (unknown) → raises clear error.
  - **Second call with same env value** → no-op, no warning (sentinel-gated; see §6 Step 3).
  - **Second call with different env value** → no-op + WARNING log `bootstrap_sandbox_provider called twice with different EOS_SANDBOX_PROVIDER` (sentinel-gated; see §6 Step 3).
  - `EOS_SANDBOX_PROVIDER=docker` + `DAYTONA_API_KEY` set → DockerProviderAdapter selected + one INFO log `Daytona credentials detected but provider=docker; ignoring DAYTONA_*`.
- **`tests/unit_test/test_sandbox/test_provider/test_docker_adapter.py`** (new): mock the local Docker client; verify `create`, `start`, `stop`, `delete`, `exec` translate to the right Docker SDK calls. Additionally:
  - Assert `DockerProviderAdapter().get_signed_preview_url(sandbox_id, port)` returns shape `{"url": None, "reason": str}` and does NOT raise.
  - Assert `DockerProviderAdapter().get_build_logs_url(sandbox_id)` returns `None` (or the same `{"url": None, "reason": str}` shape — pick one; plan locks in `None` per §6 Step 2 list) and does NOT raise.

### 5.2 Integration
- **`tests/integration_test/test_sandbox/test_docker_post_lifecycle.py`** (new, gated by `EOS_SANDBOX_PROVIDER=docker` and `EOS_HAVE_DOCKER=1` env vars): exercise the full `setup_post_lifecycle("create")` path against a real local Docker container — runtime bundle upload via `ensure_runtime_uploaded`, `ensure_git`, `ensure_workspace_base`, `runtime.ready` probe.
- **`tests/integration_test/test_sandbox/test_provider_parity.py`** (new): same `adapter.exec("pwd")`, `adapter.exec("git --version")` against both providers — asserts equal `exit_code` and equivalent stdout shape. Skip Daytona path when credentials missing.

### 5.3 E2E
- **`tests/integration_test/test_benchmarks/test_sweevo_docker_smoke.py`** (new, gated by `EOS_SANDBOX_PROVIDER=docker` + `EOS_HAVE_DOCKER=1`): runs **one** SWE-EVO instance end-to-end on Docker.
  - **Acceptance bar (a, mount-mode coverage):** **≥95% of `attempt`-strategy execs** in the smoke run report `ShellProcessResult.mount_mode == MountMode.PRIVATE_NAMESPACE` on a **Linux CI host**. Strategy classification: count any exec dispatched through `execution/strategies/namespace.py` `Strategy.NAMESPACE` (i.e., not eagerly-`COPY_BACKED`-fallback). Test asserts:
    ```python
    namespace_execs = [r for r in results if r.strategy == Strategy.NAMESPACE]
    private_ns = [r for r in namespace_execs if r.mount_mode == MountMode.PRIVATE_NAMESPACE]
    assert len(private_ns) / len(namespace_execs) >= 0.95
    ```
  - **Acceptance bar (b, perf delta):** **p95 exec latency within ±25% of Daytona baseline** on the same SWE-EVO instance. See "Daytona baseline measurement procedure" below.
- **Daytona baseline measurement procedure (one-time setup for §5.3):**
  1. Run the same SWE-EVO instance under `EOS_SANDBOX_PROVIDER=daytona` on the same CI host class (or the closest equivalent — Daytona's perf is partly host-independent because work runs in remote VMs; document this caveat in the test's baseline-data file).
  2. Collect per-exec elapsed-time from `ShellProcessResult` or `host/lifecycle.py` log line over a full run (~200 execs minimum).
  3. Persist `p50_ms, p95_ms, p99_ms` to `tests/integration_test/test_benchmarks/data/daytona_baseline_p95.json` checked into the repo.
  4. Re-baseline annually or when Daytona or SWE-EVO is upgraded; document re-baseline procedure inline.
  5. Docker smoke test reads this file and asserts `docker_p95_ms <= daytona_p95_ms * 1.25`. On failure, fail loud with both numbers in the assertion message.

  > **±25% rationale:** intentionally loose. Daytona runs in a remote VM (network round-trip per exec); Docker runs locally. The gate exists to catch gross regressions (e.g., 2× slowdown from a missing `--security-opt seccomp=unconfined`), not to enforce parity. **A future maintainer SHOULD NOT tighten this to ±10% without first switching to a platform-relative baseline.**

  6. **Test fails loud if baseline is stale:** test asserts `os.path.getmtime("daytona_baseline_p95.json")` is within 180 days of `time.time()`, raising `AssertionError(f"daytona_baseline_p95.json is N days old; re-baseline required")` otherwise. Prevents silent baseline drift.
- **Daytona regression gate:** existing `tests/unit_test/test_benchmarks/test_sweevo_sandbox.py` (currently modified per `git status`) must pass unchanged under `EOS_SANDBOX_PROVIDER=daytona`.

### 5.4 Observability
- **Mount-strategy logging:** confirm `ShellProcessResult.mount_mode` (set at `namespace.py:110`) is already emitted to whatever shipping log destination the integration tests inspect. Add one log line at `host/lifecycle.py` post-exec if not present today — `logger.info("provider=%s mount_mode=%s exit=%d", provider.name, result.mount_mode, result.exit_code)`. **This is the only `host/*.py` edit permitted by this plan, and only if not already logged.**
- **Provider identity log at startup:** `bootstrap_sandbox_provider()` logs `INFO: sandbox provider = <name>` once on first call. Required so operators can confirm which provider is active without inspecting env.
- **Daytona-credentials-with-docker-provider log:** dispatcher emits `INFO: Daytona credentials detected but provider=docker; ignoring DAYTONA_*` exactly once when this condition holds at first bootstrap.
- **Container-side capability probe:** `DockerProviderAdapter.create()` runs `detect_private_mount_namespace()` inside the newly created container as part of `setup_post_lifecycle` and logs the boolean. Critical breadcrumb for diagnosing Scenarios 1 and 2.

## 6. File-by-File Work Breakdown

### Step 0 — Pre-flight CI capability experiment (NEW; gating Step 1+)
**Purpose:** Empirically prove Option A.2 (`--cap-add=SYS_ADMIN --security-opt seccomp=unconfined --security-opt apparmor=unconfined`) is sufficient for the layer-stack's full overlay+OCC code path on a Linux CI host **before** the rest of the plan lands.

**Action:**
- New script `backend/scripts/preflight_docker_a2_caps.sh` that:
  1. Builds (or pulls) the existing SWE-EVO base image.
  2. Runs the container with the A.2 flag set (NOT `--privileged`).
  3. Run the actual overlay-mount code path the runtime uses today —
     exec `subprocess.run(["mount", "-t", "overlay", ...])` (the same call
     pattern as `execution/overlay/kernel_mount.py:44-50`, which the module
     docstring confirms is mount(8) / mount(8), NOT mount(2) syscall) inside
     the container at progressively higher overlay depths: 1, 5, 10, 15, 16.
     Document the highest depth at which the mount succeeds.
     - Expected: per project memory (overlay_depth_cap_root_cause.md),
       util-linux mount(8) returns rc=32 at depths >~10-16 (the rc=32 cliff).
       Anything ≥10 is acceptable for the current default layer-stack squash
       policy (depth-≤14 hybrid).
  4. Reports per-step pass/fail with clear log lines.
- New CI job (label: `docker-a2-preflight`) runs this script on the Linux CI host matrix; the rest of this plan does NOT land until this job is green.

**Decision rule:**
- **All preflight steps pass AND mount(8) succeeds at depth ≥10** → A.2 is the default; proceed to Step 1.
- **`unshare -Urm` fails** → A.2 insufficient; demote to opt-in, document required cap delta, halt and re-trigger consensus loop.
- **`detect_private_mount_namespace()` returns False** → same demotion + halt.
- **Mount succeeds at depth ≤5 but fails at higher depths** → expected mount(8) behavior; not a blocker, but flag in §7.3 perf-delta tolerance.

**On halt:** commit `preflight_docker_a2_caps.log` to the repo under `.planning/ralplan-docker-provider/preflight-logs/` and surface to the consensus reviewer. Do NOT proceed to Step 1.

**Verify:** CI job green on Linux runner; results captured in `backend/scripts/preflight_docker_a2_caps.log` artifact for the consensus record.

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
**Decision on `workspace.py` reuse (resolved now, not deferred):** **Path (b) — Docker writes its own.** Evidence from reading `backend/src/sandbox/provider/daytona/workspace.py`:
- `discover_workspace` / `discover_workspace_async` (`workspace.py:32-51`) are **Daytona-specific**: they call `sandbox.process.exec("pwd")` (lines 37, 48), which is the Daytona SDK shape; Docker exposes a different exec surface.
- `_sandbox_project_root` (`workspace.py:13-22`) reads `sandbox.project_dir` and `sandbox.labels` — Daytona SDK shape (the module docstring explicitly says "The `sandbox` argument uses Daytona SDK shape: `project_dir`, `labels`, and `process.exec`. Keep this provider-owned until the lifecycle layer no longer needs raw provider objects for workspace discovery").
- `prepare_sandbox_runtime_context` (`workspace.py:54-76`) is *mostly* provider-neutral — it only manipulates the `context` dict — but it transitively calls `_sandbox_project_root(sandbox)` (line 70) as a fallback, so it inherits the Daytona shape coupling.

**Consequence:** Docker provider writes `provider/docker/workspace.py` mirroring the same public surface (`discover_workspace`, `discover_workspace_async`, `prepare_sandbox_runtime_context`) but resolving project root from Docker's container labels / `WORKDIR` / `exec("pwd")` via the Docker SDK shape. **The `sandbox/host/workspace.py` lift is NOT done in this plan**; §9 scope guard stands as written. Lifting is added to ADR §Follow-ups as a future cleanup (would require refactoring `prepare_sandbox_runtime_context` to accept a resolved project-root string instead of the raw sandbox handle).

**New files (all under `backend/src/sandbox/provider/docker/`):**

- `__init__.py` — exports `DockerProviderAdapter`, `bootstrap_docker_provider`. **Module docstring** documents:
  - `EOS_SANDBOX_PROVIDER`, `EOS_DOCKER_PRIVILEGED`, `EOS_DOCKER_NO_PRIVILEGE` env vars.
  - **macOS caveat:** "macOS Docker Desktop is unsupported as default; use Daytona on darwin or set `EOS_SANDBOX_PROVIDER=docker` explicitly. Even with explicit opt-in, Docker Desktop's VM UID-mapping and overlay-on-overlay2 storage driver may cause `mount_mode=COPY_BACKED` fallback for some execs."
  - Env-var precedence: `EOS_SANDBOX_PROVIDER` is authoritative over `DAYTONA_API_KEY`.
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
  - call **its own** local `prepare_sandbox_runtime_context(...)` (defined in `provider/docker/workspace.py`) — NOT the Daytona one (per decision above)
  - register `DockerProviderAdapter` for the sandbox via `register_adapter(sandbox_id, ...)` if not already registered (mirror `provider/daytona/exec_context.py:97-106`)
- `workspace.py` — Docker-specific `discover_workspace`, `discover_workspace_async`, `prepare_sandbox_runtime_context`. Public surface matches `provider/daytona/workspace.py` so call sites in `exec_context.py` are symmetric. Internals resolve project root from Docker container labels and a `docker exec ... pwd` via the SDK.
- `bootstrap.py` — `bootstrap_docker_provider()` that calls `set_default_provider(DockerProviderAdapter())`. Symmetric with `provider/daytona/bootstrap.py:15-17`.

**Verify:** Step 1's runtime-Protocol assertion passes for `DockerProviderAdapter` too.

### Step 3 — Provider dispatcher
**New file** `backend/src/sandbox/provider/bootstrap.py`:
```python
import logging
import os
import sys
import threading

_PROVIDER_BOOTSTRAPPED = False
_PROVIDER_BOOTSTRAP_LOCK = threading.Lock()
_FIRST_PROVIDER: str | None = None

logger = logging.getLogger(__name__)


def _resolve_provider_name() -> str:
    raw = os.environ.get("EOS_SANDBOX_PROVIDER")
    if raw is not None:
        return raw.strip().lower()
    if sys.platform == "darwin":
        return "daytona"
    if sys.platform.startswith("linux"):
        return "docker"
    raise RuntimeError(f"unsupported platform {sys.platform!r}; set EOS_SANDBOX_PROVIDER explicitly")


def bootstrap_sandbox_provider() -> None:
    """Select provider from EOS_SANDBOX_PROVIDER env var.

    Sentinel-gated: first call wins. Subsequent calls with a DIFFERENT env
    value log a warning and are no-ops. Subsequent calls with the SAME env
    value are silent no-ops.
    """
    global _PROVIDER_BOOTSTRAPPED, _FIRST_PROVIDER

    with _PROVIDER_BOOTSTRAP_LOCK:
        name = _resolve_provider_name()

        if _PROVIDER_BOOTSTRAPPED:
            if name != _FIRST_PROVIDER:
                logger.warning(
                    "bootstrap_sandbox_provider called twice with different "
                    "EOS_SANDBOX_PROVIDER (first=%s, now=%s); ignoring",
                    _FIRST_PROVIDER, name,
                )
            return

        if name == "docker":
            from sandbox.provider.docker.bootstrap import bootstrap_docker_provider
            bootstrap_docker_provider()
        elif name == "daytona":
            from sandbox.provider.daytona.bootstrap import bootstrap_daytona_provider
            bootstrap_daytona_provider()
        else:
            raise RuntimeError(
                f"Unknown EOS_SANDBOX_PROVIDER={name!r}; expected 'docker' or 'daytona'"
            )

        if name == "docker" and os.environ.get("DAYTONA_API_KEY"):
            logger.info(
                "Daytona credentials detected but provider=docker; ignoring DAYTONA_*"
            )

        logger.info("sandbox provider = %s", name)
        _FIRST_PROVIDER = name
        _PROVIDER_BOOTSTRAPPED = True
```

**Idempotency framing (corrected from v1):** `bootstrap_sandbox_provider()` itself is **sentinel-gated** via a `_PROVIDER_BOOTSTRAPPED` module-level flag — first call wins; subsequent calls with a different env value log a warning and are no-ops; subsequent calls with the same env value are silent no-ops. This pattern mirrors `_BOOTSTRAPPED` in `task_center_runner/core/bootstrap.py:16`. (The previous v1 claim that "set_default_provider is idempotent" was misleading — at `provider/registry.py:25-29` it is in fact last-writer-wins, not idempotent. The sentinel on the dispatcher is what enforces correctness.)

**Note for tests that parametrize across providers (e.g., `test_provider_parity.py` in §5.2):** such tests must reset `_PROVIDER_BOOTSTRAPPED = False` between cases via a pytest fixture, mirroring the same escape hatch tests already use for `_BOOTSTRAPPED` in `task_center_runner/core/bootstrap.py:16`. Without this, a parametrized test that runs `daytona` first then `docker` will silently keep `daytona` for the second case.

- Exported from `backend/src/sandbox/provider/__init__.py` alongside `set_default_provider`.

**Verify:** unit test in §5.1 (`test_dispatcher.py`) passes for all env-var cases including the new "second call with different env value emits warning and is no-op" case.

### Step 4 — Switch the three bootstrap call sites
Replace `bootstrap_daytona_provider()` with `bootstrap_sandbox_provider()` at:
1. `backend/src/task_center_runner/core/bootstrap.py:44-46` — the runtime startup hook called by CLI and pytest fixtures.
2. `backend/src/task_center_runner/benchmarks/sweevo/fixtures.py:81-87` — SWE-EVO pytest fixture.
3. `backend/src/benchmarks/sweevo/__main__.py:58-63` — SWE-EVO CLI entrypoint.

**Daytona's existing `bootstrap_daytona_provider()` stays callable** (do NOT delete it). The dispatcher just selects which bootstrap to call.

**Verify:** existing Daytona tests pass with `EOS_SANDBOX_PROVIDER=daytona` set (regression gate); Docker tests pass with the env unset (default on Linux) or set to `docker`.

### Step 5 — SWE-EVO snapshot-creation provider branch
**Edit** `backend/src/benchmarks/sweevo/sandbox.py:498-540`:
- Inside `register_sweevo_snapshot`, branch on `get_default_provider().name`:
  - `"daytona"` path: existing `subprocess.run(["daytona", "snapshot", "create", ...])` (unchanged).
  - `"docker"` path: `subprocess.run(["docker", "pull", image_ref])` then `subprocess.run(["docker", "tag", image_ref, name])`. Returns `name` (same contract).
  - **`else` branch (NEW, required):** `raise NotImplementedError(f"register_sweevo_snapshot does not support provider={provider_name!r}; supported: 'daytona', 'docker'")`. Unknown providers must fail loud, never silently skip. Failure mode this prevents: a future third provider added to `provider/` but forgotten in this benchmark branch — would otherwise return `None` or skip silently.
- Add a `register_sweevo_snapshot_docker` private helper to keep the diff readable.
- Investigate `benchmarks/sweevo/__main__.py:59` (further references to the `daytona` CLI in the same file) — apply identical branching with the same `else: raise NotImplementedError` discipline where present.

**Rationale for not putting this on the Protocol:** snapshot creation from a Dockerfile/image is a benchmark setup concern, not a runtime container primitive. Keeping it as a provider-name branch inside the benchmark preserves the Protocol's shape (constraint: "do NOT modify Protocol beyond `context_preparer`").

**Enforcement (added to ADR §Consequences):** a linter rule or test enforces the branch covers every registered provider name — e.g., a parametrized unit test `test_register_sweevo_snapshot_covers_all_providers` iterates `get_registered_provider_names()` and asserts each name does not raise `NotImplementedError` from this function (skipping side effects via mock).

**Verify:** `EOS_SANDBOX_PROVIDER=docker python -m benchmarks.sweevo --instance-id <id>` proceeds past snapshot registration; setting `EOS_SANDBOX_PROVIDER=foo` reaches the `NotImplementedError` cleanly.

### Step 6 — macOS verification + capability-probe logging
- Add a one-time startup probe inside `DockerProviderAdapter.create()` (post-`docker start`): run `detect_private_mount_namespace()` script (`namespace.py:137-152`) inside the container via `adapter.exec`, log result.
- The dispatcher's per-platform default (§3 Axis B) means macOS users get Daytona unless they set `EOS_SANDBOX_PROVIDER=docker` explicitly. `provider/docker/__init__.py` module docstring (per §6 Step 2) documents this and the supporting reasoning.
- Add a manual smoke-test entry to `backend/scripts/` (e.g., `smoke_docker_provider.sh`): runs `EOS_SANDBOX_PROVIDER=docker python -m benchmarks.sweevo` on a known instance and grep-checks the log for `mount_mode=PRIVATE_NAMESPACE` ratio ≥95% per §7.3.

**Verify:** the smoke script exits 0 on Linux dev host with PRIVATE_NAMESPACE coverage ≥95%; exits with documented warning on macOS Docker Desktop if PRIVATE_NAMESPACE is below threshold (this is the documented unsupported configuration).

## 7. Acceptance Criteria

1. **Protocol conformance (static):** `isinstance(DockerProviderAdapter(), ProviderAdapter)` and `isinstance(DaytonaProviderAdapter(), ProviderAdapter)` both return `True` under `@runtime_checkable`, OR an attribute-presence assertion passes for every method listed in `protocol.py:21-64` plus the new `context_preparer`.
2. **Daytona regression gate:** the full existing test suite runs under `EOS_SANDBOX_PROVIDER=daytona python -m pytest backend/tests/unit_test/test_sandbox/ backend/tests/unit_test/test_benchmarks/ backend/tests/unit_test/test_task_center/` with **zero new failures vs. main**.
3. **Docker end-to-end (strengthened from v1):** `EOS_SANDBOX_PROVIDER=docker python -m benchmarks.sweevo --instance-id <known-id> --no-register-snapshot` completes the full lifecycle — `setup_post_lifecycle` → `ensure_git` → `ensure_runtime_uploaded` → `ensure_workspace_base` returns `ready=True`. **Plus both of:**
   - **(a) Mount-mode coverage ratio:** **≥95% of `attempt`-strategy execs** in the run report `ShellProcessResult.mount_mode == MountMode.PRIVATE_NAMESPACE` on a **Linux CI host**. (See §5.3 test for ratio definition and computation.)
   - **(b) Perf delta:** **p95 exec latency within ±25%** of the Daytona baseline on the same SWE-EVO instance (baseline file: `tests/integration_test/test_benchmarks/data/daytona_baseline_p95.json`; procedure in §5.3).
   - **Failure mode if (a) or (b) misses:** abort the default flip; either demote A.2 (per §6 Step 0 decision rule) or document the host-specific cause and re-trigger consensus loop.
4. **Provider identity:** the new dispatcher logs `sandbox provider = docker` (or `daytona`) exactly once at startup; visible in stderr/stdout for both CLI entrypoints and pytest runs.
5. **Build-logs URL tolerance:** `EOS_SANDBOX_PROVIDER=docker` SWE-EVO run does not raise from `benchmarks/sweevo/sandbox.py:310` despite `get_build_logs_url()` returning `None` (already wrapped in try/except — verify path is exercised).
6. **No host/daemon edits beyond observability:** `git diff main --stat sandbox/host/ sandbox/daemon/ sandbox/execution/ sandbox/layer_stack/` shows at most one new log line in `host/lifecycle.py`. No other lines change.
7. **Documentation:** `provider/docker/__init__.py` docstring documents the `EOS_SANDBOX_PROVIDER`, `EOS_DOCKER_PRIVILEGED`, `EOS_DOCKER_NO_PRIVILEGE` env vars, the env-var precedence rule (`EOS_SANDBOX_PROVIDER` authoritative over `DAYTONA_API_KEY`), and the macOS-unsupported-as-default caveat.

## 8. Rollback Plan

**Single-knob revert: set `EOS_SANDBOX_PROVIDER=daytona` in the environment AND restart the process.**

> **Rollback requires process restart due to the `_BOOTSTRAPPED` sentinel at `task_center_runner/core/bootstrap.py:16,41`.** There is no in-process flip path. The runtime's `bootstrap_real_agent_runtime()` is a one-shot under the existing sentinel, and the new `bootstrap_sandbox_provider()` is sentinel-gated the same way (see §6 Step 3, `_PROVIDER_BOOTSTRAPPED`). Flipping `EOS_SANDBOX_PROVIDER=daytona` in a long-lived runner daemon, a test harness with module-cached state, or a Jupyter session is **inert until process exit**.

**Step-level rollback during implementation:**
- Each step (0, 1, 2, 3, 4, 5, 6) lands as its own commit with the regression gate (criterion #2) as a precondition.
- Step 0 (pre-flight CI experiment) is the gate: if A.2 fails preflight, halt the plan, escalate to consensus revision, do not commit Steps 1+.
- Step 1 (Protocol method add) is safe to ship independently — adds a method already present on the live adapter.
- If Step 4 (call-site switch) regresses Daytona tests, revert that commit; Steps 1–3 are still useful prep.
- If Step 5 regresses SWE-EVO under Daytona, revert; Step 4 still works because the new dispatcher routes back to `bootstrap_daytona_provider()`.
- **No DB migration. No agent-profile change. No state file format change.** Rollback is `git revert <commit> && unset EOS_SANDBOX_PROVIDER && <restart process>`.

## 9. Out of Scope (explicit non-goals)

- **Do NOT** unify Daytona "snapshot" and Docker "image" verbs at the Protocol level. The Protocol's `create(snapshot=..., image=...)` already accepts both (`provider/protocol.py:33-42`); leave it asymmetric.
- **Do NOT** change anything in `sandbox/layer_stack/`, `sandbox/daemon/`, `sandbox/execution/` (except for the optional one-line log addition in `host/lifecycle.py` per §5.4). **Workspace.py lift to `sandbox/host/` is explicitly NOT done in this plan** — see §6 Step 2 decision; lift is a future cleanup in ADR §Follow-ups.
- **Do NOT** introduce platform-specific code (macOS branches, Linux branches) into `sandbox.host.*` or `sandbox.daemon.*`. All platform handling lives inside `sandbox.provider.docker.*` plus the dispatcher's per-platform default in `sandbox/provider/bootstrap.py`.
- **Do NOT** delete `provider/daytona/bootstrap.py` or rename the Daytona adapter. Both providers remain first-class.
- **Do NOT** change `task_center_runner/core/bootstrap.py`'s agent-registry loading; only its provider-bootstrap line.
- **Do NOT** add a config-file mechanism for provider selection in this plan. Env-var only (Option B.1).
- **Do NOT** modify the `_BOOTSTRAPPED` sentinel pattern in `task_center_runner/core/bootstrap.py:16,68`. The dispatcher uses an independent `_PROVIDER_BOOTSTRAPPED` sentinel.
- **Do NOT** alter SWE-EVO image content, Dockerfile, or daemon scripts. Docker provider consumes whatever image SWE-EVO supplies, with the same daemon assets uploaded by `ensure_runtime_uploaded`.

---

## ADR (decision record for archival)

**Decision:** Make Docker the default `ProviderAdapter` on Linux while keeping Daytona as the `darwin` default and as an `EOS_SANDBOX_PROVIDER=daytona`-selectable alternative on any platform.

**Drivers:**
1. Minimize Daytona–Docker behavioral divergence at the host↔daemon seam.
2. Preserve overlay-based layer-stack performance.
3. Safe rollback via single env-var flip + process restart.

**Alternatives considered:**
- Docker with `--privileged` (Option A.1): rejected as default — oversized blast radius. Kept as `EOS_DOCKER_PRIVILEGED=1` escape hatch.
- Docker with `--userns=host`/rootless (Option A.3): rejected — overlay-on-overlay risk in Docker Desktop VM.
- Docker with no caps, COPY_BACKED-only (Option A.4): rejected — forfeits layer-stack perf. Kept as `EOS_DOCKER_NO_PRIVILEGE=1` for hostile-multi-tenant.
- Single-platform default (e.g., Docker default everywhere including darwin): rejected — Principle 4 (COPY_BACKED is safety net, not co-equal mode) requires Daytona as macOS default.
- Config-file provider selection (B.2): rejected — no existing config infrastructure.
- CLI-flag provider selection (B.3): rejected — env-var subsumes; multiple entrypoints simplify.
- Adding snapshot-creation to the Protocol: rejected — benchmark-local concern, keeps Protocol minimal.
- Lifting `prepare_sandbox_runtime_context` to `sandbox/host/workspace.py` now: rejected — the helper inherits Daytona SDK shape via `_sandbox_project_root(sandbox)` (reads `sandbox.project_dir`, `sandbox.labels`); a clean lift requires a refactor to pass resolved strings instead of sandbox handles. Deferred to follow-up.

**Why chosen:**
- Option A.2 is the minimum-cap surface that unblocks `unshare -Urm` + overlayfs `mount(2)`; conditional on the Step 0 pre-flight CI experiment proving sufficiency for the layer-stack hottest paths. Pairs with the already-wired `COPY_BACKED` fallback for hosts that reject SYS_ADMIN.
- Option B.1 matches existing `DAYTONA_*` env-var conventions; works identically from CLI, pytest, and process startup; per-platform default (`darwin → daytona`, `linux → docker`) honors Principle 4 on every supported platform.
- Promoting `context_preparer` to the Protocol formalizes existing duck-typed behavior (`api/_sandbox_control.py:95`) with zero caller change.

**Consequences:**
- New maintenance surface: `provider/docker/` package + Docker Python SDK dependency.
- macOS Docker Desktop is explicitly an **unsupported default configuration**; macOS users get Daytona by default. If they opt-in via `EOS_SANDBOX_PROVIDER=docker` on darwin, they should expect `mount_mode=COPY_BACKED` for some execs and the documented workaround is to switch back to Daytona.
- SWE-EVO snapshot registration acquires a provider-branch in `benchmarks/sweevo/sandbox.py` with mandatory `else: raise NotImplementedError(...)`. **Linter rule or test enforces the branch covers every registered provider name** (see §6 Step 5).
- Operators must learn the new env var; mitigation: startup log line announces active provider; second log line warns if `DAYTONA_API_KEY` is present alongside `EOS_SANDBOX_PROVIDER=docker`.
- **Rollback is not in-process** — flipping the env var requires process restart due to the `_BOOTSTRAPPED` and `_PROVIDER_BOOTSTRAPPED` sentinels. Documented in §8.

**Follow-ups:**
- Track macOS PRIVATE_NAMESPACE coverage; if persistently zero on opt-in users, investigate Docker Desktop VM seccomp profile tuning or formal rootless mode (revisit Option A.3).
- **Lift `prepare_sandbox_runtime_context` to `sandbox/host/workspace.py`** by refactoring it to accept a pre-resolved `project_root: str | None` instead of the raw sandbox handle. This unblocks deduplication between Daytona and Docker `workspace.py` files.
- Add Docker provider perf benchmarks parallel to existing Daytona perf tests (`backend/tests/unit_test/test_sandbox/...`).
- Consider promoting the Docker `register_sweevo_snapshot` branch to a separate `BenchmarkSnapshotRegistrar` Protocol in a future cleanup, if a third provider is added.
- Re-baseline `daytona_baseline_p95.json` annually or on Daytona/SWE-EVO upgrade (see §5.3 procedure).
- **Migrate `execution/overlay/kernel_mount.py:35-50` from `subprocess.run(["mount", ...])` (mount(8)) to a direct `mount(2)` syscall via ctypes/libc** to unlock the 199-layer overlay regime documented in project memory. After that migration lands, re-run Step 0 against the syscall path to validate A.2 still suffices, and consider tightening §7.3 perf delta from ±25%.
