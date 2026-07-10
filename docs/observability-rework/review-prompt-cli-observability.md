# Adversarial Review Prompt - CLI Observability Display, Protocol, and Implementation

> **Frozen historical prompt (operation-layout exempt, 2026-07-11):** Do not
> execute this prompt verbatim; its package names and source paths identify the
> implementation reviewed at the time.

Use this to drive a skeptical, subagent-led review of the CLI observability docs:

- `docs/observability-rework/cli-observability.md`
- `docs/observability-rework/cli-observability-examples.md`

The goal is not to confirm the current CLI design. The goal is to find the smaller,
clearer CLI and rendering contract that still helps an operator understand what
happened inside a sandbox.

Paste this into a fresh lead agent with repo access.

---

## Role

You are an adversarial CLI and observability reviewer. Your north star is:

- useful observability display over pretty but noisy output;
- a CLI protocol that is easy to remember, script, and extend;
- implementation that is trivial first, flexible second, and never clever for its own sake.

Assume the current docs are thoughtful but not sacred. Challenge every subcommand,
flag, table, waterfall line, and output shape. Prefer deleting or merging surfaces over
adding new ones. Flexibility should come from simple data contracts and escape hatches
like `raw`, not from speculative abstractions.

## Subagent Workflow

Run this fixed workflow. Keep Phase 1 agents blind to each other.

1. Phase 1 - parallel area review, 4 subagents:
   - Area A: display/readability reviewer.
   - Area B: CLI protocol/minimalism reviewer.
   - Area C: implementation-triviality reviewer.
   - Area D: examples/coherence reviewer.
2. Phase 2 - parallel verification, 4 subagents:
   - One verifier per area tries to refute that area's findings.
   - Drop findings that are taste-only, unsupported, or make implementation more complex.
3. Phase 3 - synthesis, 1 subagent:
   - Dedupe findings.
   - Resolve conflicts.
   - Produce the final verdict and a concrete docs/code punch-list.

Do not spawn dynamic extra subagents. Do not let subagents design large new systems.

## What To Read

Primary docs:

1. `docs/observability-rework/cli-observability.md`
   - operation family/specs;
   - help output;
   - command permutation matrix;
   - exact output shapes for `snapshot`, `trace`, `events`, `cgroup`, `layerstack`, `raw`.
2. `docs/observability-rework/cli-observability-examples.md`
   - concrete trace examples for each operation;
   - raw NDJSON blocks;
   - rendered waterfalls;
   - sync/async/cross-process/event conventions.

Context docs, only as needed:

- `docs/observability-rework/README.md`
- `docs/observability-rework/crate-core-impl.md`
- `docs/observability-rework/span-trace-impl.md`
- `docs/observability-rework/layerstack-impl.md`

Implementation reality check, only where a claim depends on code:

- `crates/sandbox-gateway/src/cli/observability_specs.rs`
- `crates/sandbox-gateway/src/cli/request_builder.rs`
- `crates/sandbox-gateway/src/cli/output.rs`
- `crates/sandbox-daemon/src/observability/view.rs`
- `crates/sandbox-daemon/src/observability/service.rs`
- `crates/sandbox-observability/src/reader.rs`
- `crates/sandbox-runtime/operation/src/**`

## Open For Attack

Everything in the two target docs is open for simplification, including:

- whether all six views need to be separate subcommands;
- whether `events` is useful if `raw --kind event` exists;
- whether `trace --id last` is worth special behavior;
- whether `cgroup` and `layerstack --window-ms` should share one trend shape;
- whether output should be rendered server-side, CLI-side, or mostly JSON;
- whether every command needs a custom human renderer;
- whether examples show too much precision to be maintainable.

Do preserve the use cases unless you can show a cheaper replacement:

- inspect current live state;
- understand one command/request as a trace;
- list important domain events;
- inspect resource samples over time;
- inspect layer/lease/upperdir state;
- get raw machine-readable records for `jq`/grep/scripts.

## Review Areas

### Area A - Display and Observability Value

Answer directly: is the displayed data good enough for a human operator, and can it be optimized?

Attack:

- Does each view answer a real question in one screen, or does it dump internal structure?
- Are the waterfall rows readable under long names, many attrs, errors, missing parents, in-flight spans, and cross-process spans?
- Does the display make the important thing obvious: latency, failure, resource pressure, lease/layer changes, and "what is still running"?
- Are the examples too bespoke to implement cheaply? Which formatting can be dropped without losing signal?
- Is there a simpler display rule that works for every span/event/sample instead of per-operation formatting?
- Should output default to human text, JSON, NDJSON, or a small set of modes?

Required Area A output:

- best display ideas to keep;
- display clutter to cut;
- exact replacement shape for any view you want changed.

### Area B - CLI Protocol Simplicity

Answer directly: is the CLI protocol good, and can it be optimized/simplified?

Attack:

- Are the subcommands memorable and orthogonal?
- Are flags consistent (`--id` vs `--trace`, `--since-ms`, `--window-ms`, `--scope`, `--workspace`)?
- Is `--sandbox-id` in the right place, or should observability behave like runtime commands?
- Does every view need its own operation spec, or can a smaller command grammar cover the same use cases?
- Can `events` collapse into `raw --kind event` plus a formatter?
- Can `cgroup` and `layerstack` trend windows share a generic `series` view?
- Are defaults safe and obvious? Challenge `trace --id last`, default windows, empty filters, and window caps.
- Does the protocol stay stable if new record kinds, scopes, attrs, or views are added?

Required Area B output:

- proposed minimal command set;
- flags to rename/delete/merge;
- compatibility cost if changing the current docs.

### Area C - Trivial Implementation and Flexible Extension

Answer directly: can this be implemented trivially, and does it stay flexible without becoming abstract?

Attack:

- Count implementation surfaces: CLI specs, request builder mapping, daemon view router, reader folds, renderers, tests.
- For each view, decide whether the daemon should return raw data, a lightly structured JSON view, or already-rendered text.
- Prefer one generic renderer or simple JSON output over six custom renderers unless a custom renderer clearly earns its place.
- Stress adding a new record kind, metric, event name, resource scope, or command span. What files change?
- Does the design require coordinated changes across daemon, gateway, protocol, and docs for ordinary additions?
- Is the "exact output shape" contract too rigid for a cheap implementation?
- Are the examples testing behavior or freezing incidental spacing and fake timings?

Required Area C output:

- smallest implementation plan you would endorse;
- what to implement first;
- what to defer until a real user asks.

### Area D - Examples, Coherence, and Maintainability

Answer directly: do the concrete examples make the design clearer, or do they overfit/freeze complexity?

Attack:

- Cross-check `cli-observability.md` and `cli-observability-examples.md` for naming, span ids, attrs, offsets, status labels, and sync/async markings.
- Every `parent` in raw NDJSON must resolve to a span in the same trace.
- Rendered offsets must match `ts - dur_ms` relative to trace start.
- If the docs say an operation has no span/event, verify the examples do not imply otherwise.
- Find examples that would be expensive or impossible to produce from the stated reader model.
- Identify fake precision that should be replaced with schematic examples.
- Decide whether examples should be canonical golden fixtures or illustrative only.

Required Area D output:

- inconsistencies or impossible examples;
- examples to delete/shorten;
- examples that should become tests, if any.

## Decision Bar

A proposed change is good only if it improves at least one of these without hurting the others:

- easier for an operator to understand;
- fewer CLI concepts;
- fewer implementation paths;
- easier to add future record kinds/scopes/events;
- easier to test without brittle golden output.

Reject changes that merely move complexity between files.

## Required Final Output

Start with one-line verdict:

`ship-as-is` / `ship-with-small-edits` / `needs-simplification` / `needs-redesign`

Then provide findings ordered by severity. Each finding must include exactly:

- Suggested fix: concrete minimal change.
- What's good: what to preserve from the current docs.
- Reason: concrete cost, confusion, or implementation burden, with doc section or code path.

Tag each finding with area labels: `A-display`, `B-protocol`, `C-implementation`, `D-examples`.

End with:

1. Minimal CLI: the smallest command/flag set you recommend.
2. Minimal rendering contract: human/JSON/NDJSON rules and where rendering lives.
3. Implementation punch-list: file-by-file changes, smallest first.
4. What to cut first / keep last: rank views, renderers, and examples by value.

## Rules

- Bias to subtraction.
- No praise-only review unless it identifies what must not be broken.
- No prose nits unless they hide a design or implementation problem.
- Every objection needs a smaller replacement.
- Do not add dependencies unless they delete more code than they add.
- Prefer flexible data over configurable machinery.
