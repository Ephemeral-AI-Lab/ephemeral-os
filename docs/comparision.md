# EphemeralOS GAN + OCC Competitive Comparison

## Scope

This document compares the target EphemeralOS design:

- GAN-style task graph with executor, planner, and evaluator roles
- OCC plus overlay workspace routing
- scout-collected file memory and proposed read/edit prehooks
- harness-local task context with parent-goal injection
- executor-first entry path for trivial tasks

The comparison focuses on high-parallelism, long-horizon multiagent software
engineering. Scores are architecture-fit ratings, not brand maturity ratings.

Scale:

- 5: native and enforced
- 4: strong
- 3: supported, but mostly lead/prompt/convention driven
- 2: limited
- 1: weak or absent

## Executive Rating

| System | Overall | Why |
|---|---:|---|
| EphemeralOS GAN + OCC | 4.6 | Strongest harness architecture: recursive task graph, evaluator gates, scoped failure, shared workspace with OCC, and file-local memory. Product polish still behind. |
| Devin Advanced / Managed Devins | 4.3 | Strong productized long-horizon async agent system with managed parallel sessions, playbooks, knowledge, session analysis, and scheduling. More isolated-session oriented. |
| Qoder Experts Mode | 4.0 | Strong expert-team UX: Team Lead, role experts, planning, parallel execution, QA/review. Runtime semantics are less transparent. |
| Claude Code Agent Teams | 3.9 | Strong native team coordination: shared task list, dependencies, direct messaging, task locks, hooks. Workspace conflict model is less advanced than OCC. |
| OMC | 3.8 | Strong iteration culture: Ralph, Team, Ultrawork, many specialists, zero-config. More prompt/plugin/protocol driven than runtime-invariant driven. |
| OpenCode agents | 3.2 | Excellent configurable agents and permissions. Good subagent fanout, but not a full long-horizon multiagent orchestration graph. |
| GitHub Copilot cloud agent | 3.1 | Good async PR agent with branch/PR transparency, custom agents, hooks, memory. More issue-to-PR oriented than multiagent collaborative. |

## High-Parallelism Collaboration

| Capability | EphemeralOS | Devin | Qoder | Claude Teams | OMC | OpenCode | Copilot Cloud |
|---|---:|---:|---:|---:|---:|---:|---:|
| Parallel worker fanout | 5 | 5 | 4 | 4 | 4 | 3 | 3 |
| Typed dependency scheduling | 5 | 4 | 3 | 4 | 2 | 2 | 2 |
| Direct sibling communication | 3 | 4 | 4 | 5 | 4 | 2 | 2 |
| Shared live workspace visibility | 5 | 2 | 4 | 4 | 4 | 3 | 1 |
| Workspace conflict control | 5 | 4 | 2 | 3 | 2 | 2 | 4 |
| Same-file non-overlap source edits | 5 | 2 | 1 | 1 | 1 | 1 | 1 |
| Long shell command handling | 4 | 4 | 3 | 3 | 3 | 3 | 3 |
| Install/cache behavior | 4 | 3 | 3 | 2 | 2 | 2 | 3 |

EphemeralOS stands out because most systems choose either isolated branches/VMs
or raw shared workspace collaboration. EphemeralOS aims for shared workspace
visibility with source-write discipline. Gitincluded files go through OCC,
where semantic edits can merge non-overlapping same-file edits and stricter
overlay shell commits fail first-writer-wins on base mismatch. Gitignored files
are direct-merged with last-writer-wins behavior, which is pragmatic for
`.venv`, `node_modules`, caches, test artifacts, and long shell commands.

Important caveat: gitignored files are not OCC-gated. They are overlay-classified
and direct-merged. This supports real build/install workflows, but concurrent
installs into the same ignored prefix can interleave per file. The correct claim
is pragmatic live-shell semantics, not fully coherent package-level transactions.

## Long-Horizon Task Handling

| Capability | EphemeralOS | Devin | Qoder | Claude Teams | OMC | OpenCode | Copilot Cloud |
|---|---:|---:|---:|---:|---:|---:|---:|
| Recursive decomposition | 5 | 4 | 4 | 3 | 4 | 2 | 2 |
| Generous plan depth | 5 | 4 | 4 | 3 | 5 | 2 | 2 |
| Phase-by-phase implementation | 5 | 4 | 4 | 3 | 5 | 3 | 3 |
| Verification loop | 5 | 4 | 4 | 3 | 5 | 2 | 3 |
| Recovery after partial failure | 5 | 4 | 3 | 3 | 4 | 2 | 3 |
| Context drift resistance | 5 | 4 | 4 | 3 | 3 | 3 | 3 |
| Trivial task efficiency | 4 | 3 | 3 | 4 | 4 | 5 | 4 |

The executor-first root is important. It avoids planner tax on trivial work,
while still allowing escalation into a planner harness when the task becomes
complex. Parent input injection at evaluator time acts like a skip connection:
the evaluator sees the original objective, not only compressed child summaries.
That directly addresses objective drift across deep multi-agent handoff chains.

Depth should remain generous. Ralph-style workflows have shown that persistent
multi-phase iteration can be useful. The better guardrail is not a shallow max
depth, but convergence telemetry: lineage depth, repeated replans, evaluator
rejection cycles, wall-clock and budget consumption, and repeated workspace
conflicts.

## Harness Engineering

| Capability | EphemeralOS | Devin | Qoder | Claude Teams | OMC | OpenCode | Copilot Cloud |
|---|---:|---:|---:|---:|---:|---:|---:|
| Typed terminal tools | 5 | 3 | 3 | 3 | 2 | 3 | 3 |
| Explicit task graph invariants | 5 | 4 | 3 | 4 | 2 | 2 | 2 |
| Evaluator as graph gate | 5 | 4 | 4 | 3 | 4 | 2 | 3 |
| Local vs global failure semantics | 5 | 4 | 3 | 3 | 3 | 2 | 3 |
| Persistent run/task audit | 5 | 5 | 4 | 3 | 2 | 2 | 4 |
| Tool permission model | 4 | 4 | 4 | 4 | 3 | 5 | 4 |
| Hook/prehook extensibility | 5 | 4 | 3 | 4 | 3 | 4 | 4 |
| Workspace transaction model | 5 | 4 | 2 | 2 | 2 | 2 | 4 |
| Observable telemetry surface | 4 | 5 | 4 | 3 | 2 | 3 | 4 |

EphemeralOS is closer to a distributed runtime than a prompt pack. Terminal
contracts, graph ownership, file-memory gates, OCC, evaluator closure, and
persistence are enforceable harness mechanisms. That is materially stronger
than relying on a lead agent to coordinate carefully through prose.

Nested evaluator failure should be local to the harness graph. A child graph's
evaluator failure marks that graph's parent executor as failed. If that parent
executor belongs to an outer graph, the outer graph should observe it as one
failed child and let its own evaluator decide whether the broader parent goal is
recoverable. Failure should propagate one graph boundary at a time, not jump
straight to root unless the failed parent is the root.

## Memory And Context Architecture

| Capability | EphemeralOS | Devin | Qoder | Claude Teams | OMC | OpenCode | Copilot Cloud |
|---|---:|---:|---:|---:|---:|---:|---:|
| File-local memory | 5 proposed | 4 | 4 | 3 | 2 | 2 | 3 |
| Explorer/scout note capture | 5 proposed | 4 | 4 | 3 | 3 | 3 | 3 |
| Read prehook memory retrieval | 5 proposed | 3 | 3 | 2 | 2 | 2 | 3 |
| Edit-reasoning memory gate | 5 proposed | 3 | 3 | 2 | 2 | 2 | 3 |
| Cross-session learning | 4 | 5 | 4 | 3 | 2 | 2 | 4 |
| Staleness control | 3 | 4 | 3 | 2 | 2 | 2 | 3 |

Scout file memory is strategically strong. It turns exploration from private
transcript into durable repo knowledge. Read prehooks can make agents inspect
file notes before reading or editing. Edit gates after repeated edits can
capture why an agent is changing a file, not just what changed.

The missing piece is staleness metadata. Each file note should carry at least:

- file path
- content hash or generation
- task id
- agent id
- note type
- timestamp
- confidence or evidence level

Without staleness control, old scout notes become another source of drift.

## Product And UX Maturity

| Capability | EphemeralOS | Devin | Qoder | Claude Teams | OMC | OpenCode | Copilot Cloud |
|---|---:|---:|---:|---:|---:|---:|---:|
| User-facing polish | 2 | 5 | 5 | 4 | 4 | 4 | 4 |
| Setup simplicity | 2 | 4 | 4 | 3 | 5 | 4 | 4 |
| Visual team monitoring | 3 | 5 | 5 | 4 | 3 | 3 | 4 |
| Enterprise workflow integration | 2 | 5 | 4 | 3 | 2 | 2 | 5 |
| Open inspectability | 5 | 2 | 2 | 3 | 4 | 5 | 3 |
| Research/runtime flexibility | 5 | 3 | 3 | 3 | 4 | 4 | 2 |

EphemeralOS' weakness is product surface and operational hardening. Devin,
Qoder, Copilot, and Claude Teams are easier to sell to a normal engineering team
because they expose polished workflows. EphemeralOS is stronger as a
research/runtime substrate.

## Competitive Positioning

| Competitor Pattern | What They Usually Do | EphemeralOS Difference |
|---|---|---|
| Worktree / isolated VM agents | Avoid conflicts by isolating agents, merge later. | Shared live workspace with OCC, so agents can see committed sibling work quickly. |
| Lead-agent teams | Lead decomposes and coordinates mostly through messages/task lists. | Task graph and evaluator closure are runtime invariants. |
| Prompt-pack specialists | Roles and workflows are prompt/protocol-driven. | Role tools and graph transitions are enforced by the harness. |
| Async PR agents | One task becomes one branch/PR. | One request can become recursive harness graphs with local evaluators and recoveries. |
| Loop agents like Ralph | Iterate until verified. | Keep the iteration strength, but add graph-local context, failure evidence, and OCC-aware workspace semantics. |

## Best System By Goal

| Goal | Best System | Runner-Up | Why |
|---|---|---|---|
| Maximum enforced parallel coding harness | EphemeralOS | Devin | EphemeralOS has DAG plus evaluator plus OCC; Devin has strong managed sessions but is more isolation-oriented. |
| Best polished product today | Devin / Qoder | Claude Teams | Better UX, observability, and enterprise workflow surface. |
| Best live team collaboration UX | Claude Teams | Qoder | Shared task list, direct messaging, teammate sessions. |
| Best persistent iteration loop | OMC Ralph | EphemeralOS | Ralph is proven as a relentless mode; EphemeralOS can surpass it if evaluator recovery lands cleanly. |
| Best permissions/config model | OpenCode | Copilot Cloud | OpenCode's agent and permission config is clean and explicit. |
| Best GitHub issue-to-PR automation | Copilot Cloud | Devin | Native GitHub environment, branch/PR automation, logs. |
| Best research substrate | EphemeralOS | OpenCode | EphemeralOS exposes the right primitives to experiment with runtime semantics. |

## Final Ratings

| Dimension | Rating |
|---|---:|
| High-parallelism architecture | 9.3 / 10 |
| Long-horizon architecture | 9.1 / 10 |
| Harness engineering depth | 9.5 / 10 |
| Product readiness today | 5.5 / 10 |
| Competitive potential if implemented cleanly | 9.0 / 10 |

## Verdict

EphemeralOS GAN + OCC stands out because it combines four control loops that are
usually separate:

1. The task graph controls who works and when.
2. The OCC/overlay layer controls how concurrent edits land.
3. The file-memory layer controls what agents know before touching files.
4. The evaluator layer controls whether local work still satisfies the parent
   objective.

The executor-first path keeps trivial tasks cheap, while recursive planner
harnesses support long-horizon phase-by-phase work. If implemented cleanly, the
design can make parallel agents visible to each other, conflict-aware,
memory-bearing, locally recoverable, and still efficient on small tasks.

The main risk is implementation complexity. The main opportunity is that no
competitor in this set appears to combine recursive evaluator graphs, shared
live workspace OCC, file-local memory hooks, and executor-first escalation as
one coherent runtime.

## Sources

- Claude Code Agent Teams: https://code.claude.com/docs/en/agent-teams
- Qoder Experts Mode: https://docs.qoder.com/user-guide/chat/experts-mode
- OpenCode agents: https://opencode.ai/docs/agents/
- OMC: https://ohmyclaudecode.com/
- Devin advanced capabilities: https://docs.devin.ai/work-with-devin/advanced-capabilities
- GitHub Copilot cloud agent: https://docs.github.com/en/copilot/concepts/agents/cloud-agent/about-cloud-agent

