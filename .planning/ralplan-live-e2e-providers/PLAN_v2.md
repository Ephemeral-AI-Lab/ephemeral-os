# PLAN v2 — Generic provider-pluggable live e2e suite (Docker target)

**Mode:** RALPLAN-DR short
**Status:** Revised after architect review v1 (`.planning/ralplan-live-e2e-providers/ARCHITECT_REVIEW_v1.md` — Verdict: ITERATE with four bounded amendments). v1 prose preserved except where explicitly noted; v2 changes flagged with **[v2]**.
**Goal:** Run the existing `backend/tests/live_e2e_test/sandbox/` suite (every scenario, including the heavy phase09 matrices and the soak/adversarial tiers, excluding real_agent flows) under any registered sandbox provider — Daytona today, Docker for this run, and any future provider with zero suite-side changes.

---

## 0. Mental model (load-bearing)

Verified from primary sources:

- The live e2e suite is the tree under `backend/tests/live_e2e_test/sandbox/` (subdirs: `layer_stack/`, `layer_stack_overlay_occ/`, `workspace_base/`, `occ/`, `overlay/`, `request_snapshot/`, `_harness/`).
- A single session-scoped `live_sandbox` fixture (`_harness/sandbox_fixture.py:201`) brings up exactly one sandbox via `setup_after_create` and every per-suite fixture chains off it. **There is only one provider seam at the import level — but several behavioral preconditions ride alongside it, see §0.1 [v2].**
- The fixture currently calls `bootstrap_daytona_provider()` at `_harness/sandbox_fixture.py:194` and reads the image string from `settings.sandbox.default_image`. Everything below that line uses the provider-neutral `provider.create(...)` / `register_adapter(...)` / `setup_after_create(...)` surface.
- Tier orchestration lives in `_tools/run_tiered.py` + `_tools/tiers.toml`. Tier 0 is a Daytona-specific HTTP `/api/health` probe (`tier0_health.py:300`). Tiers 1–6 are pure `pytest` invocations. Tier 7 (`sweevo_mock_framework`) points to `backend/src/live_e2e/tests/sweevo/`, a path that does not exist on disk; it is excluded by this plan.
- The `EOS_SANDBOX_PROVIDER` env var + `bootstrap_sandbox_provider()` dispatcher already exists. This plan reuses that seam.

### 0.1 [v2] Behavioral preconditions the Daytona image silently satisfies

The architect surfaced that swapping providers moves these from "implicit SaaS-image guarantee" to "operator responsibility on local Linux":

| Precondition | Source of truth on Daytona | Source of truth on Docker | Verified at |
|---|---|---|---|
| Container runs as root with CAP_SYS_ADMIN | Daytona images bake it (cf. `overlay/syscall/test_mount_depth.py:128` comment "Daytona images run as root with CAP_SYS_ADMIN already") | `EOS_DOCKER_PRIVILEGED=1` OR PLAN_v4 default cap set (`--cap-add=SYS_ADMIN --security-opt seccomp=unconfined --security-opt apparmor=unconfined`) | Tier 0 docker probe (§Step 3 [v2]) |
| `unshare -Urm` succeeds inside the container | Implicit (root + CAP_SYS_ADMIN) | Same | Tier 0 docker probe runs `docker run --rm <img> sh -c 'unshare -Urm true'` |
| `git` on PATH | Baked into Daytona image | Operator bakes into Docker image | Tier 0 docker probe runs `command -v git` |
| `/testbed` exists and is writable | Baked | Operator bakes | Tier 0 docker probe runs `test -w /testbed` |
| Runtime bundle target dir + `BUNDLE_HASH_MARKER` reachable | Baked or filled by `setup_after_create`'s upload path | Same path; `setup_after_create` uploads it via `adapter.exec` after `provider.create()` returns | Tier 1 smoke (first `setup_after_create` under docker) |

**Failure mode this prevents:** without an explicit precondition probe, half the suite (everything under `overlay/syscall/` plus the integrated/native fixtures) fails at runtime with cryptic `mount: permission denied`, leaving operators to debug what should be tier-0-fail-loud.

---

## 1. Principles

1. **One provider seam, one env var.** Reuse `EOS_SANDBOX_PROVIDER` and `bootstrap_sandbox_provider()`. Do NOT add a `--provider` flag that races the env var.
2. **Tests stay provider-agnostic.** No test file in `live_e2e_test/sandbox/**/test_*.py` imports `sandbox.provider.daytona` or `sandbox.provider.docker`. The harness owns provider knowledge.
3. **Image resolution is provider-shaped, fail-loud on mismatch.** Daytona consumes a remote snapshot name; Docker consumes a local-or-pullable image tag. **[v2] Fallback to `settings.sandbox.default_image` is permitted only when `provider == daytona`** — under Docker, missing `EOS_LIVE_E2E_IMAGE` is a hard fixture skip with a clear message, never an implicit pull of a Daytona-shaped registry string.
4. **Tier runner is provider-aware at preflight only.** The Tier 0 probe branches per provider with **[v2]** a real capability check (not just `docker info`); Tiers 1–6 are oblivious.
5. **No new test files in this plan.** Generalize the harness; reuse the tree.

## 2. Decision Drivers (top 3)

1. **Surface-minimum changes to the live tree.** ~40 existing test files in `live_e2e_test/sandbox/**` continue to pass unchanged under both providers. Touching any of them is a regression risk that scales with file count.
2. **Cycle time on Docker locally.** Daytona pays ~7s sandbox bring-up + remote round-trip per `raw_exec`. Docker brings up faster (~1–2s) and execs locally. The plan must let developers iterate quickly under Docker on Linux. **[v2] Honest tradeoff caveat:** the single-suite approach forces Docker to pay the same `_reset_workspace` git-init-or-reset cost (~200 ms per test) and bundle-upload sequencing (~5 s bring-up) that Daytona persistent sandboxes need. Optional follow-up: pre-bake the runtime bundle into the Docker image to recover the bring-up cost.
3. **Preflight realism per provider.** Daytona's tier 0 is HTTP health on `localhost:3000/api`. Docker's tier 0 must be a real capability probe inside the configured image — daemon up, image present, `git` + `/testbed` + `unshare -Urm` all working — so misconfiguration fails at probe time, not deep inside a test.

## 3. Viable Options

### Axis A — Where the provider switch is wired

| Option | Mechanism | Verdict |
|---|---|---|
| **A.1 `EOS_SANDBOX_PROVIDER` env var only** | Reuse the dispatcher; `live_sandbox` calls `bootstrap_sandbox_provider()`. Tests inherit. | **RECOMMENDED.** |
| **A.2 New `--provider` CLI flag on `run_tiered.py`** | Tier runner accepts `--provider docker\|daytona`, exports the env var for child pytest. | Land as sugar over A.1. |
| **A.3 Per-test parametrize** | Doubles suite runtime; touches every fixture. | **Reject.** |

**Recommended:** A.1 + A.2.

### Axis B — Image resolution shape

| Option | Mechanism | Verdict |
|---|---|---|
| **B.1 Single `default_image` setting, provider-interpreted** | Settings stays one string; provider interprets. Footgun: Daytona-string passed to Docker triggers pull-timeout. | Reject as default. |
| **[v2] B.2 `EOS_LIVE_E2E_IMAGE` env var, fallback gated by provider** | Harness reads `EOS_LIVE_E2E_IMAGE` first. **Daytona only**: falls back to `settings.sandbox.default_image`. **Docker**: missing env var is hard skip with clear message. | **RECOMMENDED.** |
| **B.3 Provider-keyed settings sub-fields** | `docker_default_image` / `daytona_default_image`. Cleanest, but settings-schema churn. | Reject; revisit if `EOS_LIVE_E2E_IMAGE` proves insufficient. |

**Recommended:** B.2 [v2-amended].

### Axis C — Tier preflight per provider

| Option | Mechanism | Verdict |
|---|---|---|
| **[v2] C.1 Branch inside `tier0_health.py` with real capability probe** | `probe_tier0()` dispatches on provider; daytona → existing HTTP probe; docker → `docker info` + `docker image inspect $EOS_LIVE_E2E_IMAGE` + `docker run --rm` capability probe; emit `EOS_DOCKER_PRIVILEGED` value in notes. | **RECOMMENDED.** |
| C.2 Skip tier 0 for Docker | Loses cascade-abort signal. | Reject. |
| C.3 Per-provider tier-0 entries in `tiers.toml` | Doubles maintenance. | Reject. |

**Recommended:** C.1 [v2-amended].

### Axis D — Excluding tier 7 + real_agent paths

| Option | Mechanism | Verdict |
|---|---|---|
| **D.1 Delete tier 7 from `tiers.toml`** | The path doesn't exist. | **RECOMMENDED.** |
| D.2 Tag tier 7 as `real_agent` and skip by default | Designs infra for a tier whose target path doesn't exist. | Reject for now. |

**Recommended:** D.1.

## 4. File-by-file work breakdown

### Step 0 — Pre-flight image verification (operator procedure)
- **Action:** On the target Linux host, run `docker info` and `docker image inspect $EOS_LIVE_E2E_IMAGE`. Confirm the image satisfies the §0.1 preconditions (root + CAP_SYS_ADMIN via run flags, `git`, `/testbed` writable, runtime bundle marker location).
- **Decision rule:** All passes → proceed. `docker info` fails → start docker. Image missing capabilities → bake them in (image authoring is out of scope; see §8).
- Step 0 is a one-time operator check **plus** the durable equivalent codified in Step 3 [v2] (tier-0 probe).

### Step 1 — Provider-generic `live_sandbox` fixture
- **Edit** `backend/tests/live_e2e_test/sandbox/_harness/sandbox_fixture.py`:
  - Replace `from sandbox.provider.daytona.bootstrap import bootstrap_daytona_provider` with `from sandbox.provider.bootstrap import bootstrap_sandbox_provider`.
  - Replace the `bootstrap_daytona_provider()` call at `_bring_up_sandbox` with `bootstrap_sandbox_provider()`.
  - **[v2] Image resolution helper** with provider-gated fallback:
    ```python
    def _resolve_live_image(provider_name: str) -> str:
        explicit = (os.environ.get("EOS_LIVE_E2E_IMAGE") or "").strip()
        if explicit:
            return explicit
        if provider_name == "daytona":
            return settings.sandbox.default_image.strip()
        pytest.skip(
            f"live test under EOS_SANDBOX_PROVIDER={provider_name} requires "
            "EOS_LIVE_E2E_IMAGE to be set to a locally-available image tag "
            "with git, /testbed, and the runtime bundle marker."
        )
    ```
  - Read `provider_name = get_default_provider().name` after the bootstrap call.
  - No change to `provider.create(...)`, `register_adapter(...)`, `setup_after_create(...)`.
- **Verify:** `EOS_SANDBOX_PROVIDER=daytona pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase00_smoke.py` passes (regression). `EOS_SANDBOX_PROVIDER=docker EOS_LIVE_E2E_IMAGE=<tag> pytest …test_phase00_smoke.py` passes on Linux+Docker (new).

### Step 2 — [v2] Conftest marker docstring honesty
- **Edit** `backend/tests/live_e2e_test/conftest.py:42`:
  - Replace `marker live: marks live Daytona-backed end-to-end tests (opt-in via env)` with `marker live: live provider-backed end-to-end tests (gated by EOS_SANDBOX_PROVIDER; opt-in by directory)`.
  - Same one-line refresh for `live_e2e_daytona` if its docstring asserts provider identity (verify on edit and align wording without changing the marker name — keep marker name for backward compat).
- **Verify:** `pytest --markers | grep -E 'live(_e2e)?'` reads honestly under both providers.

### Step 3 — [v2] Provider-aware tier 0 probe with real capability check
- **Edit** `backend/tests/live_e2e_test/_tools/tier0_health.py`:
  - `probe_tier0()` reads `os.environ.get("EOS_SANDBOX_PROVIDER")` (same dispatcher default: linux→docker, darwin→daytona, error otherwise).
  - **Daytona branch:** existing HTTP `/api/health` probe (unchanged).
  - **Docker branch:**
    1. `subprocess.run(["docker", "info"], …)` — daemon up.
    2. `subprocess.run(["docker", "image", "inspect", os.environ["EOS_LIVE_E2E_IMAGE"]], …)` — image present locally (pre-empts implicit pull).
    3. **Capability probe** — `subprocess.run(["docker", "run", "--rm", *_docker_run_flags(), os.environ["EOS_LIVE_E2E_IMAGE"], "sh", "-c", "command -v git && test -w /testbed && unshare -Urm true"], …)` where `_docker_run_flags()` returns the same `host_config_kwargs()`-equivalent CLI flags used by `DockerProviderAdapter.create` (DEFAULT_RUN_FLAGS or `--privileged` per `EOS_DOCKER_PRIVILEGED`).
    4. Notes: include `EOS_DOCKER_PRIVILEGED` value in the `Tier0Result.detail` so artifacts diff cleanly between privileged/unprivileged runs.
  - **Unknown-provider branch:** `Tier0Result(passed=False, detail="unsupported provider for tier 0 probe")`.
- **Verify:** `python -m backend.tests.live_e2e_test._tools.run_tiered --tiers 0` exits 0 on healthy Linux+Docker with `EOS_LIVE_E2E_IMAGE` set; exits non-zero with a clear detail message when daemon down / image missing / git missing / /testbed read-only / `unshare -Urm` blocked.

### Step 4 — Tier runner `--provider` sugar + tier 7 removal
- **Edit** `backend/tests/live_e2e_test/_tools/run_tiered.py`:
  - Add `--provider {docker,daytona}` flag; when set, the runner exports `EOS_SANDBOX_PROVIDER=<value>` into the child pytest environment (sugar over the env var).
  - When `--provider` is unset, the runner inherits the existing env (no-op).
  - Print the resolved provider as the first line of the run summary and include it as `provider` in the per-tier artifact JSON.
- **Edit** `backend/tests/live_e2e_test/_tools/tiers.toml`:
  - **[v2] Single concrete edit:** delete the `[[tier]] id = 7 name = "sweevo_mock_framework"` block. **Tier 5's `cascade = "abort_eq", cascade_target = 6` stays exactly as written** — tier 6 still exists, the cascade target is unchanged. (v1 had a self-contradictory paragraph on this; v2 is one sentence: delete tier 7, change nothing else.)
  - Update the file's header comment from "Tiers 0–7" to "Tiers 0–6: tier 0 is provider-aware preflight, tiers 1–6 are pure pytest".
- **Verify:** `python -m backend.tests.live_e2e_test._tools.run_tiered --provider docker --tiers 0-6` enumerates 7 tiers (0..6 inclusive), runs tier 0 docker probe, tiers 1–6 pytest, prints `provider=docker` in summary and JSON artifacts.

### Step 5 — Documentation + scripts
- **Edit** `backend/tests/live_e2e_test/sandbox/README.md`:
  - "Running on Docker" section: env var matrix (`EOS_SANDBOX_PROVIDER=docker`, `EOS_LIVE_E2E_IMAGE`, optional `EOS_DOCKER_PRIVILEGED`), Linux-only caveat, image-bake requirements.
- **New** `backend/scripts/run_live_e2e_docker.sh`:
  - Bails on non-Linux; sets `EOS_SANDBOX_PROVIDER=docker`; calls `python -m backend.tests.live_e2e_test._tools.run_tiered --provider docker --tiers 0-6` and uploads artifacts.

## 5. Test Plan

### 5.1 Unit
- New `backend/tests/unit_test/test_sandbox/test_provider/test_live_harness_provider_resolution.py`: parametrize over `(env, provider, expected_outcome)`:
  - `EOS_LIVE_E2E_IMAGE=foo`, provider=docker → returns `"foo"`.
  - `EOS_LIVE_E2E_IMAGE=foo`, provider=daytona → returns `"foo"`.
  - Unset, provider=daytona, `settings.sandbox.default_image="bar"` → returns `"bar"`.
  - Unset, provider=docker → `pytest.skip` (asserted via `pytest.raises(_pytest.outcomes.Skipped)`).
  - Unset, provider=daytona, settings empty → `pytest.skip`.

### 5.2 Integration
- **[v2] Daytona regression baseline (must run BEFORE Step 1 edit):**
  - `EOS_SANDBOX_PROVIDER=daytona pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase00_smoke.py -v` against `main`. Capture stdout + JUnit XML as `daytona-baseline-pre-change.xml` in the planning dir.
  - This artifact is the regression reference for criterion 6.
- **Post-change Daytona regression:** same command after Step 1 lands → must match the baseline pass/fail set.
- **Post-change Docker integration:** `EOS_SANDBOX_PROVIDER=docker EOS_LIVE_E2E_IMAGE=<tag> pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase00_smoke.py -v` on Linux+Docker → passes.

### 5.3 E2E
- `bash backend/scripts/run_live_e2e_docker.sh` on Linux CI runner runs tiers 0–6 end to end. Acceptance:
  - Tier 0 reports docker daemon healthy + image present + capability probe (`git` + `/testbed` writable + `unshare -Urm`) passing + `EOS_DOCKER_PRIVILEGED` value recorded.
  - Tier 1 smoke passes.
  - Tiers 2–6 cascade outcomes match the Daytona baseline modulo provider-specific failures captured in the run report.
- A second invocation with `--provider daytona` on the same host (creds present) → tier 1 passes; tiers 2–6 cascade per existing budgets.

### 5.4 Observability
- Tier-runner summary prefixes each `TierOutcome` log with `provider=<name>`.
- Per-tier artifact JSON gains top-level `provider` and (when applicable) `eos_docker_privileged` fields.
- Tier-0 docker probe records each of the four sub-checks (`docker info`, `image inspect`, capability probe stdout, `EOS_DOCKER_PRIVILEGED` value) as separate keys for diff-friendly artifacts.

## 6. Acceptance Criteria

1. `_harness/sandbox_fixture.py` imports no provider-specific module: `rg 'provider\.(daytona|docker)' backend/tests/live_e2e_test/` returns zero hits.
2. `pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase00_smoke.py` passes under both `EOS_SANDBOX_PROVIDER=daytona` (creds + default image) and `EOS_SANDBOX_PROVIDER=docker` (`EOS_LIVE_E2E_IMAGE` + daemon + capabilities) on a Linux host.
3. `python -m backend.tests.live_e2e_test._tools.run_tiered --provider docker --tiers 0-6` on Linux + Docker completes tier 0 (docker info + image present + capability probe), then runs tiers 1–6; per-tier cascade behavior matches the Daytona baseline modulo documented provider-specific failures.
4. Tier 7 (`sweevo_mock_framework`) is removed from `tiers.toml`; the runner enumerates exactly 7 tiers (0–6). Real_agent paths under `task_center_runner` are untouched.
5. README documents the Docker invocation; `backend/scripts/run_live_e2e_docker.sh` exists, is executable, bails cleanly on non-Linux.
6. **[v2] Daytona regression gate with explicit baseline:** `EOS_SANDBOX_PROVIDER=daytona pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase00_smoke.py` after Step 1 lands matches the pre-change baseline (`daytona-baseline-pre-change.xml`) pass/fail set. Captured as `daytona-baseline-post-change.xml`.
7. **[v2] Conftest marker docstring** at `conftest.py:42` no longer asserts "Daytona-backed"; new wording reflects provider-pluggable gating.
8. **[v2] Tier 0 docker branch** runs all four sub-checks (info / image inspect / capability probe / `EOS_DOCKER_PRIVILEGED` capture) and reports each. Failure of any sub-check produces a `Tier0Result.passed=False` with a clear detail string naming the specific sub-check.

## 7. Rollback

- Step 1 is the only behavioral change; revert that commit and the suite returns to the Daytona-hard-coded path. Steps 2–5 are additive (docs, tier-runner sugar, new env var, new probe branch) and independently revertible.
- No DB migration, no agent profile change, no test rewrite.

## 8. Out of Scope (explicit non-goals)

- Adding Docker variants of existing tests. One tree, two providers — that's the entire point.
- `task_center_runner` real_agent code paths or unit tests (`test_real_agent_*.py`).
- Modifying `sandbox.host.bootstrap.setup_after_create` or any `sandbox.layer_stack/overlay/occ/daemon` internals.
- New tier (8+) for Docker-specific perf measurement. Docker-vs-Daytona delta study is a separate plan.
- Image baking — operator supplies an image satisfying §0.1 preconditions. **Optional follow-up** [v2]: bake the runtime bundle into the Docker image to recover bring-up cost.
- Single-pytest-run parametrize over providers. Sequential per-provider runs + artifact diff is the comparison surface.
- Provider-keyed settings sub-fields (B.3). Revisit if `EOS_LIVE_E2E_IMAGE` proves insufficient in practice.

---

## ADR (decision record)

**Decision:** Generalize `live_e2e_test/sandbox/` to run under any provider selected by `EOS_SANDBOX_PROVIDER`, by flipping a single line in the session-scoped `live_sandbox` fixture, branching the tier-0 health probe per provider **with a real in-image capability probe under Docker**, gating the image-fallback to Daytona only, and removing dead tier 7. Run Docker via this seam now.

**Drivers:**
1. Surface-minimum changes to the live test tree (one fixture file + one tier-config file + one probe file + one conftest line).
2. Reuse the existing `bootstrap_sandbox_provider()` dispatcher.
3. Realistic per-provider preflight — fail loud at tier 0 when environment doesn't satisfy §0.1 preconditions.

**Alternatives considered:**
- Per-test parametrize: rejected (suite-doubling, fixture churn).
- New CLI flag as source of truth: rejected (races env var).
- Provider-keyed settings sub-fields for image: rejected for now (overkill; revisit if env-var fallback proves insufficient).
- Keep tier 7 with a `real_agent` tag and skip default: rejected (target path doesn't exist).
- **[v2] Two separate suites (`daytona_sandbox/` + `docker_sandbox/`)** — rejected. The single-suite approach explicitly buys cross-provider conformance: any divergence is the bug we want to find. The cost is paid in `_reset_workspace` + bundle-upload overhead under Docker; that cost is acceptable for CI and recoverable via the optional bundle-prebake follow-up.

**Why chosen:**
- One env var + one new env var matches the idiom developers already use elsewhere.
- Tier 0 docker branch is the natural place to encode §0.1 preconditions — it fires before tier 1 and produces a clear cascade-abort signal.
- Image-fallback gated on `provider == daytona` removes the silent-cross-feed footgun from v1.
- Test files in `live_e2e_test/sandbox/**` change zero lines.

**Consequences:**
- New maintenance: `EOS_LIVE_E2E_IMAGE` is now part of the operator mental model. Mitigation: tier 0 fails loud when unset on Docker.
- Tier 0 has a small provider switch. Mitigation: unit-tested resolution helper + identical failure shape across branches.
- Honest cost under Docker: `_reset_workspace` git dance (~200 ms × ~40 tests = ~8 s) + bundle-upload sequencing (~5 s bring-up) carry over from Daytona. Optional follow-up: pre-bake runtime bundle.
- Dead tier 7 removed; re-adding `backend/src/live_e2e/tests/sweevo/` later means re-adding the tier entry intentionally.

**Follow-ups:**
- Optional: bake the runtime bundle into the Docker image to drop bring-up to ~1–2 s.
- If Docker p95 exec latency materially diverges from Daytona on the same workload, add a comparison harness (separate plan).
- If real_agent tiers reappear under `backend/src/live_e2e/tests/sweevo/`, restore the tier 7 entry plus a `--exclude-tag real_agent` mechanism in `run_tiered.py`.
- Investigate the conftest import fence: `conftest.py:30` forbids `sandbox.occ` imports but `occ/test_routing.py` imports `sandbox.occ.changeset`. Either the fence is being bypassed today (latent integrity issue) or the test is currently broken on main. Orthogonal to this plan but worth a separate ticket.
