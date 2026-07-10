# Crate-Core Observability — Subagent Adversarial Review Spec

> **Historical review specification (operation-layout exempt, 2026-07-11):**
> Source paths below identify the implementation reviewed at the time and are
> preserved as evidence.

Status: archived after review. Drove a parallel, evidence-gated review of the landed
crate-core slice (`crate-core-impl.md` + `crate-core-impl-expected-outcomeshape.md`).

## 0. Why this exists

The crate-core slice replaced the SQLite snapshot store with a one-file NDJSON
`span`/`event`/`sample` model + `Observer`/`Sink`/`Reader` and a daemon swap. It
compiles, tests pass, clippy is clean. **That is necessary, not sufficient.** This
review asks the harder questions a green build cannot:

- **Completeness** — does the code meet *every* primary-spec requirement, or did
  green tests paper over a missing one?
- **Correctness** — are the concurrency/serde/timing claims in the spec actually
  true of the code, under adversarial reading?
- **Cleanness** — SRP, boundary law, dead code, redaction, naming.
- **Extensibility (the priority)** — when the system grows *more varieties of
  operations and executions*, do they plug into this model without touching the
  leaf crate? Where is the friction the open/closed table (`§3.6`) promises away?

## 1. Hard rules for every agent

1. **Evidence or it didn't happen.** Every finding MUST cite concrete code:
   `crate/path/file.rs:line` plus a short quoted snippet, and (when claiming a
   spec gap) the spec section it violates. A finding without a code citation is
   discarded in synthesis.
2. **Do not assume the work is complete.** "Tests pass" is not evidence of
   correctness or completeness. Read the code paths directly; reproduce the claim
   from the source, not from the test names.
3. **Adversarial stance.** Each reviewer tries to *break* its lens, not bless it.
   Each verifier tries to *refute* the finding, not confirm it.
4. **Severity** on each finding: `blocker` (spec violated / real bug) >
   `major` (extensibility friction / correctness risk) > `minor` (cleanliness) >
   `nit`.
5. **Actionable.** Each finding ends with a concrete recommended change (file +
   what to change), not a vague concern.

## 2. Source of truth

- Primary spec: `docs/observability-rework/crate-core-impl.md` (wins on conflict).
- Shape checklist: `docs/observability-rework/crate-core-impl-expected-outcomeshape.md`.
- Code under review: `crates/sandbox-observability/src/**`,
  `crates/sandbox-daemon/src/observability/**`, the daemon config gate
  (`server/runtime.rs`, `serve.rs`, `server/dispatch.rs`), and
  `crates/sandbox-config/src/configs/observability.rs`.

## 3. Review lenses (parallel subagents)

Each lens is one reviewer subagent. It reads the real code + spec and returns structured
findings.

| # | Lens | Charge |
|---|---|---|
| L1 | **Completeness** | Walk every requirement in impl §2–§4 and checklist §1–§20. For each, cite the code that satisfies it or mark it a gap. Special attention: per-record fields intentionally absent (`sandbox`/`component`/`pid`/`exit_code`), `MAX_LINE_BYTES` single cap, `record::names`/`record::proc`, export surface (§17), dependency boundary (§18) incl. the `rusqlite` guard. |
| L2 | **Correctness — write/IO** | `Sink::append` single-`write_all` atomicity + per-line cap + the Span-nested/Sample-top-level `_truncated` asymmetry; truncation re-serializes exactly once and never drops map entries; rotation (`rotate_if_needed`) race vs concurrent append and the "reopen by re-open-per-append" claim; parent-dir-once. |
| L3 | **Correctness — read/fold** | `Reader::scan` sort stability + malformed skip across primary+rotated; `samples` counter-Δ math (emitter-tagged `_counters`, gauges untouched, first-in-window has no Δ, meta keys stripped); `trace` tree build + offsets + out-of-order resolution + parent-outside-trace handling; `events` reuses parsed records (no re-parse); window-vs-now filter. |
| L4 | **Correctness — observer/context** | thread-local `CTX` save/set/restore in `span`/`with_context`; no `RefCell` borrow held across user code or sink writes; `SpanGuard` `!Send` + drop best-effort + previous-context restore; `event` drop without ctx; `with_context(None)` runs+restores; panic-safety of `CtxRestore`; `scope` self-sets `Error` only when still `Completed`; two cloned observers share `SpanIds` + `CTX`. |
| L5 | **Extensibility — async execution sources (PRIORITY)** | Concretely: to instrument a *new* async engine (e.g. compaction/GC/prefetch), what must change? Verify the blanket `impl TerminalHook<K> for SpanRegistry<K>` + `SpanKeyAttrs` orphan story against the code; confirm `open`/`cancel` are `pub(crate)` and `launch` is the only public launch path; confirm `record`/`cancel` no-op on never-parked ids; confirm the child-context (`{trace, parent:<new id>}`) is constructible at launch for the cross-process `np-* parent=d-*` link. Flag any place a new source would have to touch the leaf. |
| L6 | **Extensibility — sync ops / events / metrics / scopes (PRIORITY)** | Validate the open/closed table (§3.6) against code: a new sync op = one `obs.span(name)`; a new event = one `obs.event(name, json!)`; a new metric = one key + (if counter) the emit-site `_counters` tag; a new scope = one `obs.sample(scope, …)`. Find every spot where adding a "variety" actually forces a leaf change, a new enum arm, a hard-coded key list, or a daemon/leaf coupling. Assess `SpanStatus` closedness, the counter-tag-at-emit design, and the daemon's `COUNTER_KEYS` const as a coupling smell. |
| L7 | **Cleanness / boundary / SRP** | Leaf deps = `serde`/`serde_json`/`thiserror` only (no rusqlite/daemon/runtime/manager/config/protocol); no inline comments in `src/`; no test code in `src/`; SRP per module; dead/forward-only API (`record::names`, `Reader::events`) justified or flagged; redaction of `attrs`/`metrics`; the `get_observability_snapshot` alias is genuinely SQLite-free; naming consistency. |

## 4. Subagent workflow

Do not use a dynamic workflow. Do not spawn subagents based on discovered findings.
Use this fixed subagent set:

1. **Review.** Run seven reviewer subagents in parallel, one for each lens L1-L7.
   Each returns `Finding[]` using the schema below.
2. **Verify.** Run seven verifier subagents in parallel, one for each reviewer
   output. Each verifier re-reads the cited code for its assigned lens and tries to
   refute every finding. If a reviewer returns no findings, its verifier returns an
   empty verdict list.
3. **Synthesize.** Run one synthesis subagent. It dedupes verifier-confirmed
   findings across lenses, ranks by severity, and emits the final report.

A finding survives only if the verifier confirms the citation is real and the
claim follows from it. Extensibility findings (L5/L6) are weighted highest in the
synthesis ordering.

## 5. Finding schema

```json
{
  "lens": "L5",
  "title": "one line",
  "severity": "blocker|major|minor|nit",
  "evidence": [{ "file": "crates/.../x.rs", "lines": "120-134", "quote": "…" }],
  "spec_ref": "crate-core-impl.md §3.4",
  "claim": "what is wrong / missing / coupled",
  "why_it_matters": "impact when more operation/execution varieties arrive",
  "recommended_change": "concrete edit"
}
```

## 6. Final output

A single report: **disparities** (code vs spec), **gaps** (spec requirements not
fully met), and **recommended changes** (ranked, extensibility first). Each item
carries its surviving evidence. No item without a confirmed code citation.
