# Architect Review v1 — Verdict: ITERATE

Review of `.planning/ralplan-live-e2e-providers/PLAN_v1.md`.

## Headline finding

PLAN_v1 is 80% there structurally — the harness genuinely has a single provider seam at `sandbox_fixture.py:194`, and `bootstrap_sandbox_provider()` already exists. **But "test files change zero lines" is true at the line level and misleading at the behavioral level.** Daytona-image-specific preconditions (root + CAP_SYS_ADMIN, `git`, `/testbed`, `unshare -Urm`) are silent guarantees today; under Docker they become operator responsibility, and the plan offloads preflight to docs instead of automating it.

## Four bounded amendments required

1. **Tier-0 docker branch must do a real capability probe**, not just `docker info` + `docker image inspect`. Run `docker run --rm <flags> $EOS_LIVE_E2E_IMAGE sh -c 'command -v git && test -w /testbed && unshare -Urm true'` with the same host_config kwargs the adapter uses. Record `EOS_DOCKER_PRIVILEGED` in notes.

2. **Image fallback must be provider-conditional.** Falling back to `settings.sandbox.default_image` under Docker silently feeds Docker a Daytona-shaped registry string (e.g., `registry:6000/daytona/...`), producing a ~30s `docker pull` timeout that masquerades as a daemon problem. Restrict the fallback to `provider == daytona`; under Docker, missing `EOS_LIVE_E2E_IMAGE` is a hard fixture-skip with a clear message.

3. **Update the `live` marker docstring** in `conftest.py:42` from "Daytona-backed" to "provider-backed (gated by `EOS_SANDBOX_PROVIDER`)". One-line edit. Principle 5 ("no marker churn without value") is wrong here — the value is removing a now-false documentation claim.

4. **Add an explicit Daytona regression-baseline artifact** to §5.2. Capture `pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase00_smoke.py` output against `main` *before* the fixture edit, and assert the post-change run matches the same pass/fail set. Criterion 6 ("no regression") needs evidence, not assertion.

Minor: tighten §Step 4's self-contradictory paragraph about tier 5's cascade — the actual change is "delete tier 7, leave everything else alone", one sentence.

## Strongest antithesis (steelman)

> Don't make the harness provider-pluggable. Keep `live_e2e_test/sandbox/` as the Daytona suite and copy-then-prune to `live_e2e_test/docker_sandbox/`. Two harnesses, two fixtures — ~30% smaller Docker suite, no `_reset_workspace` git dance, no bundle-upload sequencing. Provider differences become explicit instead of papered over.

**Rebuttal:** The single-suite approach buys cross-provider conformance — any divergence between Daytona and Docker under the same test is exactly the bug worth finding. Doubling the tree to optimize the local-Docker fast path sacrifices that property.

## Tradeoff tension surfaced

One suite = guaranteed conformance + acceptable performance tax (the ~8 s `_reset_workspace` overhead × ~40 tests carries to Docker; bundle-upload bring-up costs ~5 s). PLAN_v1 implicitly takes side A without naming the cost. v2 should name it honestly and document the optional bundle-prebake follow-up.

## Verdict

**ITERATE.** Four bounded amendments — should be one short revision cycle, not a re-architecture.
