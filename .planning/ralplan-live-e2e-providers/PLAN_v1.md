# PLAN v1 — Generic provider-pluggable live e2e suite (Docker target)

**Mode:** RALPLAN-DR short
**Goal:** Run the existing `backend/tests/live_e2e_test/sandbox/` suite (every scenario, including the heavy phase09 matrices and the soak/adversarial tiers, excluding real_agent flows) under any registered sandbox provider — Daytona today, Docker for this run, and any future provider with zero suite-side changes.

---

## 0. Mental model (load-bearing)

Verified from primary sources:

- The "live e2e" suite is the tree under `backend/tests/live_e2e_test/sandbox/` (subdirs: `layer_stack/`, `layer_stack_overlay_occ/`, `workspace_base/`, `occ/`, `overlay/`, `request_snapshot/`, `_harness/`).
- A single session-scoped `live_sandbox` fixture (`_harness/sandbox_fixture.py:201`) brings up exactly one sandbox via `setup_after_create` and every per-suite fixture (`overlay_sandbox`, `integrated_sandbox`, `workspace_base_sandbox`, `native_sandbox`) chains off it. **There is only one provider seam to flip.**
- The fixture currently calls `bootstrap_daytona_provider()` directly at `_harness/sandbox_fixture.py:194` and reads the image string from `settings.sandbox.default_image`. Everything below that line is provider-neutral (`provider.create(...)`, `register_adapter(...)`, `setup_after_create(...)`).
- Tier orchestration lives in `_tools/run_tiered.py` + `_tools/tiers.toml`. Tier 0 is a Daytona-specific HTTP `/api/health` probe (`tier0_health.py`). Tiers 1–6 are pure `pytest` invocations against the live tree (smoke → k_scaling → single/cross-axis matrices → soak → adversarial). **Tier 7 (`sweevo_mock_framework`) points to `backend/src/live_e2e/tests/sweevo/`, a path that does not exist on disk; it is the closest thing to "real_agent" in this config and is excluded by this plan.**
- The `EOS_SANDBOX_PROVIDER` env var + `bootstrap_sandbox_provider()` dispatcher already exists (PLAN_v4-docker-provider landed). This plan reuses that seam — it does NOT introduce a parallel provider-selection mechanism.
- "Heavy" in user vocabulary = tiers 4 (cross_axis_matrices), 5 (soak), 6 (adversarial). "Excluding real_agent" = exclude tier 7 + any path under `backend/src/task_center_runner` and `backend/tests/unit_test/test_task_center_runner/test_real_agent_*`. The live_e2e_test/sandbox/ tree has no real_agent tests already.

---

## 1. Principles

1. **One provider seam, one env var.** Reuse `EOS_SANDBOX_PROVIDER` and `bootstrap_sandbox_provider()`. Do NOT add a `--provider` flag that races the env var.
2. **Tests stay provider-agnostic.** Test files in `live_e2e_test/sandbox/**/test_*.py` must not import `sandbox.provider.daytona` or `sandbox.provider.docker`. The harness owns provider knowledge.
3. **Image resolution is provider-shaped.** Daytona consumes a remote snapshot name; Docker consumes a local-or-pullable image tag. The harness must accept both via a provider-typed config field, not a single `default_image` string overloaded by convention.
4. **Tier runner is provider-aware at preflight only.** The Tier 0 health probe branches per provider; Tiers 1–6 are oblivious — they just run pytest against the same tree.
5. **No new test files in this plan.** Adding Docker variants of existing tests doubles the suite for zero coverage gain. Generalize the harness; reuse the tree.

## 2. Decision Drivers (top 3)

1. **Surface-minimum changes to the live tree.** ~40 existing test files in `live_e2e_test/sandbox/**` must continue to pass unchanged under both providers. Touching any of them is a regression risk that scales with file count.
2. **Cycle time on Docker locally.** Daytona pays a ~7s sandbox bring-up + remote round-trip per `raw_exec`. Docker brings up faster (~1–2s) and execs locally. The plan should let developers iterate quickly under Docker on Linux, without forcing Daytona round-trips for unrelated work.
3. **Preflight realism per provider.** Daytona's tier 0 is HTTP health on `http://localhost:3000/api`. Docker's equivalent is `docker info` + image presence (`docker image inspect`). A wrong preflight masks real environment problems and produces tier-2+ failures that look like test bugs.

## 3. Viable Options

### Axis A — Where the provider switch is wired

| Option | Mechanism | Pros | Cons | Verdict |
|---|---|---|---|---|
| **A.1 `EOS_SANDBOX_PROVIDER` env var only** | Reuse the PLAN_v4 dispatcher; `live_sandbox` calls `bootstrap_sandbox_provider()`. Tests inherit. | Zero new surface; matches recently-landed convention; works identically from CLI and pytest. | None material — env var already governs the same choice everywhere else in the codebase. | **RECOMMENDED.** |
| **A.2 New `--provider` CLI flag on `run_tiered.py`** | Tier runner takes `--provider docker\|daytona`, exports the env var, then forks pytest. | Explicit per-run; visible in `--help`. | Convenience wrapper, not a separate mechanism. The env-var still does the real work; the flag is sugar. | Acceptable as **sugar**, NOT a substitute for A.1. Land both. |
| **A.3 Per-test parametrize** | `pytest.mark.parametrize("provider", ["docker", "daytona"])` on every fixture. | One pytest invocation covers both. | Doubles suite runtime; each test now needs sandbox bring-up per param; requires touching every test or every harness fixture. Violates Principle 5. | **Reject.** |

**Recommended:** A.1 + A.2 (flag is sugar over the env var).

### Axis B — Image resolution shape

| Option | Mechanism | Pros | Cons | Verdict |
|---|---|---|---|---|
| **B.1 Single `default_image` setting, provider-interpreted** | `settings.sandbox.default_image` stays one string; Docker treats it as a local image tag, Daytona as a snapshot name. | Zero settings churn; works today if the image name happens to satisfy both. | Footgun: ops sets a Daytona snapshot name unreachable by Docker, suite fails opaquely at create-time. | Reject as default. |
| **B.2 Provider-typed env vars, harness resolves** | New `EOS_LIVE_E2E_IMAGE` (provider-neutral name) read by the harness; harness validates the value matches the active provider's expectations (Docker: `docker image inspect <tag>` succeeds locally OR `docker pull` succeeds; Daytona: snapshot exists). | Explicit, one env var per host; fails loud at preflight, not deep inside a test. | New env var; need to document. | **RECOMMENDED.** |
| **B.3 Provider-keyed settings sub-fields** | `settings.sandbox.docker_default_image`, `settings.sandbox.daytona_default_image`. | Both can coexist with no override dance. | Settings schema churn; need code in `config/` and `.env` docs; overkill for one consumer. | Reject. |

**Recommended:** B.2. Reuse existing `settings.sandbox.default_image` as the Daytona-compatible fallback when `EOS_LIVE_E2E_IMAGE` is unset and provider == daytona.

### Axis C — Tier preflight per provider

| Option | Mechanism | Pros | Cons | Verdict |
|---|---|---|---|---|
| **C.1 Branch inside `tier0_health.py`** | `probe_tier0(provider=...)` dispatches: daytona → existing HTTP probe; docker → `docker info` + image presence + `unshare -Urm true` host capability. | Single dispatcher; matches the dispatcher pattern from PLAN_v4. | Touches one file. | **RECOMMENDED.** |
| **C.2 Skip tier 0 for Docker** | Just don't probe; let tier 1 fail naturally. | No new code. | Loses the cascade-abort signal; tier-1 smoke failure is harder to diagnose than "docker daemon not running". | Reject. |
| **C.3 New tier 0' for Docker, leave Daytona tier 0 alone** | Two separate tier-0 entries gated on provider. | No conditional logic inside the probe. | Doubles the tier definition; `tiers.toml` becomes per-provider, multiplying maintenance. | Reject. |

**Recommended:** C.1.

### Axis D — Excluding tier 7 + real_agent paths

| Option | Mechanism | Pros | Cons | Verdict |
|---|---|---|---|---|
| **D.1 Delete tier 7 from `tiers.toml`** | Remove the stale `sweevo_mock_framework` entry. | Honest — the path doesn't exist; the tier is dead. | If someone later adds the suite back, they re-add the tier (which is correct). | **RECOMMENDED.** |
| **D.2 Tag tier 7 as `real_agent` and skip by default** | Add `tags = ["real_agent"]` to the entry; runner skips tagged tiers unless `--include-tag real_agent`. | Keeps the entry alive for future. | Designs infrastructure for a tier whose target path doesn't exist. | Reject for now; revisit if `backend/src/live_e2e/tests/sweevo/` is reintroduced. |

**Recommended:** D.1.

## 4. File-by-file work breakdown

### Step 0 — Pre-flight verification (no code change)

- **Action:** Run `docker info` + `docker image inspect $EOS_LIVE_E2E_IMAGE` on the target Linux host. Confirm the image contains `git`, has `/testbed` writable, and ships the runtime bundle marker (`BUNDLE_HASH_MARKER` at `BUNDLE_REMOTE_DIR`).
- **Decision rule:**
  - All three checks pass → proceed to Step 1.
  - `docker info` fails → ask the operator to start docker.
  - Image missing `git` / `/testbed` / runtime bundle → bake them in (out of scope for this plan; the live tree assumes a "prebaked" image — same Daytona contract).
- **Note:** This step is documented procedure, NOT new code. Step 4 wires the equivalent into tier 0 so future runs catch it automatically.

### Step 1 — Provider-generic `live_sandbox` fixture
- **Edit** `backend/tests/live_e2e_test/sandbox/_harness/sandbox_fixture.py`:
  - Replace `from sandbox.provider.daytona.bootstrap import bootstrap_daytona_provider` with `from sandbox.provider.bootstrap import bootstrap_sandbox_provider`.
  - Replace the `bootstrap_daytona_provider()` call at `_bring_up_sandbox()` with `bootstrap_sandbox_provider()`.
  - Replace the image resolution: read `os.environ.get("EOS_LIVE_E2E_IMAGE")` first, fall back to `settings.sandbox.default_image`. Skip cleanly when both are empty.
  - No change to `provider.create(...)`, `register_adapter(...)`, `setup_after_create(...)` — those are already provider-neutral.
- **Verify:** `EOS_SANDBOX_PROVIDER=daytona pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase00_smoke.py` and `EOS_SANDBOX_PROVIDER=docker EOS_LIVE_E2E_IMAGE=<tag> pytest …test_phase00_smoke.py` both pass.

### Step 2 — Conftest marker realism
- **Edit** `backend/tests/live_e2e_test/conftest.py`:
  - Update the docstring + marker definition for `live_e2e_daytona` to note it is **provider-agnostic in practice** (gated by `EOS_SANDBOX_PROVIDER`). Keep the marker name for backward compat.
  - Add a new marker `live_e2e_provider_neutral` documented as "tests that run unchanged across providers"; apply it via `pytestmark` only if it materializes a real use case. Otherwise skip this addition — Principle 5 says no marker churn without value.
- **Verify:** `pytest --markers | grep live_e2e` shows the documented set; no test files change.

### Step 3 — Provider-aware tier 0 probe
- **Edit** `backend/tests/live_e2e_test/_tools/tier0_health.py`:
  - `probe_tier0()` reads `os.environ.get("EOS_SANDBOX_PROVIDER")` (with the same darwin→daytona / linux→docker default as `sandbox.provider.bootstrap`).
  - If `daytona`: existing HTTP `/api/health` probe (unchanged).
  - If `docker`: shell out via `subprocess.run(["docker", "info"], ...)`; then `docker image inspect $EOS_LIVE_E2E_IMAGE`; emit one `Tier0Result(passed=True, …)` on success or a per-failure detail string. Mirrors the Daytona shape.
  - Unknown provider: return `Tier0Result(passed=False, detail="unsupported provider for tier 0 probe")`.
- **Verify:** `python -m backend.tests.live_e2e_test._tools.run_tiered --tiers 0` exits 0 under both providers on a healthy host; exits non-zero with a clear message when the corresponding daemon is down.

### Step 4 — Tier runner `--provider` sugar + tier 7 removal
- **Edit** `backend/tests/live_e2e_test/_tools/run_tiered.py`:
  - Add `--provider {docker,daytona}` CLI flag; when set, the runner exports `EOS_SANDBOX_PROVIDER=<value>` into the child pytest environment (sugar over the env var).
  - When `--provider` is unset, the runner reads the inherited env (no-op) — exactly the existing semantics with no surprises.
  - Print the resolved provider as the first line of the run summary, so artifacts encode which provider produced them.
- **Edit** `backend/tests/live_e2e_test/_tools/tiers.toml`:
  - Delete the `[[tier]] id = 7 name = "sweevo_mock_framework"` block — the path `backend/src/live_e2e/tests/sweevo/` does not exist. Update the section comment to "Tiers 1–6 are provider-agnostic; preflight (tier 0) branches per provider".
  - Adjust `cascade = "abort_eq", cascade_target = 6` in tier 5 to `cascade = "warn"` since there is no tier 6+ to abort to once tier 7 is gone. (Re-validate: tier 6 stays; tier 5's cascade target stays 6.) — actually keep tier 5's cascade as-is; only the dead tier 7 is removed.
- **Verify:** `python -m backend.tests.live_e2e_test._tools.run_tiered --provider docker --tiers 0-6` enumerates 7 tiers, runs tier 0 docker probe, tiers 1–6 pytest, prints `provider=docker` in the summary.

### Step 5 — Documentation + scripts
- **Edit** `backend/tests/live_e2e_test/sandbox/README.md`:
  - Add a "Running on Docker" section: env var matrix (`EOS_SANDBOX_PROVIDER=docker`, `EOS_LIVE_E2E_IMAGE`, optional `EOS_DOCKER_PRIVILEGED`), Linux-only caveat, recommended image bake recipe (git + /testbed + runtime bundle).
- **New** `backend/scripts/run_live_e2e_docker.sh`:
  - Shell wrapper: bails on non-Linux; sets `EOS_SANDBOX_PROVIDER=docker`; calls `python -m backend.tests.live_e2e_test._tools.run_tiered --provider docker --tiers 0-6` and uploads artifacts (mirroring existing patterns).
- **Verify:** README example commands copy-paste-execute on a fresh Linux dev box with docker + a baked image.

## 5. Test Plan

### 5.1 Unit
- New `backend/tests/unit_test/test_sandbox/test_provider/test_live_harness_provider_resolution.py`: parametrize over `(env, expected_provider, expected_image_source)`. Asserts the harness's image-resolution helper picks `EOS_LIVE_E2E_IMAGE` first, falls back to `settings.sandbox.default_image`, and raises cleanly when both are empty.

### 5.2 Integration
- Manual + CI: `EOS_SANDBOX_PROVIDER=daytona pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase00_smoke.py` passes (regression gate against existing Daytona path).
- `EOS_SANDBOX_PROVIDER=docker EOS_LIVE_E2E_IMAGE=<tag> pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase00_smoke.py` passes on a Linux host with Docker (new path).

### 5.3 E2E
- `bash backend/scripts/run_live_e2e_docker.sh` on a Linux CI runner runs tiers 0–6 end to end. Acceptance: tier 0 reports `docker info OK`; tier 1 smoke passes; tiers 2–6 cascade rules behave identically to the Daytona baseline (warn/abort outcomes match modulo provider-specific flakes documented in the run report).
- A second invocation with `--provider daytona` on the same host (if creds present) passes tier 1 and either runs or skips tiers 2–6 per existing budgets — proving no regression.

### 5.4 Observability
- Tier-runner summary now prefixes each `TierOutcome` log with `provider=<name>`; existing artifact JSON gains a top-level `provider` field. Operators can diff Daytona vs Docker tier outcomes from the same artifact directory.

## 6. Acceptance Criteria

1. `_harness/sandbox_fixture.py` imports no provider-specific module; one grep `rg 'provider\.(daytona|docker)' backend/tests/live_e2e_test/` returns zero hits.
2. `pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase00_smoke.py` passes under both `EOS_SANDBOX_PROVIDER=daytona` (with creds + default image) and `EOS_SANDBOX_PROVIDER=docker` (with `EOS_LIVE_E2E_IMAGE` set + docker daemon up) on a Linux host.
3. `python -m backend.tests.live_e2e_test._tools.run_tiered --provider docker --tiers 0-6` on a Linux host completes tier 0 (docker info + image presence), then runs tiers 1–6; per-tier cascade behavior is identical to the Daytona baseline modulo any provider-specific failures captured in the run report.
4. `tier 7 (sweevo_mock_framework)` is removed from `tiers.toml`; the runner enumerates exactly 7 tiers (0–6). Real_agent paths under `task_center_runner` are untouched and out of scope.
5. README documents the Docker invocation; `backend/scripts/run_live_e2e_docker.sh` exists, is executable, and bails cleanly on non-Linux.
6. Daytona regression gate: existing `pytest backend/tests/live_e2e_test` invocation (no env override, Daytona creds present) shows zero new failures vs main.

## 7. Rollback

- Step 1 is the only behavioral change; revert that commit and the suite returns to the Daytona-hard-coded path. Steps 2–5 are additive (docs, tier-runner sugar, new env var, new probe branch) and are independently revertible.
- No DB migration, no agent profile change, no test rewrite.

## 8. Out of Scope (explicit non-goals)

- **Do NOT** add Docker variants of existing tests. The whole point is one tree, two providers.
- **Do NOT** touch `task_center_runner` real_agent code paths or unit tests (`test_real_agent_*.py`). User explicitly excluded.
- **Do NOT** modify `sandbox.host.bootstrap.setup_after_create` or any `sandbox.layer_stack/overlay/occ/daemon` internals — the conftest import fence already forbids that.
- **Do NOT** introduce a new tier (8+) for Docker-specific perf measurement. If a Docker-vs-Daytona delta study is wanted, that's a separate plan.
- **Do NOT** bake a new image in this plan. The plan assumes the operator supplies an image that satisfies the live-tree assumptions (`/testbed`, `git`, runtime bundle marker). Image authoring is a one-time devops task, not test infrastructure.
- **Do NOT** parallelize across providers in one pytest invocation (`pytest.mark.parametrize`). Sequential per-provider runs are cleaner and the artifact diff is the comparison surface.

---

## ADR (decision record)

**Decision:** Generalize the existing `live_e2e_test/sandbox/` suite to run unchanged under any provider selected by `EOS_SANDBOX_PROVIDER`, by flipping a single line in the session-scoped `live_sandbox` fixture and branching the tier-0 health probe per provider. Remove dead tier 7 (`sweevo_mock_framework`). Run Docker via this seam now.

**Drivers:**
1. Surface-minimum changes to the live test tree (one fixture file + one tier-config file + one probe file).
2. Reuse existing `bootstrap_sandbox_provider()` dispatcher (PLAN_v4-docker-provider).
3. Realistic per-provider preflight (tier 0) so environment problems fail loud at probe time.

**Alternatives considered:**
- Per-test `pytest.mark.parametrize` over providers: rejected (doubles runtime, churns every fixture).
- New CLI flag as the source of truth instead of env var: rejected (races the existing convention).
- Provider-keyed settings sub-fields for image: rejected (overkill).
- Keep tier 7 alive with a tag and skip-by-default: rejected (the target path doesn't exist).

**Why chosen:**
- One env var (`EOS_SANDBOX_PROVIDER`) + one new env var (`EOS_LIVE_E2E_IMAGE`) is the same idiom developers already use to flip Daytona/Docker elsewhere in this codebase.
- Tier 0 branch is a five-line change to one file; tiers 1–6 stay oblivious.
- ~40 test files in `live_e2e_test/sandbox/**` change zero lines.

**Consequences:**
- New maintenance: `EOS_LIVE_E2E_IMAGE` is now part of the operator's mental model. Mitigation: README + tier-0 probe fails loud when unset on Docker.
- Tier 0 probe now has a small provider switch. Mitigation: unit-test the resolution helper; failure shape is identical across branches.
- The dead tier 7 entry is removed; if `backend/src/live_e2e/tests/sweevo/` is ever (re)introduced, a new tier entry must be added intentionally.

**Follow-ups:**
- If Docker p95 exec latency diverges materially from Daytona on the same SWE-EVO-like workload, add a comparison harness as a follow-up plan — out of scope here.
- If the operator demand for "run both providers in one command" emerges, layer a thin shell wrapper that runs `run_tiered` twice with different `--provider` values and diffs artifacts. Still not a pytest-parametrize change.
- Consider adding a tag-based skip mechanism to `run_tiered.py` (`--exclude-tag real_agent`) if real_agent tiers come back; defer until they do.
