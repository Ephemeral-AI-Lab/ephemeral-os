# Adversarial Review Prompt — Observability Rework Spec

Use this to drive a skeptical, independent review of the spec. Paste it into a
fresh agent/reviewer with repo access. It is deliberately self-contained.

---

## Role

You are an adversarial design reviewer. Your job is **not** to validate this spec
— it is to find where it is more complex than it needs to be, where it will be
hard to live with, and where it quietly breaks. Assume the author is smart and
already convinced; your value is the objection they didn't think of. A review
that finds nothing is a failed review. Be concrete, cite specifics, and prefer a
smaller design over a cleverer one.

## What to read first

1. The spec: `docs/observability-rework/README.md` (the single source of truth —
   model, example cases, crate rework, fetch, removal, rollout).
2. The code it touches, enough to judge feasibility, not exhaustively:
   - `crates/sandbox-observability/**` (what's being rewritten)
   - `crates/sandbox-daemon/src/observability/**` + `src/server/dispatch.rs`
     (collection + the RPC seam)
   - `crates/sandbox-runtime/namespace-execution/src/{engine.rs,types.rs}`
     (the `ExecutionObserver` / watcher-thread seam the async spans hook)
   - `crates/sandbox-runtime/operation/src/command/service/exec_command.rs`
     (the one-shot finalize tail)
   - `crates/sandbox-runtime/layerstack/src/stack/**` (lease / publish / squash)
   - `crates/sandbox-provider-docker/src/runtime.rs` (env forwarding being removed)

## The bar to judge against (hard constraints — do not relitigate these as if they were open)

These were chosen by the project owner; treat them as fixed *intent* and review
whether the spec serves them well. You MAY argue one is a mistake, but only with
a concrete, costed case — not a preference.

- **One layer:** all observability lives in `sandbox-observability`; it must stay
  a dependency-light leaf the runtime can emit into.
- **No SQLite:** the store is an append-only file. (If you want to re-add a DB,
  you must beat append-only NDJSON on the project's actual needs, not in the
  abstract.)
- **Must cover:** spans, traces, events, and cgroup/disk samples.
- **Developer fetch must be easy.**

## Review dimensions (attack each)

For every dimension, don't just opine — name the specific spec section or type,
propose the smaller/simpler alternative, and say what it costs.

### 1. Simplicity & extensibility of the implementation
- Count the moving parts: `Observer`, `Span`, `SpanHandle`, `TraceContext`,
  `Sink`, `Reader`, `Attrs`, `Scope`, the view types. Which of these earn their
  place? Which collapse into another? Could free functions replace the
  `Observer` object? Is `SpanHandle` (async) vs `Span` (sync) a real split or
  two names for one thing?
- Start/end as two records vs one completion record: is the live "in-flight"
  view worth the reducer complexity, the orphaned-start failure mode, and 2× the
  write volume? Argue both sides and pick.
- Extensibility test: walk through adding (a) a new record kind, (b) a new emit
  seam in a new crate, (c) a new attr on `sample`. How much churn? Does anything
  force a coordinated multi-crate change?
- The Phase A → Phase B split: does shipping per-process correlation first create
  a format or API that Phase B then has to break? Is the "no schema change
  between phases" claim actually true?

### 2. Easy to fetch & observe (does the data round-trip into understanding?)
- Can the `Reader` actually reconstruct the Case A waterfall from the raw lines
  as written? Check: parent ends before child, missing `end` (crash), a `start`
  lost to rotation while its `end` survives, records from 2 processes
  interleaved, monotonicity of `ts` across processes/clocks.
- Phase A correlates cross-process work by `exec_id` + time only. Is that
  genuinely observable, or a footgun that looks correlated but isn't? Would a
  developer trust the rendered tree?
- Is one-shot RPC pull enough, or does "observe" need a live tail/stream
  (`--follow`) for a command that runs for minutes? Is reading a possibly-rotated
  multi-file NDJSON over an RPC that returns one JSON blob a scaling problem?
- Does the spec actually specify enough to *build* the rendered views, or are the
  CLI mockups hiding undefined reduction logic?

### 3. Can it be simpler? (this is the primary question — answer it directly)
- Challenge every component for removal: do we need spans AND events AND samples,
  or can events subsume spans (start/end as events)? Do we need a tree (`parent`)
  or is a flat, time-ordered, `trace`-tagged log enough for the stated goal
  ("understand performance issues")? Do we need server-side `Reader`/views at
  all, or should the daemon return raw filtered lines and let the CLI reduce —
  moving all reduction to one place?
- Do we need the `Observer` abstraction and config gating, or is "always write
  cheap lines" simpler and fine?
- Reuse vs build: would adopting `tracing` + a JSON layer be *simpler overall*
  (less bespoke code) despite the dep, or genuinely heavier? Make the call with
  reasons, don't dodge it.
- State the **simplest viable design** that still meets the four hard constraints,
  even if it deletes half the spec. If the spec is already minimal, say so and
  defend it.

### Failure-mode checklist (force coverage — address each, even if just "fine, because…")
- Crash/abort mid-span → permanent unpaired `start` → false "in-flight forever".
- `O_APPEND` atomicity claim: is ≤`PIPE_BUF` (4096B) real on the actual fs
  (tmpfs/overlay in-container)? What enforces line length once `attrs` grow? What
  happens to a >4096B line — interleaved corruption?
- Rotation racing concurrent appenders; a trace split across `…ndjson` and
  `…ndjson.1`; total loss of in-flight history on rotate.
- Always-on emit overhead in hot paths (per-span serialize + lock + write) under
  the forked namespace-process and high command rates.
- Multi-process clock skew / `ts` ordering; pid reuse; trace-id collisions.
- PII / secrets: command lines, paths, env in `attrs`/transcripts written to a
  file then shipped over RPC — any redaction story?
- Unbounded cardinality of traces/attrs; the 32MiB cap silently dropping the data
  you wanted.
- What real capability is *lost* by dropping SQLite (durable concurrent writes,
  ad-hoc queries, retention/history beyond the file cap) — and does any consumer
  need it?

## Required output format

Start with a one-line **verdict**: `ship-as-is` / `ship-with-changes` /
`needs-rework`, plus the single biggest reason.

Then a **findings list**, ordered by severity (Critical → Major → Minor). Each
finding MUST contain exactly these three fields, in this order:

- **Suggested fix:** the concrete, minimal change (a smaller type set, a deleted
  component, a format tweak, an added field, a phase reordering). Show the diff in
  intent, not vague advice.
- **What's good:** state what the spec gets right here / what to preserve, so the
  fix doesn't regress a real strength. (If a finding is pure praise with no
  change, mark severity `Praise` and leave Suggested fix = "none".)
- **Reason:** why it matters — the concrete failure, cost, or confusion it
  causes, tied to a spec §/type/example or a code path. No hand-waving.

Tag each finding with the dimension(s) it serves (1/2/3) and a spec section ref.

End with two required sections:
- **Simplest viable design:** the smallest version of this system you'd endorse
  that still meets the four hard constraints — even if it contradicts the spec.
- **What you'd cut first / keep last:** rank the spec's components by value, so
  the author knows the load-bearing core vs. the negotiable extras.

## Rules of engagement
- Bias to subtraction: when in doubt, propose removing, not adding.
- No rubber-stamping and no nitpicking prose — only findings that change the
  design or its risk.
- Every "this is wrong" must come with the smaller/better thing that replaces it.
- Respect the four hard constraints; if you breach one, justify it as a costed
  trade, not a preference.
