# Multi-Agent Adversarial Review: `sandbox-e2e-live-test` Spec

Use this prompt to run a **read-only, multi-agent** adversarial review of the
design spec in:

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os/docs/e2e/sandbox-e2e-live-test-spec.md
```

## Mission & Premise

The premise is **not** that the spec is wrong in direction. Treat its direction
as fixed and correct:

- a **black-box** live E2E runner that drives real Docker-container sandboxes
  **only** through the public `sandbox-cli` → `sandbox-gateway` boundary;
- crate shape = **harness library (`src/`) + per-operation integration tests
  (`tests/[manager|runtime]/<family>/<operation>/<case>.rs`) + orchestrator bin
  `eos-e2e`**;
- three settled product decisions (the "Ownership Boundary"): sandbox/image ops
  performed by `sandbox-cli`; **no** manager-side observability sink; **Linux +
  Docker only**.

Your job is to make the *resulting* design **simpler, more generic, easier to
set up and run, and cheaper to extend** — across four lenses — and to verify the
spec's factual claims against live code. Each finding must end in a **concrete
change to the spec** (a deletion, a collapse, a simplification, a fixed friction
point, or a corrected claim), with the benefit quantified (moving parts removed,
setup steps removed, touch-points-per-new-op removed, env vars/artifacts/config
fields removed, or a corrected `file:line`).

"Looks fine" is not an acceptable verdict for any reviewer. If a part of the
design truly cannot be improved on a reviewer's axis, that reviewer must justify
it with live-code evidence and still report its before→after counts.

This is a **review-only** task. Do **not** implement code. Do **not** rewrite the
spec unless explicitly asked after the review. Treat the spec as a proposal and
**live code as the source of truth**.

## How To Run (multi-agent)

```text
1. Orchestrator: run the bootstrap below once, then spawn the 5 reviewer agents
   IN PARALLEL. Each reviewer is blind to the others and owns exactly one lens.
   Give each only: this prompt, its own section, the shared reading list, and the
   quantitative targets.
2. Each reviewer returns findings in the per-reviewer output contract.
3. Orchestrator: spawn the Synthesis agent with ALL five reviewers' findings.
   It merges, de-duplicates, resolves conflicts, and produces one hardened set of
   spec edits.
4. Orchestrator: return the synthesized output verbatim, plus an appendix of the
   raw per-reviewer findings.
```

Reviewers must not coordinate or converge prematurely — diversity of lens is the
point. Overlap is expected and is resolved by synthesis, not by reviewers
deferring to each other.

## Repo

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os
```

Bootstrap (orchestrator runs once, shares results):

```sh
git status --short
git log --oneline -8
# The target crate is a declared-but-empty workspace member: confirm.
sed -n '1,25p' Cargo.toml
ls -la crates/sandbox-e2e-live-test/
# The single biggest feasibility claim: the SHIPPED gateway wires stub services
# that always error. Confirm before reviewing — the spec's headline PROOF command
# depends on a real Docker-runtime gateway that does NOT ship.
sed -n '90,150p' crates/sandbox-gateway/src/gateway/main.rs
# The repo's own tests-module convention the spec says it mirrors.
sed -n '1,60p' crates/sandbox-daemon/tests/unit.rs
```

## Shared Required Reading

Target spec (read first, in full):

```text
docs/e2e/sandbox-e2e-live-test-spec.md
```

Live code — verify the spec's quoted shapes and line numbers; do not trust the
spec's citations:

```text
Cargo.toml                                                   # :17 member; [workspace.dependencies]
crates/sandbox-gateway/src/gateway/main.rs                   # :94-146 Unconfigured* stubs (the prerequisite gap)
crates/sandbox-gateway/src/cli/client.rs                     # :30-95 one JSON line in/out over the socket
crates/sandbox-gateway/src/cli/request_builder.rs            # :74-98 scope/id resolution
crates/sandbox-gateway/src/cli/output.rs                     # :21-23 exit codes; :266-272 error-key discriminator
crates/sandbox-manager/src/operation/impls/management/       # create/list/inspect/destroy/get_observability_tree (+ mod.rs response shapes)
crates/sandbox-runtime/operation/src/cli_definition/command_operations.rs
crates/sandbox-runtime/operation/src/cli_definition/workspace_session_operations.rs
crates/sandbox-runtime/operation/src/cli_definition/layerstack_operations.rs
crates/sandbox-manager/src/daemon_install.rs                 # :52-57 per-sandbox socket/pid paths
crates/sandbox-manager/src/model.rs                          # :10-22 SandboxId charset validation
crates/sandbox-observability/src/paths.rs                    # :19-35 obs db derived from socket path
crates/sandbox-observability/src/store.rs                    # V5 schema; *_for_test readers
crates/sandbox-daemon/tests/unit.rs                          # tests-module #[path]/include! convention
crates/sandbox-runtime/operation/tests/support/mod.rs        # shared-fixture convention
experiments/sandbox-cli-latency/run.py                       # prior repeatable-runner precedent
```

Use `rg` for call paths, response field readers, and the actual op argument sets.
Verify a claim before defending or attacking it.

## Shared Ground Rules

- Live code wins over the spec.
- Every finding cites `file:line` (in the spec and/or live code) and ends in a
  concrete spec edit + the quantified benefit.
- Stay inside the three settled product decisions (sandbox ops via `sandbox-cli`;
  no manager observability sink; Linux + Docker only) and the fixed crate shape
  (lib + per-operation tests + orchestrator). You may challenge any **internal
  mechanism** (the two-process orchestrator/`cargo test` split, the `EOS_E2E_*`
  env handshake, the artifact set, the cleanup keying, the module breakdown), but
  not the settled decisions themselves.
- Simplify by deletion before abstraction. Do not propose a new module/indirection
  to "clean up" unless it removes more than it adds.
- Separate live-code facts from inferred design advice.

## Quantitative Targets

Every reviewer reports the relevant counts, **before (spec) → after (your
proposal)**. The synthesis agent drives each toward its leanest defensible value.

```text
Distinct moving parts the runner introduces (src modules + bin + env vars + handshakes):  minimize, list each
Setup steps from clean checkout to one green run:                                          minimize, enumerate
Touch points to add ONE new CLI operation test:                                            target: add 1 file (challenge any extra edit)
Manual registration points (e.g. #[path] include lines, env exports, config fields):       minimize
Artifact files written per run:                                                            justify each or merge
RunConfig fields / env vars / CLI flags:                                                   minimize, prove each is load-bearing
Cross-process / cross-binary coupling points (env contract, libtest-output parsing):       minimize, de-risk each
Unverified or stale file:line claims in the spec:                                           target 0
```

---

## Reviewer 1 — Architecture Simplicity

**Lens:** the total number of moving parts in the *resulting* design and the
seams between them. Every module, binary, env var, handshake, artifact file, and
config field is suspect until proven load-bearing.

**Mandate:** enumerate the parts, then cut every avoidable one and collapse every
avoidable seam — without crossing the settled decisions.

**Seed hunt list (find more):**

- The **two-process split** (orchestrator bin `eos-e2e` *and* `cargo test`): is
  the orchestrator earning its keep, or could a single entry point do build +
  gateway + run + aggregate + cleanup? Conversely, is the split actually forced by
  the `cargo test` process model (separate test binaries) — and if so, does the
  spec lean on it more than necessary?
- The **`EOS_E2E_*` env handshake** (`EOS_E2E_GATEWAY_SOCKET`/`RUN_ROOT`/`RUN_ID`/
  `IMAGE`): four env vars as the cross-process contract — could one (a run-root
  path, with everything else discovered from a `run-manifest.json` inside it)
  replace them?
- **Cross-binary `summary.json` aggregation by parsing libtest output**: a fragile
  seam. Is it needed at all, or can per-test `result.json` files be the sole
  source of truth (orchestrator just globs them)?
- **Two test binaries** (`manager`, `runtime`) vs one: what does the split buy?
- The **module set** in `src/` (`config`, `cli_client`, `fixtures`, `gateway`,
  `docker`, `observe`, `report`, `cleanup`, `assertion`, `outcome`): merge any
  that are thin wrappers. Is `cli_client` more than `std::process::Command` +
  one-line parse? Is `outcome` separable from `report`?
- **Three cleanup tags** (docker label + sandbox-id prefix + path namespacing): is
  path namespacing + docker label sufficient, making the id-prefix sweep
  redundant — or vice versa?
- The **artifact file set** per run (`run-manifest`, `summary`, `timing`,
  `cleanup-report` + per-test `exchange.jsonl`/`observability.json`/`traces.json`/
  `result.json`): collapse overlapping files; challenge any not consumed by the
  rerun or proof flow.
- **`RunConfig`** field count: prove each flag/env is used.

**Output:** a parts inventory (part → keep/merge/delete → why), the before→after
moving-parts and seam counts, and the single biggest structural simplification.

## Reviewer 2 — Genericity

**Lens:** whether the harness core is genuinely operation-agnostic, or is secretly
shaped around `create_sandbox` + `exec_command` with the other 9 ops bolted on.

**Mandate:** prove a single generic CLI-driving + fixture + assertion core serves
all manager and runtime operations, or name precisely where it breaks and the
minimal generic shape that absorbs the exception.

**Seed hunt list (find more):**

- The **manager-vs-runtime scope** split: does one `cli_client` surface drive both
  (`manager <op>` System scope, `runtime --sandbox-id <id> <op>` Sandbox scope —
  `request_builder.rs:74-98`) generically, or are there two near-duplicate paths?
- **Stateful op chains**: R4→R5→R6 (running `exec_command` → `command_session_id`
  → `write_command_stdin`/`read_command_lines`) require capturing an id mid-test
  and asserting **monotonic offsets**. Does the generic assertion/fixture model
  express this, or does it need a per-op escape hatch? (See
  `command_operations.rs` for the real `command_session_id`/offset semantics.)
- **Conditional outcomes**: `squash` reports `squashed:false` without error unless
  layers changed; `destroy_workspace_session` fails with
  `active_command_session_ids` if commands are live. Does the json-pointer
  assertion set cover "either-or" contracts generically?
- **`provision_sandbox`** genericity: across `--image` and across
  `create_workspace_session --profile {host_compatible,isolated}` — one fixture or
  many?
- **"One test owns one sandbox"** vs ops that inherently need a *session* and a
  *running command* in the same sandbox: does the model generalize, or do some
  ops need multi-step fixtures the spec under-specifies?
- The **assertion helper set** (`ok`/`err_kind`/`field`/`offsets_monotonic`): is
  it complete for every response shape in `management/mod.rs`,
  `command_operations.rs`, `workspace_session_operations.rs`,
  `layerstack_operations.rs`, or are bespoke asserts unavoidable for some ops?

**Output:** a per-operation "served by generic core / needs exception" table (all
manager + runtime ops), the exact failing op + minimal generic shape for each
exception, and a verdict on whether the core is truly generic.

## Reviewer 3 — Ease of Use & Setup

**Lens:** a new engineer or a CI job going from `git clone` to one green run, and
the failure surface when something is missing.

**Mandate:** count every prerequisite and step, find every first-run footgun, and
cut friction — being brutally honest about whether the "easy to use and setup"
requirement is actually met today.

**Seed hunt list (find more):**

- **The headline hazard**: the spec's PROOF command
  (`cargo run --bin eos-e2e ...`) needs a gateway wired with a **real Docker
  runtime**, but the shipped `sandbox-gateway` wires `Unconfigured*` stubs that
  always error (`crates/sandbox-gateway/src/gateway/main.rs:94-146`). So the
  out-of-the-box experience is a **guaranteed failure** until an unshipped
  prerequisite exists. Is this surfaced loudly enough? What is the minimal thing
  the spec must specify so first-run isn't a dead end (a clear precondition check
  with an actionable message? a documented way to point at a real gateway? a
  fail-fast that says exactly what's missing)?
- **Running tests the "obvious" way**: a newcomer types
  `cargo test -p sandbox-e2e-live-test`. With no orchestrator, `EOS_E2E_*` is
  unset and fixtures panic. Is the panic message actionable (tells them to run
  `eos-e2e`)? Should the tests **skip with a clear reason** instead of panicking
  when the env/gateway is absent?
- **Prerequisite enumeration**: Linux, Docker daemon running, a Docker image
  pulled, the daemon binary packaged (`xtask package`?), the real-runtime gateway.
  The spec scatters these — is there one preflight that checks them all and prints
  a checklist?
- **The single-command claim**: does any single command work from a clean
  checkout? If not, what is the shortest honest "getting started" sequence, and
  can it be shortened?
- **Reproduction ergonomics**: re-running one failed operation — is
  `--rerun-failed-from` plus the manual `EOS_E2E_* cargo test ... <filter>` path
  discoverable, or does it require reading the whole spec?
- **Error legibility**: when the gateway socket is wrong, when Docker is down,
  when an image is missing — does the design route these to clear messages, or to
  an opaque CLI nonzero exit?

**Output:** an ordered clone→green step list (with each prerequisite and its
failure mode), the top first-run footguns ranked, the concrete spec additions that
remove each (preflight check, skip-vs-panic policy, the minimal first-run command),
and an honest met/not-met verdict on the requirement.

## Reviewer 4 — Extensibility

**Lens:** the marginal cost of growth — adding the next operation, assertion kind,
image/profile, artifact, or observability signal.

**Mandate:** sketch each growth scenario end-to-end, count the touch points, and
verify growth is **add-a-file**, not **edit-a-registry**; cut every manual
registration tax.

**Seed hunt list (find more):**

- **Add one runtime operation**: new leaf `tests/runtime/<family>/<op>/<case>.rs` *plus*
  a `#[path = "..."] mod ...;` line in `tests/runtime.rs`. The path-include is a
  **manual registration point** that scales linearly with op count. Across dozens
  of ops, is this a real tax? Is there a leaner convention (e.g. a single
  `include!`-of-a-generated-list, a directory module pattern, or a per-family
  aggregator file) that keeps "add a file" truly one edit — without violating the
  repo's `#[path]` convention?
- **Add a new operation family** (a third runtime grouping): how many files/edits?
- **Add a new assertion kind**: is `assertion.rs` open for extension, or will new
  response shapes force bespoke per-test code?
- **Second image / non-ubuntu container / new session profile**: is `image` a
  clean parameter, or baked into fixtures/cleanup assumptions?
- **New artifact or new `summary.json` field**: is there `schema_version`
  discipline so consumers don't break? (Spec claims `schema_version` — verify it's
  applied to every artifact, not just `summary.json`.)
- **Consuming the optional P1/P2 daemon spans** (cgroup CPU/mem, queue-wait): does
  the runner pick them up automatically once they land (read via
  `get_observability_tree` / `*_for_test`), or does it need code changes? Verify
  against `store.rs` `*_for_test` readers and the tree fields.
- **Brittleness of growth**: does the orchestrator's libtest-output parsing break
  when someone renames a test or adds a `#[ignore]`? Does the `{run_id}-<slug>`
  id scheme survive many tests without collision?

**Output:** a per-scenario touch-point count (before→after), each manual
registration point with a concrete leaner alternative, and the single change that
most reduces the marginal cost of the next operation.

## Reviewer 5 — Live-Evidence & Feasibility Honesty

**Lens:** every `file:line` claim and every load-bearing feasibility assumption in
the spec. (This lens guards the other four: a simplification built on a wrong
claim is worse than none.)

**Mandate:** verify the spec against live code; flag drift, wrong line numbers,
and assumptions that won't hold; for each, give the corrected fact and the spec
edit it forces.

**Seed hunt list (find more):**

- **Response shapes & op arguments**: confirm every assertion in the test matrix
  matches the real builders/parsers — `management/mod.rs` record shape;
  `command_operations.rs` (`command_session_id` present iff `status=="running"`,
  offset fields); `workspace_session_operations.rs` (`profile` values, grace
  validation); `layerstack_operations.rs` (`squashed`/`revision`). Wrong field
  names or wrong conditionals are L1.
- **Transport & exit-code contract**: `client.rs:30-95` (one line in/out),
  `output.rs:21-23` exit codes and `:266-272` error-key discriminator — exactly as
  the spec asserts?
- **The `cargo test` process model assumptions**: (a) each top-level `tests/*.rs`
  is a separate binary; (b) `#[test]` fns parallelize via `--test-threads`;
  (c) separate test binaries can share ONE externally-started gateway + run-root
  via env; (d) a `Drop` impl reliably runs `sandbox-cli destroy_sandbox` even on
  assertion-panic (and on `--test-threads` aborts / SIGINT — does it?). Stress (d):
  panics in parallel tests, leaked sandboxes if the process is killed.
- **libtest output parseability**: is the orchestrator's "merge libtest pass/fail
  with per-test `result.json`" feasible on stable Rust (no JSON test output
  without nightly `-Z`)? If not, name the corrected mechanism (e.g. tests are the
  sole source via `result.json`; orchestrator only needs the process exit code).
- **Docker label injection** (spec Open Item #2): `create_sandbox` must stamp
  `eos.e2e.run_id` on the container, but `create_sandbox` today takes only
  `--image`/`--workspace-root` and the runtime is unshipped. Confirm there is **no
  current way** to pass a label through `sandbox-cli`, and state what the spec must
  require of the (unshipped) Docker runtime so label-based cleanup is real rather
  than assumed.
- **Determinism claims**: `run_id` charset must satisfy `model.rs:10-22`; the
  `sha2`-based slug and `EOS_E2E_RUN_CLOCK` pin — verify `sha2`/`time` are
  workspace deps and the scheme is actually reproducible.
- **Member/build claim**: `Cargo.toml:17` lists the crate but the dir is empty, so
  the workspace currently fails to build — confirm and confirm Phase 0 fixes it.

**Output:** a claim ledger (spec claim → live-code verdict `confirmed/stale/wrong`
→ corrected `file:line` → forced spec edit), severity-tagged, with the
feasibility assumptions that must change before implementation called out first.

---

## Synthesis Agent — Hardened Spec

**Input:** all five reviewers' raw findings.

**Method:**

1. De-duplicate overlapping findings; keep the strongest `file:line` evidence.
2. Resolve conflicts between lenses explicitly (e.g. Reviewer 1 wants to delete
   the orchestrator/`cargo test` split for simplicity; Reviewer 5 shows the
   process model forces it — pick one and state why).
3. Drive every Quantitative Target to its leanest defensible value; show
   before→after for each.
4. Produce one hardened design and the precise spec edits that get there.

**Output:**

```text
Scorecard
  <each quantitative target: before → after, with the change that achieves it>

Findings (merged, severity-ordered, deduped)
  N. [Lens] [Severity] Title
     Evidence:   file:line (spec and/or live code)
     Change:     <concrete spec edit: delete/merge/simplify/fix-claim>
     Benefit:    <moving parts | setup steps | touch-points | env/flags | corrected fact>
     Risk/limit: <constraint, if any>

Conflicts Resolved
  <lens-vs-lens decisions and rationale>

Hardened Design
  Simplicity:     final moving-parts set + seams
  Genericity:     final generic core + any justified exception
  Ease of setup:  final clone→green path + preflight/skip policy
  Extensibility:  final add-an-operation cost + registration convention

Spec Edits Required (precise, section-by-section, so the spec converges)
Deferred / Residual Risk (incl. the unshipped Docker-runtime gateway prerequisite)
```

## Severity Scale

Severity = leverage on the four axes (and correctness of claims), not runtime bug
risk:

```text
L0  A claim is wrong or an assumption won't hold such that the design fails as
    written (e.g. tests can't share a gateway; cleanup label can't be set) —
    must-fix before implementation.
L1  Large win: removes a whole module/seam/handshake, makes the core genuinely
    generic, removes a first-run dead-end, or makes "add an op" truly one file.
L2  Meaningful win: merges modules/artifacts, removes config/env, cuts a setup
    step, removes a manual registration point.
L3  Clarity/consistency improvement that reduces future-reader confusion.
```

## Forbidden Recommendations

Do not recommend anything that crosses a settled decision or adds weight to undo a
win:

- reintroducing a **test-injected `SandboxRuntime`** or any non-`sandbox-cli`
  provisioning path (sandbox/image ops go through `sandbox-cli`);
- adding a **manager-side observability sink** or a second observability
  classification axis (monitoring is `get_observability_tree` + daemon spans only);
- any **non-Linux / non-Docker** code path;
- collapsing the crate back to **bin-only** or abandoning the per-operation
  `tests/[manager|runtime]/<family>/<operation>/<case>.rs` layout (the crate shape is
  fixed) — internal mechanisms may still be challenged;
- new layers/indirection/abstraction whose only justification is "cleaner";
- compatibility shims, aliases, or dual provisioning paths.

## Rules

- Lead with concrete findings, not summaries. Cite `file:line`.
- Every finding ends in a concrete spec edit with a quantified benefit.
- Separate live-code facts from inferred design advice.
- Prefer a small spec edit over a broad rewrite; name the exact edit and section.
- A reviewer who finds little must still report its before→after counts and name
  the single biggest remaining improvement in its lens.
- The synthesis agent must output one hardened design and one edit list, not a
  menu.
