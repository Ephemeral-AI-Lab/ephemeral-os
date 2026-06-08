# Phase 00 - Architecture Lock Spec

Status: Draft
Date: 2026-06-09
Owner: agent-core architecture

## Scope

This phase freezes the target architecture before implementation starts. No file
moves, crate renames, or code edits should begin until this spec is accepted.

Phase 0 exists because the cleanup is intentionally destructive. The target
crate map, naming vocabulary, service rule, module budget, and parallel work
lanes must be stable before agents start editing disjoint crates.

## Locked Decisions

| Decision | Target |
| --- | --- |
| external facade crate | `eos-agent-core` |
| HTTP/path router | outside `agent-core`; belongs in `backend-server` |
| removed runtime crate | `eos-runtime` folds into `eos-agent-core/src/runtime/` |
| only allowed port crate | `eos-sandbox-port` |
| service meaning | sibling-crate consumed callable surface |
| runtime wiring vocabulary | `runtime`, `handles`, `catalog`, `sink` |
| forbidden vocabulary | `composition`, `deps`, `runtime_services` |
| final crate count | 11 |
| final module count | 180-200 |

## Final Crate Map

```text
agent-core/crates/
├── eos-agent-core/
├── eos-agent-run/
├── eos-engine/
├── eos-tool/
├── eos-workflow/
├── eos-types/
├── eos-config/
├── eos-db/
├── eos-llm-client/
├── eos-sandbox-port/
└── eos-testkit/
```

Retired crates:

```text
eos-runtime
eos-agent-api
eos-agent-ports
eos-tool-ports
eos-agent-message-records
eos-tools
eos-agent-runner
eos-skills
eos-plugin-catalog
```

## Boundary Rules

### eos-agent-core

Owns the user-facing Rust facade and hidden request runtime wiring.

```text
eos-agent-core/src/
├── lib.rs
├── error.rs
├── model.rs
├── agent_core.rs
├── request.rs
├── state.rs
├── cancellation.rs
├── runtime.rs
└── runtime/
    ├── builder.rs
    ├── database.rs
    ├── engine.rs
    ├── sandbox.rs
    ├── agents.rs
    ├── audit.rs
    └── plugins.rs
```

Does not own HTTP routing. Does not define domain logic owned by engine, tool,
workflow, run lifecycle, DB, or sandbox crates.

### eos-agent-run

Owns agent-run lifecycle: spawn, wait, poll, cancel, active runs, persistence,
completion handoff.

### eos-engine

Owns execution only: loop, turns, stream events, message records, midflight
printing, background accounting, and sibling-facing engine services.

### eos-tool

Owns the tool framework, concrete model-callable tools, hooks, registry, skill
loading, and sibling-facing tool services.

### eos-workflow

Owns workflow lifecycle and sibling-facing workflow services.

### eos-llm-client

Owns outbound provider clients. It uses `client.rs`, `providers.rs`, and
`stream.rs`, not `services.rs`.

## Vocabulary Rules

| Word | Status | Rule |
| --- | --- | --- |
| `api` | restricted | external contract language only; not the facade crate name |
| `router` | banned in agent-core | HTTP/path routing belongs in backend-server |
| `service` | restricted | only sibling-crate consumed callable surfaces |
| `runtime` | allowed | private request-running wiring inside `eos-agent-core` |
| `handles` | allowed | grouped concrete resource handles |
| `catalog` | allowed | loaded/static definitions |
| `sink` | allowed | write-only event/audit output |
| `client` | allowed | outbound external clients |
| `port` | restricted | only `eos-sandbox-port` |
| `composition` | banned | too vague and visually noisy |
| `deps` | banned | implementation leakage |
| `runtime_services` | banned | old mixed naming |

## Parallel Work Lanes

| Lane | Scope | Can run after |
| --- | --- | --- |
| Guardrails | `workspace-guard` tests | Phase 0 accepted |
| Tool | `eos-tool` and folded tool/skill crates | Phase 0 accepted |
| Engine/run | `eos-engine`, `eos-agent-run`, message records | Phase 0 accepted |
| Agent core/workflow/types | `eos-agent-core`, `eos-workflow`, `eos-types` | Phase 0 accepted |
| Integration | root `Cargo.toml`, dependency DAG, public exports | after lane contracts are drafted |

Only the integration lane should edit root `Cargo.toml`, shared workspace
dependencies, or cross-crate public re-export surfaces during the destructive
move.

## Progress Tracker

| Item | Status |
| --- | --- |
| Approve `eos-agent-core` over `eos-agent-api` / router | Not started |
| Approve final 11-crate map | Not started |
| Approve retired crate list | Not started |
| Approve vocabulary rules | Not started |
| Approve service sibling-use rule | Not started |
| Approve module budget | Not started |
| Approve parallel work lanes | Not started |
| Approve verification ladder | Not started |

## Acceptance Criteria

- The target facade crate is `eos-agent-core`.
- No target crate or module is named router.
- `eos-runtime` is not a target crate.
- `composition`, `deps`, and `runtime_services` are rejected vocabulary.
- The final crate map contains exactly 11 crates.
- Every target `services.rs` has a named sibling-crate consumer.
- The implementation phases may begin without reopening naming or ownership
  decisions.
