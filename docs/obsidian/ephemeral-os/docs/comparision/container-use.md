---
title: container-use vs EphemeralOS
tags:
  - ephemeral-os
  - comparison
  - container-use
  - dagger
  - multi-agent
status: draft
---

# container-use vs EphemeralOS

A side-by-side of [Dagger's container-use](https://github.com/dagger/container-use)
and [[ephemeral-os|EphemeralOS]]. Of all the projects in the [[landscape]], this
is the **intent twin**: both run many coding agents in parallel, isolated, against
one repo, self-hosted. They differ on the thing that matters most — how durable
change from those agents is reconciled.

> [!note] Evidence basis
> EphemeralOS rows are verified against this repo's code. container-use rows come
> from its [README](https://github.com/dagger/container-use),
> [CLI reference](https://container-use.com/cli-reference), and the
> [Dagger blog](https://dagger.io/blog/agent-container-use/).

## One-line framing

- **container-use isolates, then merges with git.** Each agent gets a fresh
  Dagger container on its own git branch; you review the branch and merge it like
  a PR. The **human is the merge authority**.
- **EphemeralOS converges on a shared base.** Agents publish small changes to one
  shared overlay layerstack; OCC arbitrates at publish time. **OCC is the merge
  authority** — no human merge step in the loop.

container-use treats parallelism as "many branches to review." EphemeralOS treats
it as "one base, continuously reconciled."

## Side by side

| Axis | **container-use (Dagger)** | **EphemeralOS** |
|---|---|---|
| **Primary job** | Run parallel coding agents in isolated, reviewable environments | Isolated execution + multi-agent collaboration on a shared base |
| **Interface** | **MCP server** (agent-facing) + CLI (`container-use` / `cu`, human-facing) | Newline-delimited **JSON-RPC** + `sandbox-cli`/gateway → manager → daemon → runtime |
| **Isolation unit** | One fresh **Dagger container** per agent environment | Namespace-isolated command (mount/pid/user, optional netns) over an overlay |
| **Change primitive** | **Git commits on a per-agent branch** (`container-use/<env>`) | Content-hashed **overlayfs layers** + per-workspace upperdir diff |
| **Multi-agent model** | **Branch-per-agent**: isolate, then merge | **Shared-base**: publish to one layerstack, converge continuously |
| **Reconciliation** | Standard **git merge** at review time: `cu merge` (keeps history) / `cu apply` (staged, no commits) | `publish_changes` with **OCC reject**: `invalid_base_revision`, `source_conflict` (expected vs actual fingerprint), `protected_path` |
| **When conflicts surface** | At merge — deferred, human-gated, resolved once | At publish — immediate, machine-arbitrated, per-source-path |
| **Review / visibility** | `cu checkout`, `cu log --patch`, `cu watch`, `cu terminal` (drop into the agent's container) | Observability snapshots/traces, service graph, per-layer attribution |
| **Network isolation** | Per Dagger container | First-class `shared` (host netns) vs `isolated` (own netns) profile per workspace |
| **Session model** | Environment = a git branch + container, persistent until deleted (`cu delete`) | One-shot ephemeral workspace, or caller-owned `create`/`destroy_workspace_session`; plus command sessions |
| **Agent adoption** | **Drop-in for existing agents** (Claude Code, Cursor, any MCP client) | Build against the RPC protocol |
| **Backend / footprint** | Dagger engine (Docker under it) | Privileged Linux container + kernel overlayfs |
| **Language / license** | Go, **Apache-2.0**, public | Rust, internal, early |

## Where it actually matters

### Reconciliation — git-at-review vs OCC-at-publish

This is the whole difference. container-use **defers** conflict to merge time:
each agent's work sits on a branch, you `cu checkout` to inspect and `cu merge` /
`cu apply` when you're satisfied. Conflicts are resolved once, deliberately, by a
human with full git tooling. EphemeralOS **surfaces** conflict at publish: a
workspace publishes against the base revision it started from, and OCC rejects on
`source_conflict` with the exact expected-vs-actual fingerprint. No human in the
loop, finer granularity (per source path, not per branch), but it pushes the cost
of a rejected publish back onto the agent.

Same tradeoff from the [[landscape]]: deferred-deliberate-merge vs
continuous-automated-convergence. container-use is the mature, human-friendly end;
EphemeralOS bets the machine can arbitrate well enough to skip the review gate.

### Isolation cost — container-per-agent vs overlay-over-shared-base

container-use spins a full container per environment; EphemeralOS mounts a thin
overlay (upperdir = diff) over one shared layerstack. For many short-lived agents,
overlay upperdirs are cheaper in space than container-per-agent, and agents read
each other's *published* work through the shared lower layers without a checkout.
container-use's containers are more strongly isolated and need no overlayfs/
privileged host, but agents never see each other until a branch is merged.

### Adoption — MCP drop-in vs custom protocol

container-use's biggest practical edge: it's an MCP server, so Claude Code, Cursor,
and any MCP agent use it **today** with no code. EphemeralOS is a protocol you
build a client against. If the question is "why not just use container-use," the
honest answer is adoption and a battle-tested git review workflow — EphemeralOS
has to justify itself on the shared-base convergence model, not on ergonomics.

### What each genuinely wins at

- **container-use wins:** drop-in MCP adoption with existing agents; human-grade
  git review (`cu checkout` / `cu merge` / `cu apply`); `cu terminal` to take over
  an agent's exact environment; no privileged host or overlayfs needed; mature,
  public, Apache-2.0.
- **EphemeralOS wins:** continuous shared-base visibility (no merge step to see a
  peer's published work); OCC at the filesystem layer with per-path conflict
  detection (no git required); overlay layers cheaper than container-per-agent;
  first-class network profiles; one-shot vs session semantics; programmatic RPC
  built for automation rather than human-gated review.

### Compose or compete

These compete more directly than [[agentfs|AgentFS]] does — same intent, same
self-hosted niche. The fork in the road is governance of change: if you want a
**human reviewing branches like PRs**, container-use is the stronger, readier
answer. If you want **agents reconciling onto a live shared base without a human
merge gate**, that's EphemeralOS's reason to exist — and the part it has to prove.

## Sources

- [dagger/container-use (GitHub)](https://github.com/dagger/container-use)
- [container-use CLI reference](https://container-use.com/cli-reference)
- [Dagger blog: Containing Agent Chaos](https://dagger.io/blog/agent-container-use/)
