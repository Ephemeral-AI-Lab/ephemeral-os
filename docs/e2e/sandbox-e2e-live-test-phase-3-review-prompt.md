# `sandbox-e2e-live-test` — Phase 3 Adversarial Review Prompt

A **multi-agent / dynamic-workflow** prompt for an adversarial review of the
**Phase 3, Stage 1** implementation across three axes — **completeness**,
**correctness**, **cleanliness**. The implementation is already build-to-green;
your job is to **find what is missing, wrong, or unclean**, not to praise it.
Default to skepticism: assume every untested path hides a defect until you have
reproduced or argued it safe.

---

## 0. How to run this (orchestration)

Drive this as a fan-out, not a single read-through. Recommended shape (adapt to
the Workflow tool or manual subagents):

1. **Find (parallel, by lane).** Spawn one finder per *lane* in §4 (and one
   dedicated **deviation auditor** for §5). Each finder reads only its spec
   section(s) + the live file(s) it owns and emits structured candidate findings
   (schema in §7). Finders are blind to each other.
2. **Verify (adversarial, per finding).** For every candidate, spawn 2–3
   skeptics whose job is to **refute** it — show the spec permits it, the code
   already handles it, or the failing scenario cannot occur. Use *diverse
   lenses*: (a) spec-conformance, (b) runtime-behavior/repro, (c)
   convention/clippy. Keep a finding only if it survives a majority.
3. **Critic + loop.** A completeness critic asks "*which lane, file, spec
   section, or artifact field went uncovered, and which surviving claim is still
   unreproduced?*" Spawn follow-up finders for each gap. Repeat find→verify until
   two consecutive rounds surface nothing new (loop-until-dry).
4. **Synthesize.** Rank surviving findings by severity (§8); de-dupe by
   `(file, line, claim)`; emit the §7 report. Separate **confirmed defects**
   from **judgment calls** (deviations that are defensible but worth a decision).

A reference Workflow script skeleton is in Appendix A.

---

## 1. Source of truth (read before reviewing)

- **Spec (author):** `docs/e2e/sandbox-e2e-live-test-phase-3-spec.md` — schemas
  (§4–§8), pipeline (§3), cleanup (§7), preflight (§3.2), the **Anchor Ledger
  (§10)**, acceptance (§11), conventions (§12). Do **not** re-derive design from
  this file; verify the code against it.
- **Parent design:** `docs/e2e/sandbox-e2e-live-test-spec.md`.
- **Conventions (binding):** root `CLAUDE.md` (SRP/prefer-less; **no inline
  comments in `src/`**; **no test code in `src/`**; workspace deps via
  `dep.workspace = true`; clippy lints) and `README.md` (boundary law).
- **The diff under review:** the Phase-3 commits on the `sandbox-e2e-live-test`
  crate (range ending at the current HEAD; `git log --oneline` shows
  `Add live E2E run config and reporting` → `Split preflight into environment
  and probe checks`). Review the **working-tree state**, which equals HEAD.

---

## 2. Scope — files in play

`△` edited, `←NEW` created this phase. Review **only** these:

```
crates/sandbox-e2e-live-test/
  Cargo.toml                 △  +clap, +sha2, +time (no tokio/uuid)
  src/lib.rs                 △  +pub mod cleanup;
  src/config.rs              △  ManifestConfig (renamed) + orchestrator RunConfig/Args/enums/run_id
  src/assertion.rs           △  thread-local assertion counter
  src/report.rs              △  write_exchange (kept) + result/run-manifest/summary writers + build_tests
  src/cleanup.rs             ←NEW  RunGuard + CleanupReport
  src/fixtures.rs            △  ManifestConfig; Sandbox.started/test_name; result.json in Drop
  src/bin/eos-e2e.rs         △  full orchestrator + preflight / --clean-run / --rerun-failed-from
```

**Out of scope — do not file findings against:** `tests/**`, `build.rs` (must be
unchanged — *do* flag if it changed), Phase 4 observability, Stage 2 runtime
leaves, the namespace-execution crate (concurrent unrelated work), and the
**Linux+Docker+real-runtime green run** (#9–#12) which cannot execute on a
non-Linux host — but you **must** still statically audit those code paths.

---

## 3. Ground rules (adversarial discipline)

- **Every finding cites evidence:** `file:line` in the live code **and** the
  governing spec anchor (`§n` / ledger row), **plus** a concrete trigger — an
  input, env, or call sequence that produces the wrong observable. "Looks risky"
  without a scenario is not a finding.
- **Reproduce when you can.** `cargo build/clippy/fmt/test -p
  sandbox-e2e-live-test`, run `target/debug/eos-e2e …`, craft a `run-manifest`
  and set `EOS_E2E_RUN_ROOT`, hand-write a `summary.json` and feed
  `--rerun-failed-from`, etc. Prefer a reproduction over an argument.
- **Black-box law:** the crate may only drive `sandbox-cli` and read artifacts
  under `{run_root}`. Flag any internal-crate dep, `*_for_test` hook, or injected
  runtime.
- **Pass/fail gate law:** the run verdict is the **`cargo test` process exit
  code**, never libtest stdout. Flag any stdout parsing used as a gate.
- **No spec-fabrication:** if the spec is silent, say so and judge against
  CLAUDE.md + least-surprise; don't invent a requirement to manufacture a defect.

---

## 4. Review lanes (assign one finder each)

### Lane A — Completeness vs spec deliverables
Confirm each spec artifact/behavior exists **and is wired**:
- `ManifestConfig` rename keeps `from_env`/`Manifest`/`SUPPORTED_SCHEMA_VERSION=1`;
  sole consumer `fixtures.rs` updated; test side compiles unchanged otherwise (§4.1).
- Orchestrator `RunConfig` + clap `Args` + enums `TestSelection`/`CleanupPolicy`/
  `BuildSource` with the §4.2/§4.3 fields, flags, envs, defaults, validations.
- `run_id` derivation (§4.4): verbatim `--run-id` (charset, no `:`) **else**
  `r{ts}-{sha256(HEAD‖test_manifest_hash‖salt)[..8]}`; `ts` is a manual
  `format!` (no `time` fmt feature); `EOS_E2E_RUN_CLOCK` pin; empty-salt default.
- Artifact **field-by-field** parity: `result.json` (§5.2), `run-manifest.json`
  (§5.1), `summary.json` + `tests[]` (§5.3), `timing` (§8), `summary.cleanup`
  (§7.3). Every artifact carries `schema_version`. **No observability artifact.**
- Subcommands/flags: `run` (default), `preflight`, `--clean-run`,
  `--rerun-failed-from`. `STAGE1_DEFAULT_TARGET` present and is the *only*
  stage-aware line. `counts.skipped` always 0.

### Lane B — Correctness: config & run_id
- Precedence is truly **flag > env > default** for every field (esp.
  `max_parallel`: `--max-parallel` > `EOS_E2E_MAX_PARALLEL` >
  `available_parallelism().min(8)`, `≥1`).
- `test_manifest_hash` matches "the same leaf set `build.rs` discovers" and is
  stable across reruns / unaffected by file *contents* (§4.4). Probe the
  relative-path base (`env!("CARGO_MANIFEST_DIR")`) vs build.rs's runtime dir.
- `run_id` is byte-stable with `EOS_E2E_RUN_CLOCK` + fixed `EOS_E2E_RUN_SALT` +
  fixed HEAD; charset always within `[A-Za-z0-9._-]`.

### Lane C — Correctness: report writers & aggregation
- `build_tests` globs `reports/*/result.json`; **missing ⇒ `errored`** with id
  from the dir name (§5.3). Scrutinize the *unparsable-but-present* case — is
  treating it as `errored` faithful or an over-reach?
- `summary.status` = `passed` iff cargo-exit 0 **AND** every `result.json`
  passed; `error` if cargo could not run; else `failed`. Check the **vacuous
  zero-tests** case (`all_passed` over empty).
- `failed_tests[]` round-trips into `--rerun-failed-from` filters.
- `u128` durations serialize (no `arbitrary_precision`); `PathBuf`/`Path`
  serialize as JSON strings the reader accepts.

### Lane D — Correctness: cleanup / RunGuard
- Survivor sweep keyed on `reports/*/` dir names → idempotent `destroy_sandbox`;
  gateway **detach-only** (never stops it); `remove_dir_all` gated by policy
  (`OnSuccess` default removes on success / keeps on failure; `Always`; `Never`
  = `--keep-artifacts`).
- **Ordering (§7.3):** is `summary.json` actually produced *before*
  `remove_dir_all`? Trace `plan()` → `write_summary` → `teardown()` →
  re-fold-on-kept. Does acceptance #9 ("**produces** `summary.json`") hold on the
  success-removed path? Does #10 hold (`removed_run_root == false` when kept)?
- `Drop` idempotency (`done` flag), panic-safety, and the honest SIGKILL gap.
- `--clean-run`: idempotent on missing run_root (exit 0), exit 2 on bad charset /
  unreadable manifest; reads the socket from `run-manifest.json`.

### Lane E — Correctness: orchestrator & preflight
- Preflight 4 checks, **exact** messages (§3.2): Linux; `Docker daemon not
  reachable at $DOCKER_HOST` (is the literal `$DOCKER_HOST` intended?); image;
  probe. Check 1–3 must not require a socket; only the probe does.
- Probe (§3.2.1): scratch temp workspace created+removed; `create_sandbox`
  reaches the runtime trait; `runtime is not configured` ⇒ exit 2 (long message
  naming `gateway/main.rs:94-146`); success ⇒ capture `/id` and `destroy`;
  other ⇒ exit 2. Is substring-detection over the whole response/stderr robust
  but not falsely-triggering?
- Exit codes (§3.1 step 7): **0** iff cargo==0 ∧ all passed; **1** run failed;
  **2** for preflight/manifest-IO/`await_ready`/cargo-couldn't-spawn/config error.
- `EOS_E2E_RUN_ROOT` is exported to the **cargo child only** (not the orchestrator
  process); `--test-threads={max_parallel}`; `STAGE1_DEFAULT_TARGET` applied.
- Phase A skipped (attach-only ⇒ all `build.*_ms = 0`).

### Lane F — Correctness: clap surface
- `eos-e2e preflight --gateway-socket X` actually routes `X` into the resolved
  socket (global-args-through-subcommand really works — verify at runtime, not
  just `--help`). Default (no subcommand) = `run`. `--cleanup on-success`
  (kebab ValueEnum) parses. `--test-names` collects. Bad `--run-id` rejected.

### Lane G — Cleanliness & conventions
- **SRP** per file; is `report.rs` depending on `config::RunConfig` /
  `cleanup::CleanupReport` acceptable coupling or a smell?
- **Prefer less:** unused-but-pub `RunConfig.gateway_ready_timeout`; duplicated
  constants (`CLI_BIN`, the `run-manifest.json` name across config/report/bin,
  scattered `*_SCHEMA_VERSION`); two `git rev-parse` calls per run; hand-rolled
  hex in a loop.
- **No inline comments in `src/`** (only `///`/`//!`); **no test code in
  `src/`** (the thread-local + result.json path must be production, no
  `#[cfg(test)]`); workspace deps only; **clippy `--all-targets` clean** (no new
  `unwrap_used`/`dbg_macro`/`undocumented_unsafe_blocks`); `fmt --check` clean.
- Doc-comment accuracy (do they describe what the code does?).

---

## 5. Known-deviation hotlist (attack these explicitly)

The author made these calls. For each: is it a **defect**, an **acceptable
prefer-less/SRP refinement**, or a **spec violation** needing reversal? Argue with
evidence.

1. **`RunGuard.gateway_socket` field dropped** (spec §7.1 lists it). The
   `CliClient` holds the socket; detach is a no-op. Defensible or contract loss?
2. **`RunGuard::plan()` added** beyond the spec's `new/set_succeeded/teardown/Drop`
   API, to write `summary.cleanup` before removal. Justified by §7.3 or scope creep?
3. **`$DOCKER_HOST` emitted literally** (spec uses `{os}`/`{image}` for
   substitution but `$DOCKER_HOST` with `$`). Correct reading or a UX bug?
4. **Preflight split / lazy socket:** `RunConfig::resolve` requires the socket,
   but the `preflight` subcommand resolves image-only for checks 1–3 and the
   socket only for the probe. Faithful to #5/#6/#7 or a divergence?
5. **`test_name` resolved at provision (stored field)** vs capture-at-drop. Extra
   field vs robustness — prefer-less violation?
6. **Summary written-then-folded** vs spec's literal "write summary **before**
   `remove_dir_all`": on the success-removed path the on-disk `summary.json` is
   deleted with the tree and only echoed to stdout. Does this satisfy "produces
   summary.json" (#9) and the §7.3 "operator reads captured stdout" intent?
7. **`run_id` derived before preflight** in the run pipeline (so `git` runs before
   the OS check). Observable harm on a non-git or non-Linux host?
8. **`build_tests` treats unparsable `result.json` as `errored`** (spec says
   *missing* ⇒ errored). Over-reach or sensible hardening?

---

## 6. Reproductions worth running

- `cargo build/clippy --all-targets/fmt --check/test -p sandbox-e2e-live-test`
  (test with `EOS_E2E_RUN_ROOT` **unset** ⇒ every leaf skips, **nothing
  written**, no stray run-root base).
- `eos-e2e preflight` and `eos-e2e --gateway-socket … ` off-Linux ⇒ exit 2 +
  exact OS message; `--clean-run bad:id` ⇒ 2; `--clean-run missing` ⇒ 0;
  `--run-id bad:id` ⇒ 2.
- **Manifest contract:** write the superset `run-manifest.json` your writer
  emits, point `EOS_E2E_RUN_ROOT` at it, run one manager leaf — it must parse
  past `ManifestConfig` (reaching `await_ready`), proving extras are ignored and
  the four typed fields round-trip.
- **Rerun parsing:** hand-craft a `summary.json` with `failed_tests[]` and
  confirm `--rerun-failed-from` turns them into libtest filters.

---

## 7. Finding output schema

Emit confirmed findings as a ranked list; each:

```jsonc
{
  "id": "C-03",
  "dimension": "correctness | completeness | cleanliness",
  "severity": "blocker | major | minor | nit",
  "title": "one line",
  "file": "src/bin/eos-e2e.rs:142",
  "spec_anchor": "§3.1 step 7 / ledger row",
  "scenario": "exact trigger (input/env/call sequence)",
  "evidence": "what was observed / command output / quoted code",
  "why_wrong": "the rule or contract it violates",
  "fix": "smallest change that resolves it",
  "verifier_verdict": "confirmed (k/n skeptics failed to refute) | judgment-call",
  "confidence": "high | medium | low"
}
```

Close with: **confirmed defects** (ranked), **judgment calls** (the §5
deviations with a recommendation each), and a **coverage statement** (which
lanes/files/spec-sections/artifact-fields were actually checked, and what
remains unverifiable without a Linux+Docker real-runtime gateway).

---

## 8. Severity rubric

- **blocker** — breaks acceptance #1–#8, the manifest↔reader contract, the
  pass/fail gate, the skip path, or a boundary/fence; or won't build/clippy/fmt.
- **major** — wrong artifact field/shape, wrong exit code, broken precedence,
  non-deterministic `run_id`, cleanup that removes/keeps against policy.
- **minor** — imperfect message, harmless ordering, defensible-but-suboptimal
  deviation.
- **nit** — naming, duplication, doc wording.

A claim that cannot be tied to a triggering scenario is **downgraded or dropped**,
not filed as major.

---

## 9. Done criteria

Two consecutive dry finder rounds; every surviving finding adversarially
verified (or explicitly marked judgment-call); each §5 deviation has a verdict;
coverage statement names the unverifiable Linux-gated surface. Output is the §7
report — nothing else.

---

## Appendix A — Reference Workflow skeleton

```js
export const meta = {
  name: 'phase3-adversarial-review',
  description: 'Adversarial review of sandbox-e2e-live-test Phase 3 (complete/correct/clean)',
  phases: [{ title: 'Find' }, { title: 'Verify' }, { title: 'Critic' }, { title: 'Synthesize' }],
}

const LANES = [
  { key: 'A-completeness', prompt: '<Lane A from §4 + §1 source-of-truth + §3 rules>' },
  { key: 'B-config-runid', prompt: '<Lane B>' },
  { key: 'C-report-agg',   prompt: '<Lane C>' },
  { key: 'D-cleanup',      prompt: '<Lane D>' },
  { key: 'E-orch-preflight', prompt: '<Lane E>' },
  { key: 'F-clap',         prompt: '<Lane F>' },
  { key: 'G-cleanliness',  prompt: '<Lane G>' },
  { key: 'X-deviations',   prompt: '<§5 deviation hotlist; one verdict each>' },
]
const FINDING = { /* §7 schema as JSON Schema */ }
const VERDICT = { /* { refuted: bool, reason: string, confidence } */ }

// Find → adversarially verify each finding, pipelined (no barrier between lanes).
const reviewed = await pipeline(
  LANES,
  l => agent(l.prompt, { label: `find:${l.key}`, phase: 'Find', schema: { /* {findings:[FINDING]} */ } }),
  (res, lane) => parallel((res?.findings ?? []).map(f => () =>
    parallel(['spec-conformance', 'runtime-repro', 'convention'].map(lens => () =>
      agent(`Refute this ${lane.key} finding via the ${lens} lens. Default refuted=true if no concrete trigger. Finding: ${JSON.stringify(f)}`,
            { label: `verify:${f.id ?? lane.key}`, phase: 'Verify', schema: VERDICT })))
      .then(vs => ({ finding: f, survives: vs.filter(Boolean).filter(v => !v.refuted).length >= 2 }))))
)
const survivors = reviewed.flat().filter(Boolean).filter(r => r.survives).map(r => r.finding)

// Completeness critic — name uncovered lanes/files/fields, then synthesize.
const gaps = await agent(`Given these survivors, name uncovered lanes/files/spec-sections/artifact-fields and unreproduced claims: ${JSON.stringify(survivors)}`,
                         { phase: 'Critic', schema: { /* {gaps:[...]} */ } })
return await agent(`Rank/de-dupe into the §7 report. Separate confirmed defects from judgment calls; add coverage statement. Survivors: ${JSON.stringify(survivors)} Gaps: ${JSON.stringify(gaps)}`,
                   { phase: 'Synthesize' })
```

> Scale finder/skeptic counts to the desired thoroughness; add a loop-until-dry
> outer loop (2 dry rounds) for exhaustive coverage. Keep verifiers **adversarial**
> — their default is *refuted* unless a concrete trigger is shown.
