# Review — `backend-server/SPEC.md`

Reviewer pass against the live code (every claim below cites `file:line` I read).
Scope: the 8 questions you posed (bridge role, port/adapter split, sandbox
independence, lifecycle ownership, naming, Rust OOP, SOLID/SRP, simplification).

## Verdict

**The core architecture is sound and unusually well-evidenced.** Option C
(port stays with the consumer, adapter moves to the resource owner) is the correct
DIP/hexagonal cut, and I verified it creates **no dependency back-edge** — the
relocation is genuinely safe. The two-DB ownership split is clean, the DIP seams
(`AuditSink` / `SandboxTransport` / `EventCallback` / `RequestProvisioner`) are
real and minimal, and the dependency DAG is acyclic and forward-only as drawn.

What needs fixing is **not** the architecture — it's a handful of factual claims
that are wrong against today's code, and four implementation realities the spec
glosses. None block; all are cheap. Then there are three honest simplification
levers (one is free, one is a question the spec must answer, one is a tradeoff).

---

## A. Verified correct (give the spec credit — these are airtight)

| Spec claim | Status | Evidence |
|---|---|---|
| Option C relocation creates no back-edge | ✅ TRUE | `eos-sandbox-host` deps = port + `eos-types` + `eos-protocol` + bollard only; nothing in `agent-core/*` (except runtime) or `sandbox/*` imports `eos_runtime`/`eos_sandbox_host` |
| `eos-tools`/`eos-engine` are port-only | ✅ TRUE | both depend on `eos-sandbox-api` only; zero `use eos_sandbox_host` |
| `eos-runtime` is the lone host-importer (7 symbols) | ✅ TRUE | `builder.rs:22-25` imports exactly `resolve_provider_kind, DaemonClient, DockerProviderAdapter, ProviderRegistry, RequestProvisioner, RequestSandboxProvisioner, SandboxLifecycle` |
| Live-event hook is a sync borrowing `Fn(&StreamEvent)` | ✅ TRUE | `eos-engine/.../runtime/types.rs:29`: `pub type EventCallback = Arc<dyn Fn(&StreamEvent) + Send + Sync>` |
| `run_request` runs inline to completion | ✅ TRUE | `entry.rs:73` provisions → runs root agent inline (`run_agent(...).await` :234) → finalizes → returns |
| `AuditSink` is a clean, non-sealed, single-method DIP seam | ✅ TRUE | `eos-audit/src/sink.rs:16` |
| Provisioning contracts live in the host crate today | ✅ TRUE | `RequestSandboxBinding` `provisioning.rs:22`, `trait RequestProvisioner` `provisioning.rs:35` |
| Sandbox audit buffer is a `static OnceLock<AuditBuffer>` w/ de-static TODO | ✅ TRUE | `eos-daemon/src/audit/buffer.rs:228` + comment `:218-220` |
| amd64 `eosd` SHA pin exists and is stale-prone | ✅ TRUE | `bootstrap_artifact.rs:53` (`af19…`); the `:40-49` comment already flags it VOLATILE/not-yet-wired |

The "forced in-process embedding" argument (Decision #1) is **well-reasoned and
correct as a description of the current hook** — but "forced" overstates it. It's
forced *given a synchronous, borrowing event hook*. An owned/async hook (emit to a
channel or to agent-core's DB) would permit a detached server. Recommend softening
to *"forced by the current hook shape; lifting it is an agent-core change, out of
scope."* Honest framing, same conclusion.

---

## B. Factual corrections (the spec states these; code disagrees)

1. **Resolved: `eos-protocol` is no longer pulled into `agent-core` by obs
   normalization.** The former standalone collector normalization/gate modules
   now live in `eos-backend-obs`, beside their only backend consumer. The spec's
   "zero wire deps in agent-core" claim is now backed by the workspace shape.

2. **The port's deps are not "only `eos-types` + `tokio`"** (§6, ~line 708).
   `eos-sandbox-api` has 6 production deps: `eos-types, serde, serde_json, schemars,
   async-trait, thiserror` (`tokio` is dev-only). The *substance* of the DIP claim
   holds (zero `bollard`, zero `eos-protocol`, zero Docker — that's what matters), so
   this is a citation error, not a design error. Fix the sentence.

3. **"R2 is a pure move / promotion, no behavior change" is false if you keep the
   §3.2 rich input.** See gap C3 below — this is both a factual and a design issue.

---

## C. Design realities the spec under-specifies (cheap, but must be owned)

| # | Gap | Why it matters | Smallest fix |
|---|---|---|---|
| **C1** | `provisioner()` is `#[cfg(test)] pub(crate)` today (`builder.rs:173-177`), while `transport()` is `pub` (`:181`). In production the provisioner is **always** the defaulted host-backed one (`:309-317`). | The spec's whole composition root (`.provisioner(sandboxes.provisioner())`, §2.3) calls a setter that **does not exist in non-test builds**. R3 is silently **three** changes, not one. | R3 must explicitly: (a) make `provisioner()` `pub` + non-test, (b) require it, (c) drop the default. The spec only says "require." |
| **C2** | `EventCallback` is **synchronous and borrowing** (`Fn(&StreamEvent)`, runs inline on the engine thread). | §2.2 routes `on_event → {broadcast + MILESTONE event_log}`. The broadcast send is sync/non-blocking (fine), but **`event_log` is async SQLite I/O — you cannot `.await` it inside a sync `Fn`.** Milestone persistence MUST be offloaded to an `mpsc` drainer task; persisting inline would either not compile or block the engine. | State the drainer task in §2.2/§5.2. One line, but it's load-bearing and currently invisible. |
| **C3** | The current contract is `prepare_for_run(request_id, sandbox_id: Option<&str>)` (`provisioning.rs:37-41`); `RequestRunInput` carries only `sandbox_id: Option<String>` (`request_input.rs:9-20`). | §3.2/§5.1 advertise per-request `sandbox_args { image, snapshot, project_dir }`. None of those flow through the current trait or input struct. Supporting them is a **trait-signature change** rippling through the port (R2 stops being a "pure move"), `RequestRunInput` (+fields/+setters — an unlisted agent-core edit), and the `entry.rs:90` call site. Also `project_dir` has **two** sources (per-request `sandbox_args` vs backend-global `SandboxConfig.project_dir`, `§5.1`) with no precedence rule. | Pick one: **(a) drop per-request image/snapshot/project_dir from v1** (smallest — keep `Option<&str>`, take `project_dir` from `SandboxConfig` only), or **(b) own the trait+input+entry.rs edits explicitly** and define precedence. Right now the spec implies (a)'s effort for (b)'s surface. |
| **C4** | `StateReader` holds both `Arc<dyn …Store>` handles **and** a raw `sqlx::SqlitePool` for `list_runs`, with a hand-written "MUST be the same pool" invariant (`§5.2`). | A raw pool re-couples the backend to agent-core's SQL schema and adds a latent footgun the type system won't catch. | Make the "optional" `TaskStore::list_for_request` a **required** R-item and drop the raw pool. The spec already lists it under "optional/recommended" — promote it. |

---

## D. Your 8 questions — direct scorecard

| # | Your criterion | Verdict | Note |
|---|---|---|---|
| 1 | backend = bridge + convenient user API | ✅ Met | Bridge role is the spine; `/api/user-request` + stats/sandbox endpoints cover it. SSE/WS reconnect-replay is the riskiest hand-rolled piece (seq-dedup + `Lagged` re-replay) — call it out as the test-heavy area. |
| 2 | sandbox code out of agent-core, keep `eos-sandbox-port` | ✅ Met | Option C is right and back-edge-free; obs normalization now lives in `eos-backend-obs`, so agent-core does not keep a sandbox wire dependency for that path. |
| 3 | sandbox independent of backend + agent-core | ✅ Met | Verified: no `sandbox/crates/*` imports `eos_runtime`/`eos_sandbox_host`/any backend crate. Coupling is one-way (backend → sandbox wire), as drawn. |
| 4 | backend owns sandbox lifecycle, wires into agent-core | ◑ Met, w/ C1+C3 | Ownership model is correct (SandboxManager → lifecycle, single provisioner S1). Blockers are the `provisioner()` visibility (C1) and the rich-args mismatch (C3), not the design. |
| 5 | naming consistency | ◑ Mostly | See §F. `eos-sandbox-api → eos-sandbox-port` rename is a real win; finish it (`SandboxApiError → SandboxPortError`). `={id}` path style is unsubstantiated. |
| 6 | Rust OOP (polymorphism / interface / api) | ✅ Strong | `dyn` is used exactly where it should be (runtime-selected providers/sinks/transports/test doubles); contracts are typed DTOs/enums/newtypes; ports are object-safe. One improvement under #8 (single injected handle). |
| 7 | SOLID / SRP | ✅ Good, 2 watch-items | `SandboxManager` trends toward a god-object (registry+lifecycle+transport+leases+teardown); keep it thin by isolating lease/refcount. The obs↔runtime sibling split is **defensible DIP**, not a violation — keep it. C4 is a small SRP leak. |
| 8 | simplify further (round trips / code / deps / classes) | ◑ Real levers exist | See §E — tiered by confidence. |

---

## E. Simplification (#8) — tiered by how sure I am

**Free win (done):**
- **Merge obs normalization into `eos-backend-obs` (B1).** Zero consumers remained
  in agent-core, so the merge removes the stray cross-workspace wire dependency
  and co-locates wire normalization with its backend owner.

**Question the spec must answer (don't cut blind):**
- **What does v1 actually surface from the audit *PULL* path?** The four stats
  endpoints (`performance/correctness/agent-runs/events`) all read the *DB* + the
  *push* sink per §2.4; the PULL ring (OCC/layer/os_resource) is **complementary**,
  not redundant — it's the only window into sandbox-internal activity, and
  `/stats/events` is the one endpoint that *might* consume it. I can't verify
  (endpoints don't exist yet). **If v1 surfaces nothing PULL-sourced**, then
  `SandboxAuditPoller` + the `audit_cursor` table + loss accounting + 3 wire ops are
  all deferrable — a large, clean cut. The spec should state this explicitly rather
  than marking `audit.*` a flat "v1 ✅."

- **Collapse the two sandbox injection seams into one handle.** Today the backend
  injects `Arc<dyn SandboxTransport>` *and* `Arc<dyn RequestProvisioner>` that
  **must** share one `ProviderRegistry` — a footgun the spec itself flags ("nothing
  in the types prevents it"). Injecting a single `Arc<SandboxManager>`-style handle
  exposing both makes the shared-registry invariant **structural** (unrepresentable
  invalid state — a #6/#7 win) and removes an injection point. *Caveat:* a clean
  supertrait upcast (`dyn SandboxRuntime → dyn SandboxTransport`) needs trait-upcasting,
  **stable only in Rust 1.86; this repo pins 1.85**. So implement it as one handle
  with an `as_transport()` accessor, not a supertrait upcast. Worth it; the footgun
  is real.

**Keep as-is (I reconsidered these — the spec is right):**
- **`run_meta` is correct, not redundant.** It looks like duplication of agent-core's
  request row, but it's the deliberate price of keeping the backend **off
  `agent-core.db`'s write path** (AC6's single-writer boundary). Eliminating it
  forces either a backend write into agent-core.db (boundary violation) or
  re-opens the GET-after-202 race. The status "merge" is a legitimate join where each
  side owns its half (backend: lifecycle status; agent-core: sandbox_id/outcome).
  Leave it.
- **Don't over-fold the `eos-backend-*` crates.** 7 crates for a v1 server is on the
  heavy side, and `store`/`obs`/`runtime` *could* merge — but the repo's CLAUDE.md
  explicitly prefers splits along ownership boundaries over LOC, and the obs↔runtime
  split has a sound DIP rationale (obs takes `Arc<dyn SandboxTransport>`, not
  `SandboxManager`, so no obs→runtime edge). Note it as a tradeoff, not a cut.

**Round-trips:** the one-shot-per-op TCP design (§11.4: N ops = N short connections,
no keepalive) is the real round-trip cost. The spec correctly defers pooling; just
flag connection reuse as the first perf lever if v1 scale is exceeded.

---

## F. Naming (#5)

- ✅ **`eos-sandbox-api → eos-sandbox-port` (R7) is the right call** — "-api" collides
  conceptually with the user-facing HTTP API and hides that it's a *port/contract*.
  **Finish the rename:** the internal `SandboxApiError` (`lib.rs:29`) should follow to
  `SandboxPortError`, or you've renamed the crate but kept the misleading type — the
  exact inconsistency the rename targets. The spec leaves this "or stay"; pick follow.
- ◑ **`={id}` path style** (`/api/user-request={id}/task_id={tid}`) is non-idiomatic
  REST (vs `/user-request/{id}/tasks/{tid}`). The spec calls it "the house
  convention," but the only prior HTTP surface (Python backend) was removed and no
  in-repo precedent anchors it. Soft flag: either point at the real precedent or
  adopt conventional path segments before the API turns user-facing.
- ✅ **Deferring the `api.v1.*` wire-op normalization is correct** — it's a
  sandbox-protocol change (op strings + `OpTable` keys + `DaemonOp::as_wire` in
  lockstep), genuinely out of backend scope. Good boundary call; the inconsistency
  (`api.v1.read_file` vs `api.isolated_workspace.enter` vs `api.audit.pull`) is real
  but not yours to fix here.
- Minor: two "runtime" crates (`eos-runtime` in agent-core, `eos-backend-runtime`
  here) — namespaced, tolerable.

---

## Bottom line

Ship the architecture. Before coding, fold in: **B1** (move obs-collector — free and
makes the spec true), **C1** (provisioner visibility = 3 changes, not 1), **C2** (sync
callback ⇒ event_log drainer task), **C3** (decide v1 sandbox_args surface), and the
two citation fixes (B2, F/`SandboxApiError`). Then answer the **audit-PULL scope
question** and consider the **single sandbox handle** — both shrink the surface
without weakening the design.
