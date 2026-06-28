---
title: AgentFS vs EphemeralOS
tags:
  - ephemeral-os
  - comparison
  - agentfs
  - layerstack
status: draft
---

# AgentFS vs EphemeralOS

A side-by-side of [Turso's AgentFS](https://github.com/tursodatabase/agentfs) and
[[ephemeral-os|EphemeralOS]]. They share the copy-on-write-overlay primitive but
solve **different layers of the stack**.

> [!note] Evidence basis
> EphemeralOS rows are verified against this repo's code. AgentFS rows come from
> its README and the [overlay blog post](https://turso.tech/blog/agentfs-overlay);
> parts of its sandbox are explicitly *experimental*, so treat those as stated
> design, not verified behavior.

## One-line framing

- **AgentFS is a storage substrate** — *where an agent's file writes and tool
  calls go, so you can query, snapshot, and ship them.* A filesystem in a SQLite
  file.
- **EphemeralOS is an execution substrate** — *how you run a command/agent in
  isolation against a shared base and reconcile its changes.* A sandbox runtime
  with a daemon and an RPC protocol.

AgentFS answers "where does the agent's state live and how do I audit it."
EphemeralOS answers "how do I run the agent safely and merge what it did."

## Side by side

| Axis | **AgentFS (Turso)** | **EphemeralOS** |
|---|---|---|
| **Primary job** | Storage + audit + reproducibility for agent file state | Isolated execution + multi-agent collaboration sandbox |
| **Form factor** | Embeddable SDK (TS/Python/Rust) + CLI + single `.db` file; in-process, even browser/WASM | Client/server: `sandbox-cli`/gateway → manager → in-container daemon → runtime, over newline-delimited JSON-RPC |
| **CoW backend** | Userspace overlay (FUSE on Linux, NFS on macOS) with a **SQLite/Turso writable delta**; copy-up whole file into the DB on first write | **Kernel overlayfs**: content-addressed layerstack (CAS lowerdirs on disk) + overlay upperdir on a disk-backed Docker volume |
| **Unit of change** | Rows in a SQLite delta (files, KV state, tool calls all in one DB) | Filesystem **layers** (immutable, content-hashed) + per-workspace upperdir diff |
| **What's isolated** | The **filesystem** ("system-wide, cannot be bypassed"); plus mount/user ns + `sandbox-exec` in the experimental sandbox | The **whole execution env**: mount/pid/user namespaces per command, optional network namespace, privileged Linux container as the boundary |
| **Network isolation** | Not addressed | First-class: `shared` (host netns) vs `isolated` (own netns) per workspace |
| **Session model** | `AgentFS.open({id})` → `.agentfs/<id>.db`; ephemeral in-memory if no id; `agentfs run --session <ID>`; sessions keyed to git-branch names | `create`/`destroy_workspace_session` (caller-owned, overlay persists) + one-shot ephemeral workspace; plus command sessions for long-running commands (stdin/stdout streaming) |
| **Multi-agent model** | **Isolation-first**: each agent gets its *own* delta DB (branch-per-agent). No shared mutable base | **Shared-base-first**: agents publish to a *shared* layerstack; designed for convergence on a mainline |
| **Merge / concurrency** | None documented — snapshot & copy, not merge; no OCC | `publish_changes` with **OCC reject**: `invalid_base_revision`, `source_conflict` (expected vs actual fingerprint), `protected_path`. Squash planned |
| **Auditability** | **Headline feature**: every file op + tool call in a queryable SQL timeline (`agentfs timeline`) | Observability snapshots/traces, service graph, per-layer attribution — operation-level, not a SQL-queryable file-op log |
| **Reproducibility** | `cp agent.db snapshot.db` — whole-state, single file | Layerstack snapshots become future lower layers; no single-file capture |
| **Footprint / portability** | Very light: single file, in-process, no container, runs in the browser | Heavy: needs a **privileged Linux container + kernel overlayfs**; server-shaped |
| **Core language** | Rust core; SDKs TS/Python/Rust (+ Go/C) | Rust workspace; JSON-RPC so any client |
| **License / maturity** | MIT, public, multi-SDK | Internal, early |

## Where it actually matters

### Backend tradeoff — SQLite delta vs kernel overlayfs

This is the root of every other difference. AgentFS putting writes in SQLite
buys *queryable audit, single-file portability, and browser/WASM* — things
EphemeralOS structurally can't do. EphemeralOS using kernel overlayfs buys
*native exec performance, real process isolation, and reuse of container
tooling* — at the cost of requiring a privileged Linux host. AgentFS isolates the
**filesystem**; EphemeralOS isolates the **whole environment**.

### Collaboration philosophy

AgentFS lands squarely in the "CoW-but-isolated" camp: each agent its own delta
DB, no shared base, no merge-back. It's branch-per-agent done in SQLite instead
of git worktrees. EphemeralOS makes the contrarian bet — a *shared* layerstack
with OCC-guarded publish, optimizing for convergence rather than isolation.
AgentFS doesn't try to merge agents because that's not its job; it gives you
`diff`/`timeline`/`snapshot` and leaves reconciliation to you.

### What each genuinely wins at

- **AgentFS wins:** SQL-queryable audit of every file op + tool call; trivial
  snapshot/reproduce (`cp`); embeddable anywhere including the browser;
  multi-language SDKs. Its audit trail beats EphemeralOS's observability at the
  storage layer.
- **EphemeralOS wins:** real execution isolation (namespaces + network
  profiles), shared-base multi-agent convergence with OCC publish, one-shot vs
  session semantics, interactive command sessions, and overlayfs-native
  performance for actual command workloads.

### They compose more than they compete

AgentFS is the kind of thing you'd reach for *as a backing store or audit layer*;
EphemeralOS is the runtime that schedules isolated execution and reconciles
changes onto a mainline. AgentFS-grade SQL-queryable audit inside EphemeralOS
would be a feature grafted onto the layerstack/observability layer — not a reason
to swap substrates. Conversely, AgentFS has no answer for "run this agent in an
isolated network namespace and publish its diff against a moving shared base,"
which is EphemeralOS's whole center.

## Sources

- [tursodatabase/agentfs (GitHub)](https://github.com/tursodatabase/agentfs)
- [Turso blog: AgentFS overlay filesystem](https://turso.tech/blog/agentfs-overlay)
