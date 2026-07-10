> **Frozen historical prompt (operation-layout exempt, 2026-07-11):** Do not
> execute this prompt verbatim; its commands and paths describe the tree used
> for the completed squash verification.

/goal Implement the LayerStack Squash live-Docker test suite exactly as cataloged in docs/obsidian/ephemeral-os/implementation_plan/squash/test-case.md — all 50 cases (SMK-01…10, MED-01…20, HRD-01…20) plus the §2 measurement kit — under cli-operation-e2e-live-test/manager/management/squash/, finishing only when every case's verdict.json passes all three axes (correctness, space, time) with zero unexplained skips.

First read, fully: test-case.md (source of truth: §1 fixtures, §2 measurement, §3 budgets, §4 cases, §5 traceability, §6 order); spec.md (behavior truth); acceptance_criteria.md; impl_plan_and_progress_tracker.md (phase state + measured baselines); cli-operation-e2e-live-test/{README.md, conftest.py, manager/management/*} (harness conventions).

Prerequisite gate: the suite drives `sandbox-cli manager checkpoint_squash`. If tracker phases 4–10 are incomplete, finish them per the tracker (experiment-first, exit reviews) before the tiers that need them: commit-only cases need phase 9; ⛔gate cases need phase 10 enablement (G1–G3 green). Never stub the product to make a test pass.

Non-negotiable rules:
1. Layout: helpers.py (CLI wrappers + §1.2 fixtures), measure.py (timers, S0–S3 disk snapshots, mountinfo poller, verdict/report writers), test_squash_{smoke,medium,hard}.py, test_spec.md (catalog mirror), test-reports/<RUN_ID>/. Markers: squash + smoke|medium|hard. All ops via sandbox-cli structured JSON, no log scraping; sandbox lifecycle in conftest fixtures so teardown runs on failure.
2. Environment: export PATH="$PWD/bin:$PATH"; bin/start-sandbox-docker-gateway --rebuild-binary; image ubuntu:24.04. Assert the §1.1 preconditions once per run, hard-fail never skip. One sandbox per case; serial by default.
3. Measurement: every case writes the §2.5 artifact bundle and verdict.json — timers T_squash/T_quiesce/T_remount/T_e2e with source attr|derived|harness per §2.2, space checked against each case's stated formula and numbers (§2.3; layerstack view vs du ≤ 5%), budgets from §3 (time-only miss = SLOW, not FAIL, except ⏱ cases). SUMMARY.md + timing distributions (§2.6) generated even on abort; HRD-20 owns the baseline artifact.
4. Iteration report — MANDATORY for every live e2e run, any scope: append an entry to docs/obsidian/ephemeral-os/implementation_plan/squash/iteration-report.md with run id + date, command/tier, cases run/passed/failed/skipped, wall time, and a description (what changed, defects found, fix applied). Write it at run time, never batched; an unrecorded run doesn't count.
5. Fault induction is external only (mountinfo kill-points, docker exec, chattr, ballast files); zero test hooks in src/. Repo law: no test code in src/, no inline comments in production code, work on main, workspace deps.
6. The §1.3 teardown contract is part of every case and fails loudly: empty lease registry, no .remount-* residue, staging/ empty, strict unmount only.
7. Failure policy: diagnose from the bundle via the §2.7 playbook. Product bug → fix per spec, updating spec.md/tracker/decision log in the same change; cargo test/clippy --all-targets/fmt clean on touched crates. Never weaken an assertion, widen a budget, or add sleeps to go green; kill-point flake gets the specified retry caps + telemetry. Skips only where §5.3 allows (HRD-04 sub-cases 9/11, HRD-12 leg b, HRD-17 failure leg), recorded skipped:<reason>, visible in SUMMARY.md.
8. Order (§6): preconditions → smoke (rebuild gate) → medium (encoding cases first; rest after gates) → hard (kill/crash last in tier, HRD-20 soak final). A tier is done only when green twice consecutively.

Finish by walking §5 traceability top to bottom: all 50 verdicts pass (allowed skips only), SUMMARY.md + soak baseline checked in, iteration-report.md complete, test_spec.md mirror current, acceptance_criteria.md §9 rows and the tracker's Phase-10 checklist updated with evidence.
