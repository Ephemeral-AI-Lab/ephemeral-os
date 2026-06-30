---
title: CubeSandbox vs EphemeralOS
tags:
  - ephemeral-os
  - comparison
  - cubesandbox
  - microvm
  - e2b
status: draft
---

# CubeSandbox vs EphemeralOS

A side-by-side of [TencentCloud/CubeSandbox](https://github.com/TencentCloud/CubeSandbox)
and [[ephemeral-os|EphemeralOS]]. CubeSandbox is the interesting one if the
question is **fast hardware-isolated sandbox infrastructure**. It is not trying
to solve EphemeralOS's shared-base collaboration problem.

> [!note] Evidence basis
> EphemeralOS rows are verified against this repo's code. CubeSandbox rows come
> from the CubeSandbox README/docs/changelog and examples, current as of
> 2026-06-30.

## One-line framing

- **CubeSandbox is an E2B-compatible microVM sandbox service.** Its bet is:
  *make hardware-isolated sandboxes fast, dense, and easy to adopt by copying the
  E2B client surface.*
- **EphemeralOS is a shared-base command/workspace runtime.** Its bet is:
  *keep agents outside, run commands inside thin overlays, and publish changes
  back through LayerStack/OCC/merge.*

CubeSandbox is cool infrastructure. EphemeralOS is cool reconciliation.

## Side by side

| Axis | **CubeSandbox** | **EphemeralOS** |
|---|---|---|
| **Primary job** | Fast, hardware-isolated sandbox service for AI agents | Fast CLI/runtime substrate for external agents and humans |
| **Adoption strategy** | E2B-compatible API/SDK surface; swap endpoint/env vars | Native `sandbox-cli` and newline-delimited JSON protocol |
| **Isolation unit** | KVM MicroVM with its own guest kernel | Namespace-isolated command/workspace inside a privileged Docker sandbox |
| **Runtime focus** | Boot, pause/resume, snapshot, clone, rollback | Exec, PTY/stdin/stdout, workspace sessions, LayerStack publish |
| **State model** | MicroVM/template snapshots and CubeCoW clone/rollback | Overlay upperdir diff + immutable content-hashed layers |
| **Multi-agent model** | Many isolated sandboxes; clone/rollback/fork state | Many workspaces converge onto one shared moving base |
| **Reconciliation** | Not the center; caller still owns durable merge semantics | Core feature: publish-time OCC/merge |
| **Browser support** | Playwright/Chromium inside the MicroVM via examples | Not core; should remain opt-in |
| **Egress/secrets** | CubeEgress, domain allowlists, credential injection/audit | Future layer, after core exec/file/session path is proven |
| **Host dependency** | Linux/KVM-style deployment, single-node or clustered | Linux Docker + overlayfs today |
| **Novelty** | Fast/dense KVM microVM service with E2B compatibility | Shared overlay LayerStack with publish-time convergence |

## Why CubeSandbox uses E2B compatibility

E2B compatibility is not mainly about agent tools. It is an adoption shortcut.
Lots of AI code-execution products already know the E2B SDK shape. CubeSandbox
lets those users test a self-hosted microVM backend by changing the endpoint
instead of rewriting application code.

That has a cost: the product is pulled toward E2B's mental model. The client API
is a sandbox service API, not a deep agent-native filesystem/merge substrate.
For CubeSandbox, that is a good trade. For EphemeralOS, it would blur the point.

## Playwright is useful but not a reason to copy it

CubeSandbox's browser examples make sense when the browser must live inside the
same isolated MicroVM as the workload. That gives clean state isolation and a
remote CDP boundary.

For EphemeralOS, default in-sandbox Playwright would mostly add burden:

- larger images;
- slower setup;
- more package/font/browser drift;
- more networking and forwarding cases;
- less focus on fast shell/file/edit loops.

Add it only when a real workflow requires sandbox-local browser state.

## What CubeSandbox genuinely wins at

- **Hardware isolation:** each sandbox has its own guest kernel.
- **Adoption:** E2B compatibility lowers migration cost.
- **Runtime state operations:** snapshot, clone, rollback, pause/resume are
  first-class product concepts.
- **Security product layer:** egress control and credential injection are already
  part of the story.

## What EphemeralOS should not copy

- Do not chase E2B compatibility unless the goal becomes E2B migration.
- Do not put the agent into the sandbox.
- Do not add a multi-language SDK before the CLI protocol is enough.
- Do not add browser automation as a default workload.
- Do not compete on microVM boot unless EphemeralOS becomes a sandbox provider
  instead of a collaboration substrate.

## The useful takeaway

CubeSandbox is the stronger project if the buyer wants **fast, secure,
self-hosted E2B-style sandboxes**. EphemeralOS is stronger only if the buyer
wants **external agents collaborating on one shared codebase without branch
drift**.

Those are different products.

## Sources

- [TencentCloud/CubeSandbox](https://github.com/TencentCloud/CubeSandbox)
- [CubeSandbox architecture overview](https://cubesandbox.com/architecture/overview.html)
- [CubeSandbox examples](https://cubesandbox.com/guide/tutorials/examples.html)
- [CubeSandbox v0.1.0 changelog](https://github.com/TencentCloud/CubeSandbox/blob/master/docs/changelog/v0.1.0.md)
- [CubeSandbox releases](https://github.com/TencentCloud/CubeSandbox/releases)
