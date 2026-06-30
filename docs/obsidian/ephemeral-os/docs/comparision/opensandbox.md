---
title: OpenSandbox vs EphemeralOS
tags:
  - ephemeral-os
  - comparison
  - opensandbox
  - sandbox-platform
status: draft
---

# OpenSandbox vs EphemeralOS

A side-by-side of [OpenSandbox](https://github.com/opensandbox-group/OpenSandbox)
and [[ephemeral-os|EphemeralOS]]. OpenSandbox is useful platform plumbing, but it
does not occupy EphemeralOS's shared-base/OCC square. It sits in the
**general-purpose sandbox control-plane** camp: SDKs, lifecycle APIs,
Docker/Kubernetes runtimes, browser/desktop examples, egress policy, and
credential injection.

> [!note] Evidence basis
> EphemeralOS rows are verified against this repo's code. OpenSandbox rows come
> from its README/docs plus source inspection of the runtime resolver, Docker/K8s
> providers, egress sidecar, and credential-vault path, current as of 2026-06-30.

## One-line framing

- **OpenSandbox packages sandbox infrastructure for product developers.** It
  asks: *how do I create, operate, and secure disposable environments through a
  common SDK/API across Docker and Kubernetes?*
- **EphemeralOS builds a shared execution substrate for external agents.** It
  asks: *how do many isolated workspaces publish changes back to one moving base
  without branch-per-agent drift?*

OpenSandbox is broad. EphemeralOS is narrow. The narrowness is the point.

## Side by side

| Axis | **OpenSandbox** | **EphemeralOS** |
|---|---|---|
| **Primary job** | General-purpose sandbox platform for AI applications | Fast command/workspace runtime for external agents over a shared base |
| **Interface** | Multi-language SDKs, CLI, MCP, OpenAPI-style specs | `sandbox-cli` + newline-delimited JSON protocol |
| **Main user** | Application/platform developer integrating sandbox lifecycle into a product | Agent or human operator controlling execution from the host |
| **Agent location** | Not prescribed; SDK controls sandboxes from outside, examples may run tools inside | Agent stays outside; sandbox runs commands and services |
| **Runtime backend** | Docker and Kubernetes | Docker-backed sandbox today |
| **Secure runtimes** | Integration hooks for Docker runtime names / K8s `RuntimeClass` (`gVisor`, `Kata`, `Firecracker`-style) | Not a current product axis |
| **Command/file tools** | Built-in command, filesystem, code interpreter APIs | Command/session surface plus layerstack-backed workspace capture/publish |
| **Browser/GUI** | Examples for Chrome, Playwright, VNC, VS Code | Not core; add only when sandbox-local browser state is required |
| **Egress/secrets** | Real egress sidecar and Credential Vault path | Future security layer if untrusted code needs network/secrets |
| **Multi-agent model** | Many sandboxes; reconciliation is left to the caller | Many workspaces converge through LayerStack publish |
| **Reconciliation** | No special merge model | OCC/merge at publish time against a moving shared base |
| **Novelty** | Productized control plane and security feature bundle | Shared overlay LayerStack + publish-time reconciliation |

## Where it actually matters

### Feature platform vs execution thesis

OpenSandbox has a lot: SDKs, CLI, MCP, Docker, Kubernetes, browser examples,
desktop examples, egress policy, credential vault, and secure-runtime knobs.
Most of those features are useful, but none is the reason EphemeralOS exists.
They are platform packaging around ordinary sandbox lifecycle and execution.

EphemeralOS should not compete by matching the checklist. Its useful thesis is
smaller: **fast bash/file/session control from outside the sandbox, then durable
publish into one shared LayerStack.**

### Secure runtime support is pass-through

OpenSandbox really has Docker and Kubernetes backends. Its secure runtimes are
not bundled isolation engines. The server resolves configured runtime names and
validates that Docker or the Kubernetes cluster already exposes them. In plain
terms: OpenSandbox can ask for `runsc`, `kata-runtime`, `kata-qemu`, or
`kata-fc`; the host/cluster must provide the actual runtime.

That is the right shape to borrow later: a runtime flag, not an owned
hypervisor project.

### Egress and Credential Vault are the strongest pieces

The egress sidecar and Credential Vault are the most useful OpenSandbox ideas
for EphemeralOS. They solve real problems once untrusted code needs outbound
network access:

- restrict destinations by policy;
- keep real credentials out of the sandbox environment, files, commands, and
  logs;
- inject credentials at the outbound request boundary.

But they are not free. OpenSandbox's Credential Vault rides on the egress sidecar
and transparent proxy path. That is a security layer, not a core execution
primitive.

### SDK and Playwright are mostly noise for EphemeralOS

OpenSandbox's SDKs make sense for product applications. They do not make
EphemeralOS more agent-native if the agent already runs outside the sandbox and
can call a CLI. MCP can be an adapter later; a first-class SDK is not needed
until there are non-agent app developers to serve.

Likewise, in-sandbox Playwright is valuable only when the browser state must be
part of the sandbox. Otherwise it adds image size, install burden, startup cost,
and more failure modes.

## What to borrow

- **Borrow:** egress policy, credential proxy/vault, runtime-class pass-through,
  and a small MCP adapter when the CLI is stable.
- **Skip:** multi-language SDKs, in-sandbox Playwright as a default, Kubernetes
  before one-node Docker is excellent, and feature flags for runtimes you cannot
  test.

## Sources

- [OpenSandbox overview](https://open-sandbox.ai/overview/home)
- [opensandbox-group/OpenSandbox](https://github.com/opensandbox-group/OpenSandbox)
- [OpenSandbox secure container guide](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/guides/secure-container.md)
- [OpenSandbox egress component](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/components/egress.md)
- [OpenSandbox Credential Vault guide](https://github.com/opensandbox-group/OpenSandbox/blob/main/docs/guides/credential-vault.md)
- [OpenSandbox runtime resolver](https://github.com/opensandbox-group/OpenSandbox/blob/main/server/opensandbox_server/services/runtime_resolver.py)
