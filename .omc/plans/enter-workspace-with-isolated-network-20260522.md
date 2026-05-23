# Plan: `enter_isolated_workspace` (pinned ephemeral workspace mode)

**Naming split (v2 refinement, 2026-05-22):**
- **Agent-facing tool name:** `isolated_workspace` (`enter_isolated_workspace()` / `exit_isolated_workspace()`) — surfaces the security property agents observe.
- **Internal mechanism name:** `pinned_workspace` (e.g. `PinnedWorkspaceManager`, cgroup prefix `eos-pinws-*`) — describes the daemon-side lifecycle. Kept to minimize churn.
- **RPC namespace:** `api.isolated_workspace.*` (renamed from `api.pinned_workspace.*` for agent-SDK consistency).
- **Env vars:** `EOS_ISOLATED_WORKSPACE_*` (renamed from `EOS_PINNED_WORKSPACE_*` and `EOS_PINNED_WS_*`).
- **Capability flag** in `api.runtime.ready`: `capabilities.isolated_workspace`.
- **Error kinds:** `isolated_workspace_already_open`, `no_isolated_workspace`.
- **Audit events / cgroup naming:** kept with `pinned` / `eos-pinws-*` prefix — daemon-internal observability, not part of any public contract.

Existing default mode (workspace-per-tool_call w/ OCC publish at end) is unchanged.

---

## v2 Refinement Changelog (2026-05-22)

The v1 network model (bridge + nftables allowlist + operator-declared shared services) was refined after design review concluded two load-bearing risks: (a) allowlist drift / silent default-allow on ruleset misconfig, and (b) operator pre-config burden that contradicts the agent-permissive driver. v2 picks **bridge + MASQUERADE, arbitrary outbound, two static deny rules**.

**Net changes from v1:**

| Area | v1 | v2 |
|---|---|---|
| Egress filter | nft allowlist of operator-declared shared services | None — arbitrary outbound (bridge + MASQUERADE) |
| Shared services | First-class via `EOS_PINNED_WS_SHARED_SERVICES` | Removed; agents reach external dbs / pypi via normal IPs |
| Inbound | Impossible by construction | Unchanged — still impossible |
| Static deny rules | 1 (default-deny chain) | 2 (IMDS drop + cross-veth drop) — static, never edited per-ws |
| IP pool | `10.244.16.0/20` carved into /29s, claimed 2048 ws (math wrong → actual 512) | Flat `10.244.0.0/24` allocated as /32 per ws; ceiling 253, ~4× headroom over `TOTAL_CAP=64` |
| DNS | Unspecified | Lowerdir `/etc/resolv.conf` → routable resolver; documented in §4 |
| Docker config | Required `cap_add: NET_ADMIN` + `network_mode: "service:..."` | Required `cap_add: NET_ADMIN` only |
| R4 (shared-netns attach) | Picked sibling-container `network_mode: service:...` | Removed |
| R13 viability probe | Includes per-veth nft allowlist install probe | Includes only static-rule install probe (MASQUERADE, IMDS drop, cross-veth drop) |
| R15 (default-deny test) | Required | Removed (no default-deny) |
| Tests removed | — | `test_shared_services_reachability`, `test_shared_services_misconfig_fails_loud`, `test_nft_default_deny_repair_or_refuse` |
| Tests added | — | `test_arbitrary_egress_works`, `test_imds_dropped`, `test_cross_agent_unreachable`, `test_dns_resolves_inside_ns`, `test_lowerdir_visible_inside_mntns`, `test_pip_install_then_run` |
| §5 cost summary | Implicit | Explicit table (disk O(1), RAM O(N×upperdir), kernel O(N)) |

**Threat-model shift in v2:** trusts agents at the "developer running pip install" level. IAM-credential exfil via IMDS is mitigated by a single static drop rule. Other egress (data exfil to attacker.com) is accepted as residual risk consistent with the agent-permissive driver. If higher-isolation deployments need an allowlist, it should be a separate, opt-in feature flag — not v2's default.

**What did NOT change from v1:**
- §1 state model (handle dataclass, locking, lease composition)
- §2 lifecycle skeleton (enter / op-routing / exit sequences) — except RPC namespace rename
- §3 LayerStack interaction (lowerdir snapshot-at-enter, A1)
- OCC unreachability (C1 + C2 + R1 + R2 + R3 — structural separation)
- Resource controls (TTL, quota, freezer R11, host-RAM gate R6, setup timeout N1)
- GC pass (R5 ordering)
- Helper-spawn discipline (R10)
- ns_holder two-step handshake (R12)

---

## RALPLAN-DR Summary

### Principles
1. **No upward writes from pinned mode.** Upperdir is born ephemeral and dies on `exit_workspace`. Enforcement is structural (different handle class, different exit function, no code path to `OccService.apply_changeset`), not policy.
2. **Default flow untouched.** `SandboxOverlay.acquire_operation_overlay` / `publish_cycle` / `release_operation_overlay` keep their existing semantics. Pinned mode is purely additive.
3. **Daemon owns ns FDs.** Agents never hold a kernel FD. Re-entry to an ns is daemon-side `setns(2)` against a stored FD; agents only see opaque `workspace_handle` strings.
4. **External agent ↔ in-sandbox server (FD ownership invariant).** The off-host agent (outside docker) never directly addresses an in-netns listener and never holds a kernel FD; `start_server` and `pytest` remain daemon-routed tool calls executed inside the pinned ns. No host-port DNAT. **(v2 clarification, C3):** This principle covers the agent-↔-daemon boundary, not the ws-↔-external-services boundary. In-ns processes spawned by tool calls (e.g., `pip`, `psql`) reach external IPs directly through the netns's default route + MASQUERADE; that's the v2 egress model, not a violation of this principle.
5. **Resource-lite by construction.** cgroup v2 freezer for idle freeze (not SIGSTOP — see §5); TTL eviction; quota=1 pinned workspace per agent default. A pinned workspace at rest must cost no CPU and bounded RAM (just kernel structs + listening sockets).

### Decision Drivers (top 4)
1. **Stateful test scaffolding correctness** — a server booted in call N must be reachable from `localhost:PORT` in call N+1, same PID, same listening socket.
2. **Isolation invariant survival under refactor** — six months from now, an "innocent" cleanup PR must not be able to silently reconnect pinned-mode upperdir to the OCC merge path.
3. **Operator cost at rest** — idle pinned workspaces (between calls, between agents) must not pin CPU or scale per-agent RAM beyond a fixed kernel cost.
4. **(v2 NEW) Agent egress works without operator pre-config.** Measurable acceptance: at least one CI-runnable integration test must demonstrate, in a single fresh isolated workspace, all of (a) DNS resolution of an injected hostname, (b) outbound TCP handshake to a daemon-host-side fixture (not internet), (c) MASQUERADE source-NAT'd source IP matches the daemon host's external IP, (d) zero operator-declared allowlist entries. Test name: `test_v2_driver_4_acceptance` in §7 integration tier. Driver fails if any of (a)–(d) fails or the test is skipped.

### Viable Options (load-bearing sub-decisions)

#### (a) Lowerdir source: snapshot at enter vs live tip

| Option | Pros | Cons |
|---|---|---|
| **A1. Snapshot lowerdir at `enter_workspace`** (acquire `WorkspaceLease` against current `Manifest`; reuse for whole session) | Test reproducibility across calls. Layerstack tip can advance freely; pinned ws unaffected. Reuses existing `LeaseRegistry.acquire` + `squash_barrier_layers` for GC protection (already proven correct). | Pinned ws cannot see a layer published after enter — surprising if the agent expects to "test the latest code." Mitigation: doc + optional `refresh()` tool that exits-and-re-enters. |
| A2. Live-tip lowerdir (overlay is re-mounted on each tool call against `read_active_manifest()`) | Always-current view of layerstack. | Test runs are non-reproducible: a peer agent advancing the tip between call N and N+1 *changes the lowerdir under a running server* — server's mmap'd files, opened FDs become stale. Catastrophic. Defeats the point. |

**Pick: A1.** Snapshot-at-enter wins on driver #1 (test correctness) and reuses `LeaseRegistry` mechanics one-for-one. Doc: "if you want fresh code, exit + re-enter."

#### (b) Re-entry behavior when `enter_workspace()` is called with an existing pinned ws (S1 — agent_id is session-resolved, not a payload arg)

| Option | Pros | Cons |
|---|---|---|
| **B1. Explicit error** (`pinned_workspace_already_open`, include `created_at` / `last_activity` diagnostics in error — S1: no `handle_id` exposed to agent) | Forces agent SDK to make a decision. Surfaces leaks (forgot to `exit_workspace`). | One extra round trip on a legitimate "I want a fresh ws" intent. |
| B2. Implicit exit + recreate | Convenient. | Silently discards any background server the agent thought was still running. Source of "where did my server go?" bugs. Conflicts with driver #1. |

**Pick: B1.** Surface the conflict; the SDK can wrap `exit + enter` in one call if it wants — but the daemon doesn't paper over it.

#### (c) OCC-unreachability mechanism

Three candidate enforcement layers; we **stack two** for defense in depth:

| Mechanism | Layer | What it catches |
|---|---|---|
| **C1. Distinct handle class.** `PinnedWorkspaceHandle` (new) is its own dataclass — not a subclass of `OperationOverlayHandle`. It exposes `release()` but no `publish_*` callable and is not a valid arg to `SandboxOverlay.publish_cycle` (which annotates `OperationOverlayHandle` / `CommandExecRequest`). | Type/structural | Any future code that tries to pass it to OCC fails type checking / runtime arg validation. |
| **C2. Distinct exit RPC.** `api.pinned_workspace.exit` calls `PinnedWorkspaceManager.exit(handle)` which calls **only** `_discard_upperdir(handle)` and `_release_lease(handle.lease_id)` — never `publish_cycle` / `apply_changeset`. Reviewable in one place. | Code path | "Cleanup PR" that "uses the existing release helper" goes through C2, not OCC. |
| C3. Runtime assertion in `OccService.apply_changeset` that the changeset's `workspace_ref` is not a pinned-ws ref. | Runtime | Belt-and-suspenders, but easy to bypass with a string. |

**Pick: C1 + C2.** C3 rejected as load-bearing — string assertions rot. C1 + C2 give type-level + code-path-level separation. The pinned manager's exit must be a literal `assert` that publish-shaped callables are not on the handle (i.e., the handle doesn't have an `upperdir_capture_target` field).

#### (d) Shared-services connectivity

| Option | Pros | Cons |
|---|---|---|
| **D1. Single daemon-owned bridge (`eos-shared0`).** veth pair per pinned ws, host end on bridge, container end in the new netns. Static /29 or /28 per ws. Default route nullopt. iptables/nftables egress whitelist: dst-IP whitelist for known shared services (db, fixtures); drop everything else (incl peer agents, host metadata, internet). | Linear cost: one bridge total. Reuses well-understood Docker-style networking. Whitelist is a single nftables ruleset the daemon installs once. | Bridge becomes a single failure domain; ruleset bugs leak across agents. Mitigated by: agents are mutually external (no peer-agent IPs on the bridge anyway), and the whitelist denies any non-allowlisted dst. |
| D2. Per-service overlay network. | Cleaner isolation per service. | Multiplies cost per pinned ws; orchestration burden. Overkill for "agent talks to one db." |
| D3. Daemon as L7 proxy (agent's netns has only `lo`; daemon forwards db connections). | Most isolating: agent traffic to db is daemon-mediated, fully observable. | Heavy: daemon becomes a TCP forwarder for arbitrary db protocols (postgres, redis…). Latency-sensitive workloads suffer. New surface area. |

**Pick: D1.** Matches resource-lite principle and existing Docker-style mental model. We get observability via netfilter logging on the bridge if we want it.

#### (e) Routing fork: where to discriminate pinned vs default — **R14**

| Option | Pros | Cons |
|---|---|---|
| e1. Per-handler arg branch (`if args.get("pinned_workspace_handle"): …`) inside `shell.py`, `read.py`, `write.py`, `edit.py`, `search.py` | Single op name per verb; agents pass an extra arg | **Edits 5 hot-path files**; violates Principle 2 ("default flow untouched"); any of those files can refactor into OCC reachability since they already import `SandboxOverlay` via `shell_runner` etc. |
| **e2. Dispatcher op-level fork** — separate ops `api.pinned_workspace.{shell,read_file,write_file,edit_file,search_content}` registered in `_load_peer_bootstraps`, routed to a new `pinned_workspace_ops` module with bounded imports | Default handlers untouched. Pinned module's import graph is bounded by construction. R3 import-graph test is meaningful (catches reintroduction by import). The structural OCC unreachability claim is verifiable at CI time. | One extra op name per verb (5 new entries in `OP_TABLE`). |
| e3. Entirely separate `pinned_workspace_handler` namespace via a new RPC dispatcher | Maximum isolation | Overkill: doubles dispatcher surface; gives no extra property over e2; harder to migrate later. |

**Pick: e2.** Preserves Principle 2 by import-graph construction; makes R3 a meaningful CI guardrail; the cost is 5 new op-table entries.

### Pre-mortem (3 scenarios, 6 months out)

**Scenario 1 — Subtle merge-back leak via shared util.**
*What happened:* Someone refactors `release_operation_overlay` and `pinned_workspace.exit` to share a "common cleanup" helper that "also flushes the upperdir for safety." `walk_upperdir` is called on the pinned ws upperdir; if it's non-empty, the helper "helpfully" forwards path changes to `OccService.apply_changeset`. Test artifacts (a `.pytest_cache/`, a generated `report.xml`) leak into the layerstack. Reproducibility of agent workspaces is broken; a follow-on agent sees test scaffolding it didn't ask for.
*What we missed:* C2 alone is not enough. **The load-bearing defense (R1):** mock `CommitQueue.apply` / `apply_sync` at the single OCC funnel and assert zero calls across a full enter→op→exit cycle — this catches both `OccService.apply_changeset` and `OccService.commit_prepared` (the latter used directly by `handler/edit.py`/`write.py` and missed by an apply_changeset-only mock). **Structural complement (R3):** an import-graph test that walks the pinned ops handler module's transitive imports and asserts neither `sandbox.occ.service` nor `sandbox.occ.commit_queue` nor `sandbox.daemon.service.sandbox_overlay` is reachable — catches reintroduction by import even when the offending code path isn't exercised by tests. The original `inspect.getsource` grep is demoted to a signal-only secondary test (misses dynamic `getattr` and re-imports). All three live in §7 unit tests.

**Scenario 2 — Resource exhaustion: leaked veths / bridge ports / netns FDs after daemon crash storm.**
*What happened:* Daemon OOMs and restarts 10× in 30s. Each crash leaves: (a) the host-side veth still attached to `eos-shared0`, (b) the netns file in `/var/run/netns/` if we used iproute2-style naming, (c) the frozen-cgroup tree reparented to init — the pidns root keeps existing (frozen or not, freeze state survives daemon death) until the daemon's GC reaps it, (d) the `eos-pinws-{handle_id}/` cgroup dir itself. After the storm: hundreds of stale veths, bridge port table exhaustion, dangling cgroups consuming the cgroup namespace.
*What we missed:* Need a deterministic naming convention (`eos-pinned-{agent_id_short}-{handle_short}`) and a daemon-startup GC pass that:
  1. Lists `ip link` for any iface matching the pinned prefix.
  2. Lists `ip netns` for any ns matching the pinned prefix.
  3. Reaps anything whose corresponding state row is absent.
  4. Reaps the pinned-mode upperdir scratch tree by mtime + naming convention.
Also: cap the number of pinned-ws creates per unit time (rate-limit), so crash-loops can't fill the bridge port table faster than GC catches up. Add to §5.

**Scenario 3 — Scope creep: "Can we make pinned-mode writes 'kinda persist' for caching?" (v2 revised)**
*What will happen:* A product team will request "but our pytest takes 5 min to install pip deps every time — can we cache them across enter/exit?" This is a perfectly reasonable request that, if granted by softening the "no merge-back" rule, immediately destroys principle #1 and the structural separation. **We say no to merge-back, but we say yes structurally** via a new orthogonal mechanism:
  - The right answer is a **named shared mount** (read-only or copy-on-write) mounted into the pinned ws at enter-time, fed by an out-of-band cache populated by an explicit "cache push" tool — *not* by exit-merge-back of arbitrary upperdir.
  - Documented as part of the public contract from day one: "If you need state to survive `exit_workspace`, write it through an explicit caching primitive, never to the workspace root."

*Anticipated v2 follow-on pressure:* "v1 had an allowlist; can we re-enable it semi-permanently to harden against compromised pip deps?" — the answer is the opt-in allowlist feature flag (ADR follow-up), NOT making v2's bridge+MASQUERADE conditional, AND NOT folding the allowlist back into the default path. The structural separation is "permissive default" vs "opt-in restrictive" — never a sliding scale.

**Scenario 4 (v2 NEW) — Compromised PyPI dep exfiltrates secrets via DNS to attacker.com.**
*What happened:* Agent runs `pip install legitimate-looking-package`. The package's `setup.py` walks `/proc/self/environ`, encodes the env-var blob, splits across DNS labels, and queries `attacker.com` via lots of TXT lookups. The bridge MASQUERADE happily routes the DNS traffic out. Six months later, audit shows no record of the exfil because v2's audit events (`sandbox_pinned_workspace_tool_call`) didn't record outbound destinations — only the verb and exit code.
*What we missed:* Outbound forensics is not part of v2's threat-model acceptance. The "developer with laptop" framing assumed worst case = data loss, not lateral movement. The right defense is **flow-logging on the bridge** (one nft chain that LOG-counts outbound destinations, post-MASQUERADE): not a filter, just a counter. Add to follow-ups; if `EOS_ISOLATED_WORKSPACE_AUDIT_EGRESS=true` is set, the daemon installs a `nft add rule inet eos_pinws_filter forward log prefix "iws_egress " counter` on the forward chain and the recorder lifts entries into `sandbox_pinned_workspace_egress_observed` audit events. Not v2 default; documented as the response-to-incident lever.
*Decision:* accept residual risk in v2 default. Defend via opt-in egress audit (above) as a future-but-already-specified hook, not a "we'll figure it out later."

**Scenario 5 (v2 NEW) — Daemon-host network position becomes the pivot surface.**
*What happened:* Daemon runs in a production VPC. Its host has routes to RDS `10.0.0.42:5432`, an internal admin API at `internal-admin.svc.cluster.local`, and an instance-profile IAM role with `s3:ListBucket` on a customer bucket. A compromised dep `import requests; requests.get("http://internal-admin.svc.cluster.local/")` succeeds — the daemon's resolver answers, MASQUERADE routes, and the agent now has read access to internal services it should never have seen. IMDS drop did nothing because the attack uses the daemon's *resolver-discoverable* internal hostnames, not metadata.
*What we missed:* The threat surface is the daemon-host's *network position*, not "the internet." This is a deployment-posture concern, not a code-path concern, but the plan must surface it as an operator decision before enabling `EOS_ISOLATED_WORKSPACE_ENABLED=true`:
  1. Documented warning in §6 threat-model row (added in this iteration) AND a `WARNING` log line at `initialize()` time enumerating reachable RFC1918 subnets the daemon can see.
  2. Operator opt-in: `EOS_ISOLATED_WORKSPACE_RFC1918_EGRESS=allow|deny|<cidr-list>` (default `allow` for v2 — agent-permissive). Setting `deny` installs a static `nft drop rule for RFC1918 dst (except the bridge's own 10.244.0.0/24)`, blocking the pivot vector while leaving public-internet egress (pypi, GitHub) intact. This is opt-in hardening, NOT default; deny-by-default re-introduces operator pre-config burden which contradicts driver #4.
  3. Test (added to §7): `test_rfc1918_egress_drop` — when `EOS_ISOLATED_WORKSPACE_RFC1918_EGRESS=deny` is set at daemon boot, ws's `curl 10.0.0.42` drops; `curl 1.1.1.1` succeeds.
*Decision:* v2 default is permissive (matches driver #4). The opt-in deny knob is specified and tested so operators in higher-isolation deployments have a one-flag path. Mitigates the architect's steelman without retreating from driver #4.

---

## Workflow Diagrams

Visual overview of the v2 design. Four diagrams: (A) enter lifecycle, (B) per-tool-call routing, (C) network topology, (D) per-workspace kernel-object stack.

### Diagram A — `enter_isolated_workspace` lifecycle

End-to-end sequence from agent RPC to handle registration. The dashed arrows are pipe-based handshakes; the solid arrows are control flow.

```
  Agent SDK            Daemon dispatcher       PinnedWorkspaceMgr      ns_holder
                       (api.isolated_                                  (becomes PID 1
                        workspace.enter)                                of new pidns)
      │                       │                       │                     │
      │ enter_isolated_       │                       │                     │
      │  workspace()          │                       │                     │
      ├──────────────────────▶│                       │                     │
      │                       │ resolve session       │                     │
      │                       │  → agent_id           │                     │
      │                       │                       │                     │
      │                       ├──── manager.enter ───▶│                     │
      │                       │                       │                     │
      │                       │                       │ ① quota check       │
      │                       │                       │   _by_agent[aid]?   │
      │                       │                       │   → 409 if open     │
      │                       │                       │                     │
      │                       │                       │ ② host-RAM gate     │
      │                       │                       │   (R6)              │
      │                       │                       │                     │
      │                       │                       │ ③ prepare_workspace │
      │                       │                       │   _snapshot         │
      │                       │                       │   (lease lowerdir)  │
      │                       │                       │                     │
      │                       │                       │ ④ posix_spawn       │
      │                       │                       │   ns_holder with    │
      │                       │                       │   unshare -Unpm     │
      │                       │                       ├────────────────────▶│
      │                       │                       │                     │ enter ns
      │                       │                       │                     │ (user, net,
      │                       │                       │      "ns-up\n"      │  pid, mnt)
      │                       │                       │◀ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┤
      │                       │                       │                     │
      │                       │                       │ ⑤ open ns FDs:      │
      │                       │                       │   /proc/{root_pid}/ │ blocks on
      │                       │                       │   ns/{net,pid,      │ control
      │                       │                       │   mnt,user}         │ pipe
      │                       │                       │                     │
      │                       │                       │ ⑥ overlay mount     │
      │                       │                       │   (helper: setns →  │
      │                       │                       │    fsopen+fsconfig+ │
      │                       │                       │    fsmount → target │
      │                       │                       │    = /testbed in    │
      │                       │                       │    new mntns)       │
      │                       │                       │                     │
      │                       │                       │ ⑦ wire veth + IP    │
      │                       │                       │   + bridge port-    │
      │                       │                       │   isolation +       │
      │                       │                       │   default route +   │
      │                       │                       │   IPv6 purge        │
      │                       │                       │                     │
      │                       │                       │ ⑧ DNS detect        │
      │                       │                       │   (inside new       │
      │                       │                       │   mntns); fallback  │
      │                       │                       │   bind-mount if     │
      │                       │                       │   127.0.0.0/8       │
      │                       │                       │                     │
      │                       │                       │ ⑨ cgroup create +   │
      │                       │                       │   move holder PID   │
      │                       │                       │                     │
      │                       │                       │     "net-ready\n"   │
      │                       │                       ├─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ▶│
      │                       │                       │                     │ ip link
      │                       │                       │                     │ set lo up
      │                       │                       │      "ready\n"      │
      │                       │                       │◀ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┤
      │                       │                       │                     │ pause()
      │                       │                       │                     │ waiting on
      │                       │                       │ ⑩ register handle:  │ SIGTERM
      │                       │                       │   _handles[hid]     │
      │                       │                       │   _by_agent[aid]    │
      │                       │                       │   status=active     │
      │                       │                       │                     │
      │                       │                       │ ⑪ persist subset to │
      │                       │                       │   manager.json      │
      │                       │                       │   (atomic write)    │
      │                       │                       │                     │
      │                       │                       │ ⑫ emit audit event  │
      │                       │                       │   sandbox_pinned_   │
      │                       │                       │   workspace_enter   │
      │                       │                       │                     │
      │                       │◀──── {success, ... } ─┤                     │
      │                       │                       │                     │
      │◀── {success,          │                       │                     │
      │     manifest_v,       │                       │                     │
      │     manifest_root_    │                       │                     │
      │     hash}             │                       │                     │
      │                       │                       │                     │

      Setup-timeout (N1) wraps steps ④–⑨: if no "ready" within
      EOS_ISOLATED_WORKSPACE_SETUP_TIMEOUT_S (default 30 s), SIGKILL holder,
      run partial rollback gated by per-step booleans, return
      isolated_workspace_failed: setup_timeout with failed_step detail.
```

### Diagram B — Per-tool-call routing (while pinned)

Every tool call after enter goes through this loop. Idle freeze/thaw is what keeps "resource-lite at rest" honest.

```
  Agent SDK         Dispatcher          PinnedWorkspaceMgr     setns_exec helper
                    (op-fork R2)        (run_in_handle)        (single-threaded
                                                                until fork+exec)
      │                  │                       │                       │
      │ api.isolated_    │                       │                       │
      │  workspace.shell │                       │                       │
      ├─────────────────▶│                       │                       │
      │                  │ op-table:             │                       │
      │                  │ pinned_workspace_ops  │                       │
      │                  │ .shell                │                       │
      │                  │                       │                       │
      │                  │ (handler module has   │                       │
      │                  │  ZERO imports of      │                       │
      │                  │  sandbox.occ.*  R3)   │                       │
      │                  │                       │                       │
      │                  │ resolve handle by     │                       │
      │                  │  session.agent_id     │                       │
      │                  │  (S1; no payload      │                       │
      │                  │  handle_id)           │                       │
      │                  │                       │                       │
      │                  ├──── run_in_handle ───▶│                       │
      │                  │                       │                       │
      │                  │                       │ acquire handle.lock   │
      │                  │                       │  (per-handle, not     │
      │                  │                       │  global)              │
      │                  │                       │                       │
      │                  │                       │ THAW: echo 0 >        │
      │                  │                       │  cgroup.freeze        │
      │                  │                       │ wait cgroup.events:   │
      │                  │                       │  frozen 0  (≤2 s;     │
      │                  │                       │  SIGCONT fallback     │
      │                  │                       │  if freezer_degraded) │
      │                  │                       │                       │
      │                  │                       │ posix_spawn helper   ▶│
      │                  │                       │ with ns FDs +         │ setns(userns)
      │                  │                       │ payload via stdin     │ setns(mntns)
      │                  │                       │                       │ setns(pidns)
      │                  │                       │                       │ setns(netns)
      │                  │                       │                       │
      │                  │                       │                       │ fork() → child
      │                  │                       │                       │  is in new
      │                  │                       │                       │  pidns
      │                  │                       │                       │
      │                  │                       │                       │ exec /bin/sh
      │                  │                       │                       │  -c "<cmd>"
      │                  │                       │                       │   (or in_ns_
      │                  │                       │                       │    write for
      │                  │                       │                       │    write ops)
      │                  │                       │                       │
      │                  │                       │                       │ Server boots
      │                  │                       │                       │ here → PID
      │                  │                       │                       │ survives next
      │                  │                       │                       │ tool call
      │                  │                       │                       │ because pidns
      │                  │                       │                       │ persists
      │                  │                       │                       │
      │                  │                       │      stdout/stderr/   │
      │                  │                       │      exit_code        │
      │                  │                       │◀──────────────────────┤
      │                  │                       │                       │
      │                  │                       │ FREEZE if no other    │
      │                  │                       │  in-flight: echo 1 >  │
      │                  │                       │  cgroup.freeze        │
      │                  │                       │                       │
      │                  │                       │ update handle.        │
      │                  │                       │  last_activity        │
      │                  │                       │                       │
      │                  │                       │ emit audit event      │
      │                  │                       │  sandbox_pinned_      │
      │                  │                       │  workspace_tool_call  │
      │                  │                       │                       │
      │                  │◀──── result ──────────┤                       │
      │                  │                       │                       │
      │◀── result        │                       │                       │
      │                  │                       │                       │

      OCC unreachability (R1+R3): handler module imports do NOT include
      sandbox.occ.* or SandboxOverlay. CommitQueue.apply / apply_sync
      call count == 0 across full enter→op→exit cycle (mock-verified).
```

### Diagram C — Network topology (host's view)

What's reachable from where. The bridge lives in the daemon's netns; per-ws veths land on it with port isolation enabled. Static rules: MASQUERADE on egress, DROP for IMDS, optional RFC1918 deny.

```
   ┌──────────────────────────────────────────────────────────────────────┐
   │  Daemon host (daemon's network namespace)                            │
   │                                                                      │
   │       ┌────────────────┐                                             │
   │       │  external NIC  │ ◀── host's default route → internet / VPC   │
   │       │   (eth0 etc.)  │                                             │
   │       └───────┬────────┘                                             │
   │               │                                                      │
   │     nat:POSTROUTING                                                  │
   │       MASQUERADE  saddr 10.244.0.0/24                                │
   │                   oifname != eos-shared0   ◀── one static rule       │
   │                                                                      │
   │     filter:FORWARD   (all static, installed once at daemon boot)     │
   │       DROP   daddr 169.254.169.254               ◀── IMDS            │
   │       DROP   daddr {RFC1918} except 10.244/24    ◀── opt-in deny     │
   │       LOG counter prefix "iws_egress "           ◀── opt-in audit    │
   │                                                                      │
   │     ┌─────────────────────────────────────────────────────────────┐  │
   │     │  bridge   eos-shared0       gw 10.244.0.1/24                │  │
   │     └──┬─────────────────┬─────────────────┬───────────────────┬──┘  │
   │        │                 │                 │                   │     │
   │   veth-host-A       veth-host-B       veth-host-C        veth-host-D │
   │   isolated on       isolated on       isolated on        isolated on │
   │   mcast_flood off   mcast_flood off   mcast_flood off    mcast ...   │
   │        │                 │                 │                   │     │
   └────────┼─────────────────┼─────────────────┼───────────────────┼─────┘
            │                 │                 │                   │
   ┌────────┴───────┐  ┌──────┴───────┐  ┌──────┴──────┐  ┌─────────┴─────┐
   │ ws-A           │  │ ws-B         │  │ ws-C        │  │ ws-D          │
   │ (netns A)      │  │ (netns B)    │  │ (netns C)   │  │ (netns D)     │
   │                │  │              │  │             │  │               │
   │ eth0           │  │ eth0         │  │ eth0        │  │ eth0          │
   │  10.244.0.2/24 │  │ .0.3/24      │  │ .0.4/24     │  │ .0.5/24       │
   │ lo  127.0.0.1  │  │ lo           │  │ lo          │  │ lo            │
   │                │  │              │  │             │  │               │
   │ default via    │  │ default via  │  │ default via │  │ default via   │
   │  10.244.0.1    │  │  10.244.0.1  │  │ 10.244.0.1  │  │  10.244.0.1   │
   │                │  │              │  │             │  │               │
   │ no v6 default  │  │ no v6 default│  │ no v6       │  │ no v6 default │
   │ accept_ra=0    │  │ accept_ra=0  │  │ default     │  │ accept_ra=0   │
   │                │  │              │  │             │  │               │
   │ Agent A's      │  │ Agent B's    │  │ Agent C's   │  │ Agent D's     │
   │ procs + own    │  │ procs        │  │ procs       │  │ procs         │
   │ /testbed       │  │              │  │             │  │               │
   │ overlay        │  │              │  │             │  │               │
   └────────────────┘  └──────────────┘  └─────────────┘  └───────────────┘

   Reachability check:
     A → A's localhost:8080 across tool calls  ✅  netns lo + pidns survives
     A → 10.244.0.1 (gw)                       ✅  bridge route
     A → internet / external db                ✅  default route → MASQ
     A → B (10.244.0.3)                        ❌  bridge port isolation
     A → 169.254.169.254 (IMDS)                ❌  static FORWARD DROP
     A → 10.0.0.42 (VPC peer)                  ✅ by default; ❌ if
                                                  RFC1918_EGRESS=deny
     external → A.0.2 (inbound)                ❌  no route from outside
                                                   daemon host
     daemon process → A (via setns)            ✅  daemon owns ns FDs
```

### Diagram D — Per-workspace kernel-object stack

Five namespaces + one cgroup + one overlay + one veth-pair, all owned by the daemon, all torn down atomically on exit (SIGKILL holder → kernel reaps pidns and everything in it).

```
   ┌──────────────────────────────────────────────────────────────────────┐
   │  Per-workspace kernel objects (one stack per ws)                     │
   │                                                                      │
   │   userns ◀── unshare(CLONE_NEWUSER)                                  │
   │     │       (single-threaded process required at setns time — R10)   │
   │     │                                                                │
   │     ├── pidns ◀── unshare(CLONE_NEWPID)                              │
   │     │     │                                                          │
   │     │     ├── PID 1 = ns_holder  (pause()ing on SIGTERM)             │
   │     │     ├── PID 2..N = agent-spawned procs                         │
   │     │     │              ┌─────────────────────────────────┐         │
   │     │     │              │ A server boots here in call N.  │         │
   │     │     │              │ Still here in call N+1 because  │         │
   │     │     │              │ pidns + listening socket in     │         │
   │     │     │              │ netns both persist for the ws   │         │
   │     │     │              │ lifetime. This is driver #1.    │         │
   │     │     │              └─────────────────────────────────┘         │
   │     │     │                                                          │
   │     ├── netns ◀── unshare(CLONE_NEWNET)                              │
   │     │     │                                                          │
   │     │     ├── lo (127.0.0.1)                                         │
   │     │     └── eth0 (10.244.0.X/24)  ──── veth pair ────▶ veth-host   │
   │     │                                                  on bridge     │
   │     │                                                  (Diagram C)   │
   │     │                                                                │
   │     ├── mntns ◀── unshare(CLONE_NEWNS, --propagation private)        │
   │     │     │                                                          │
   │     │     ├── inherited daemon fs (/usr, /bin, /lib, /proc, ...)     │
   │     │     │   → agents get pytest / python / curl / pip for free     │
   │     │     │                                                          │
   │     │     ├── /testbed = OVERLAY                                     │
   │     │     │      lowerdir = lease-pinned layer dirs                  │
   │     │     │                 (snapshot at enter; A1)                  │
   │     │     │      upperdir = tmpfs size=1G  (ephemeral, ENOSPC =      │
   │     │     │                                  backpressure)           │
   │     │     │      workdir  = tmpfs                                    │
   │     │     │                                                          │
   │     │     └── /etc/resolv.conf  (lowerdir, OR fallback bind-mount    │
   │     │           if 127.0.0.0/8 detected INSIDE this mntns)           │
   │     │                                                                │
   │     └── (no IPC ns — sharing host IPC is acceptable)                 │
   │                                                                      │
   │   cgroup v2  /sys/fs/cgroup/eos-pinws-{handle_id}/                   │
   │     ├── cgroup.freeze    (1 = idle, 0 = active)                      │
   │     ├── cgroup.procs     = holder + spawned children                 │
   │     ├── memory.current   (per-ws accounting, free)                   │
   │     └── cpu.stat         (per-ws accounting, free)                   │
   │                                                                      │
   │   Daemon-side handle (in-memory + persisted subset):                 │
   │   ┌──────────────────────────────────────────────────────────────┐   │
   │   │ PinnedWorkspaceHandle {                                      │   │
   │   │   handle_id, agent_id, lease_id,                             │   │
   │   │   manifest_root_hash, manifest_version,                      │   │
   │   │   netns_fd, pidns_fd, mntns_fd, userns_fd,  ← FDs not        │   │
   │   │   ns_ip,                                       persisted     │   │
   │   │   veth_host_name, root_pid (holder),        ← rest persisted │   │
   │   │   scratch_dir, status, freezer_degraded,      to manager.    │   │
   │   │   created_at, last_activity                   json           │   │
   │   │ }                                                            │   │
   │   └──────────────────────────────────────────────────────────────┘   │
   │                                                                      │
   │   Teardown (on exit_isolated_workspace OR daemon-restart GC):        │
   │     SIGTERM → grace 5s → SIGKILL holder (PID 1 of pidns)             │
   │       └─▶ kernel reaps entire pidns tree atomically                  │
   │       └─▶ mntns + overlay + tmpfs upperdir vanish with last ref      │
   │       └─▶ netns reaped when last FD closed (daemon closes its FDs)   │
   │       └─▶ veth-host removed from bridge; IP returned to pool         │
   │       └─▶ cgroup rmdir; lease released; scratch dir rmtree           │
   │                                                                      │
   │   Upperdir contents NEVER reach OCC. Structural separation           │
   │   (distinct handle type + distinct exit RPC + import-bounded         │
   │    handler module + behavioral mock test) makes this true            │
   │    by construction, not by policy.                                   │
   └──────────────────────────────────────────────────────────────────────┘
```

### How the four diagrams compose

- **Diagram A** is the one-time setup that produces the kernel stack of **Diagram D**.
- **Diagram B** is what happens N times between an A and the eventual exit — re-entering the namespaces shown in D.
- **Diagram C** is the cross-workspace view: what D's `eth0` connects to, and how N copies of D coexist without interfering.

---

## 1. State model

Owner: a new `PinnedWorkspaceManager` (working name) lives in `backend/src/sandbox/daemon/service/pinned_workspace.py` (new). It is constructed once per daemon, alongside the existing `SandboxOverlay` facade in `sandbox/daemon/__main__.py` startup wiring.

Per-pinned-workspace state is a dataclass `PinnedWorkspaceHandle` (new, in same module):

| Field | Type | Source |
|---|---|---|
| `handle_id` | `str` (uuid hex 16) | `uuid4().hex[:16]` at enter |
| `agent_id` | `str` | RPC arg, validated non-empty |
| `lease_id` | `str` | from `OverlayLayerStackClient.prepare_workspace_snapshot(request_id=...)` (existing) |
| `manifest_root_hash` | `str` | from snapshot |
| `manifest_version` | `int` | from snapshot |
| `lowerdir_layer_paths` | `tuple[Path, ...]` | from snapshot |
| `workspace_root` | `Path` | The overlay's actual mount target *inside the new mntns*. Same convention as existing `SandboxOverlay.acquire_operation_overlay` (default `/testbed`). Matches the `mount_overlay(workspace_root=...)` API at `backend/src/sandbox/execution/overlay/kernel_mount.py:51,110` — the overlay mounts directly at this path, NOT at a scratch path. The pinned ws's mntns means this mount is visible only to tool calls inside the pinned ws; the daemon's mntns and other agents still see the unaltered `/testbed` in their own contexts. |
| `scratch_dir` | `Path` | `{scratch_root}/runtime/pinned-workspace/{handle_id}/` — daemon-side bookkeeping directory holding `upper/` and `work/`. **Not** the overlay's mount target; the only thing here that the agent's tool calls see is whatever the kernel projects via the overlay onto `workspace_root`. `rmtree`'d on exit after the mntns dies. |
| `upperdir` | `Path` | `{scratch_dir}/upper` (tmpfs, see §5) — passed as `mount_overlay(upperdir=...)`. |
| `workdir` | `Path` | `{scratch_dir}/work` — passed as `mount_overlay(workdir=...)`. |
| `netns_fd` | `int` (raw FD, `O_CLOEXEC`) | `open(/proc/{root_pid}/ns/net, O_RDONLY)` |
| `pidns_fd` | `int` (raw FD) | `open(/proc/{root_pid}/ns/pid, O_RDONLY)` |
| `mntns_fd` | `int` (raw FD) | `open(/proc/{root_pid}/ns/mnt, O_RDONLY)` |
| `userns_fd` | `int` (raw FD) | `open(/proc/{root_pid}/ns/user, O_RDONLY)` (kept if we use a userns; otherwise omit) |
| `root_pid` | `int` | the long-lived PID 1 of the pidns (a daemon-spawned `sleep infinity`-style "ns holder") |
| `veth_host_name` | `str` | `eos-pinws-{handle_id[:8]}h` |
| `veth_ns_name` | `str` | `eos-pinws-{handle_id[:8]}n` (peer end, lives in netns; renamed to `eth0` inside) |
| `ns_ip` | `IPv4Address` | allocated from a daemon-owned pool, /29 |
| `created_at` | `float` | monotonic now |
| `last_activity` | `float` | updated on every tool call setns |
| `status` | `Literal["active","stopped","exiting","reaping"]` | state machine |
| `quota_slot` | `int` | per-agent slot index |

Storage and locking:
- `PinnedWorkspaceManager._handles: dict[str, PinnedWorkspaceHandle]` keyed by `handle_id`, plus `_by_agent: dict[str, str]` (agent_id → handle_id) for the quota=1 lookup.

**Persisted state — `manager.json` schema (R9 + N4 + N5):**
On each successful `enter_workspace` / `exit_workspace`, the manager atomically writes `{scratch_root}/runtime/pinned-workspace/manager.json` with this top-level shape:
```
{
  "schema_version": 1,            // N5 — bumped on any breaking change
  "handles": [ <per-handle record>, ... ]
}
```
Per-handle record contains ONLY these fields:
- `handle_id` (str)
- `agent_id` (str)
- `veth_host_name` (str — GC key for ip-link cleanup)
- `ns_ip` (str — dotted-quad, for IP-pool reconciliation)
- `cgroup_path` (str — absolute path under `/sys/fs/cgroup/`)
- `scratch_dir_path` (str — for rmtree)
- `lease_id` (str — for `LeaseRegistry.release(lease_id)`)
- `manifest_root_hash` (str), `manifest_version` (int)
- `created_at` (float, unix epoch)
- `root_pid` (int — best-effort; may be reparented to init after daemon death, but useful as a hint for orphan reaping)
- `freezer_degraded` (bool — **N4**: persisted so the GC pass knows whether `cgroup.freeze=0` alone will thaw the tree, or whether the freezer-stall fallback (R11) used per-pid `SIGSTOP` and the GC pass must use `SIGCONT` instead)

**Schema-version policy (N5).** On daemon restart, if `schema_version` ≠ daemon's expected version (currently `1`), log WARN `manager_json_schema_mismatch expected=1 found=<N>` and treat `manager.json` as empty — GC falls back to naming-convention reap only. This keeps daemon upgrades safe across in-flight pinned workspaces; the cost is that an upgrade right after a daemon crash mid-pinned-ws may leave one or two orphans for naming-convention GC to catch (which it does).

**Explicitly NOT persisted:** the raw FD ints (`netns_fd`, `pidns_fd`, `mntns_fd`, `userns_fd`). FDs are process-local and meaningless across a daemon restart. On daemon restart the GC pass does **not** reopen FDs from manager.json — it reaps orphan resources by naming convention (`eos-pinws-{handle_id}-*` for cgroup / veth / netns / scratch dir) and releases leases by `lease_id`. The handle is considered lost; agents must call `enter_workspace` again to recreate.
- **Two-level locking** to preserve parallelism across agents:
  - `_map_lock: asyncio.Lock` — serializes only register / deregister / quota-check (enter, exit, TTL sweep). Held briefly.
  - `handle.lock: asyncio.Lock` (per-handle, stored on the dataclass) — serializes `run_in_handle` calls for *that one handle* and the exit sequence. Two different agents' pinned workspaces never contend.
- Exit acquires `_map_lock` to transition `active → exiting` and remove from maps, then acquires `handle.lock` (exclusive — no other tool calls can be in flight; the state flag stops new ones from being accepted), then runs the §2 exit sequence. This avoids a global serialization point for tool-call execution.

Composition with existing tracking:
- `WorkspaceLease` from `sandbox/layer_stack/lease.py` is the **only** GC anchor used for the lowerdir. `LeaseRegistry.acquire` is already called by `prepare_workspace_snapshot`; we keep that lease alive for the pinned ws lifetime via `handle.lease_id`. Squash-barrier semantics (`LeaseRegistry.squash_barrier_layers`) already protect layers from squash-collapse for active leases — pinned ws gets this for free.
- The handle is *not* added to `LeaseRegistry`'s state directly — `prepare_workspace_snapshot` handles that.

Audit composition with `recorder.py`: events are emitted on `SandboxOverlay.event_bus` (extended) and / or a new `PinnedWorkspaceEventBus`, then mirrored via the existing `AuditEventBus.subscribe(self._record_sandbox_event)` listener in `AuditRecorder._record_sandbox_event`. New `EventType` enum values (R8 — class is named `EventType`, not `AuditEventType`, at `backend/src/task_center_runner/audit/events.py:17`): `sandbox_pinned_workspace_enter`, `sandbox_pinned_workspace_exit`, `sandbox_pinned_workspace_tool_call`, `sandbox_pinned_workspace_evicted`, `sandbox_pinned_workspace_gc_orphan`. These get written into `sandbox_events.jsonl` automatically — *no recorder code changes*, only enum additions.

## 2. Lifecycle

### `enter_workspace() → {success, manifest_version, manifest_root_hash}` (S1)

Daemon RPC `api.isolated_workspace.enter` (renamed in v2). Handler in `sandbox/daemon/handler/pinned_workspace.py` (new). **No request payload required (S1):** agent identity is resolved from the daemon's TCP session state established at handshake. No allowlist arg — v2 has no shared-services concept; egress is controlled by the static bridge+MASQUERADE+IMDS-drop rules installed at daemon boot (see §4). The agent never sees `handle_id` — quota=1 per agent means the daemon resolves the active handle from session identity on every subsequent op.

Sequence:
1. **Quota check.** If `_by_agent.get(session.agent_id)` is set, return `isolated_workspace_already_open` error (B1). Agent identity comes from the session, not the request body (S1).
2. **Snapshot lowerdir.** Call `layer_stack.prepare_workspace_snapshot(request_id=f"pinned-{handle_id}", materialize=False)`. Capture `lease_id`, `manifest`, `layer_paths`.
3. **Spawn ns holder (R12 — two-step handshake).** Fork a long-lived `ns_holder` subprocess via `unshare -Unpm --propagation private` (user, net, pid, mount), exec a tiny Python sentinel (`sandbox.daemon.scripts.ns_holder`) that does a two-step handshake on a pipe pair to the daemon, then `pause()`s waiting for SIGTERM. This child becomes PID 1 of the new pidns, owns the netns, mntns, userns.
   - Use `os.posix_spawn` (preferred) or `subprocess.Popen` with `start_new_session=True`.
   - **Step 3a — `ns-up`:** holder writes `ns-up\n` to the readiness pipe as soon as it has entered the new namespaces. Its `/proc/{pid}/ns/{net,pid,mnt,user}` symlinks are stable and openable from this point.
   - The daemon then opens the ns FDs, runs step 5 (mount overlay), step 6 (wire veth + apply port-isolation + default route), step 7 (DNS detection + optional fallback bind-mount + IPv6 default-route purge) — all synchronously — and once the network setup completes successfully writes `net-ready\n` back to the holder on a control pipe.
   - **Step 3b — `ready`:** holder reads `net-ready`, sets `lo` up, then writes `ready\n` back on the readiness pipe and calls `pause()`.
   - The daemon only registers the handle as `active` after seeing `ready`. If the holder dies before `ready` (e.g., network setup failed and daemon SIGTERMed it), the manager treats this as a failed enter, runs the partial-rollback subset of `exit_isolated_workspace` (steps 4–8 of §2 exit), and returns the error.
   - **Setup timeout (N1).** The entire step-3-through-`ready` sequence is wrapped in a hard `EOS_ISOLATED_WORKSPACE_SETUP_TIMEOUT_S` deadline (default 30 s) on every blocking read of the readiness pipe. On timeout: SIGKILL the holder PID; run a partial rollback that touches only the steps that completed before the deadline — gated by per-step booleans tracked in the manager (`cgroup_created`, `tmpfs_mounted`, `overlay_mounted`, `ip_allocated`, `veth_installed`, `dns_configured`); call the exit-sequence steps 3–7 selectively in reverse order; return `isolated_workspace_failed: setup_timeout` with `failed_step ∈ {ns_holder_ready, overlay_mount, veth_install, dns_configure, net_ready_handshake}` in the error details. The timeout exists because `fsmount`/`mount(2)` calls can wedge a thread in `D` state under kernel/storage pressure; we cannot rely on holder-death signaling alone.
4. **Open ns FDs.** From the daemon (PID lives in the daemon's mntns/netns), `os.open("/proc/{root_pid}/ns/net", O_RDONLY | O_CLOEXEC)` for each. Store in the handle.
5. **Mount overlay (R7 — fix reuse claim).** Construct an overlay mount whose lowerdir = `layer_paths`, upperdir = `{scratch_dir}/upper` (tmpfs), workdir = `{scratch_dir}/work`, and **target = `workspace_root` (e.g., `/testbed`) inside the new mntns** — this is the canonical mount-overlay convention (see `backend/src/sandbox/execution/overlay/kernel_mount.py:51,110`). The overlay mounts AT the workspace path, replacing the daemon-image's `/testbed` with the merged view for *this mntns only*. The rest of the filesystem (`/usr/bin`, `/lib`, `/proc`, etc.) remains the daemon container's image — agents get `pytest`, `python`, `curl`, etc. for free, no chroot or pivot_root needed. Spawn a single-purpose helper that: (a) `setns(mntns_fd, CLONE_NEWNS)`, (b) opens the layer dir FDs **from inside the target mntns** (not from the daemon's mntns; `pass_fds` is NOT how namespaces are crossed — `setns` is), (c) constructs the overlay via `sandbox.execution.overlay.kernel_mount.mount_overlay`, which uses the `fsopen`/`fsconfig`/`fsmount` new mount API via `libc.syscall(SYS_fsopen)` (see `kernel_mount.py:29-73`). The `pass_fds` parameter in that function is vestigial — present in the signature but the implementation doesn't depend on it for namespace traversal.
   - Tmpfs upperdir: pre-mount `tmpfs` of `size=1G` at the upperdir path (per-ws disk quota — §5).
6. **Wire veth (v2 — full outbound).** From the daemon's netns (host side):
   - `ip link add {veth_host_name} type veth peer name {veth_ns_name}`
   - `ip link set {veth_ns_name} netns {root_pid}` — moves peer into pinned netns.
   - `ip link set {veth_host_name} master eos-shared0`.
   - `ip link set {veth_host_name} type bridge_slave isolated on mcast_flood off` — cross-agent isolation (§4).
   - `ip link set {veth_host_name} up`.
   - Inside the pinned netns (via `setns(netns_fd)` in a small helper): rename peer to `eth0`; **`ip addr add {ns_ip}/24 dev eth0`** (v2: /24 to cover the bridge subnet, NOT /29); **`ip route add default via 10.244.0.1`** (v2: default route exists, points to bridge gateway); `ip -6 route del default || true` (v2: purge any inherited IPv6 default route); `sysctl -w net.ipv6.conf.eth0.accept_ra=0` (block IPv6 RAs); `ip link set lo up`; `ip link set eth0 up`.
7. **Configure DNS (v2 NEW step).** Helper inside the new mntns opens `/etc/resolv.conf`, reads the first `nameserver` line; if matches `127.0.0.0/8`, bind-mounts the fallback resolv.conf (`nameserver $EOS_ISOLATED_WORKSPACE_FALLBACK_DNS`) over `/etc/resolv.conf`. See §4 DNS strategy. (v1's "install per-ws nft allowlist rules" step is gone — no per-ws nft state in v2.)
8. **Register handle.** Append to `_handles`, `_by_agent`. Set `status=active`, `last_activity=now`.
9. **Emit audit event** `sandbox_pinned_workspace_enter` with v2 payload: `{handle_id, agent_id, manifest_version, ns_ip, dns_fallback_applied, rfc1918_egress_mode}` (v2 dropped `allowlist_size`; see §7 audit events). `handle_id` is daemon-internal — kept in audit for diagnostics; not returned to the agent per S1.
10. **Return** `{success: true, manifest_version, manifest_root_hash}` (S1 — no `handle_id` exposed to the agent; daemon resolves the handle by `session.agent_id` on subsequent calls).

### Tool-call routing while pinned — **dispatcher-level op fork (R2)**

Routing is decided **at the dispatcher**, not by branching inside existing handlers. The default `api.v1.shell` / `api.read_file` / `api.write_file` / `api.edit_file` / `api.search_content` ops are **literally unchanged** — neither code nor signature. Pinned-mode ops are registered as separate ops on a separate handler module that **has zero imports from `sandbox.occ.*` or `sandbox.daemon.service.sandbox_overlay`** (verified by R3 import-graph test).

| Default op | Pinned counterpart (new) | Why distinct |
|---|---|---|
| `api.v1.shell` / `api.shell` | `api.pinned_workspace.shell` | Default-mode flow runs default; pinned-mode flow runs in `run_in_handle` |
| `api.v1.read_file` / `api.read_file` | `api.pinned_workspace.read_file` | Reads bypass overlay capture in pinned mode (overlay is opened in-ns) |
| `api.v1.write_file` / `api.write_file` | `api.pinned_workspace.write_file` | Writes go to tmpfs upperdir in-ns; never reach OCC |
| `api.v1.edit_file` / `api.edit_file` | `api.pinned_workspace.edit_file` | Same |
| `api.v1.search_content` / `api.search_content` | `api.pinned_workspace.search_content` | Same |

New handler module: `backend/src/sandbox/daemon/handler/pinned_workspace_ops.py`. Its imports are **bounded by construction** to: `sandbox.daemon.service.pinned_workspace` (PinnedWorkspaceManager + handle), `sandbox.daemon.request_context`, `sandbox._shared.clock`, and stdlib. No `sandbox.occ`, no `sandbox.daemon.service.sandbox_overlay`, no `SandboxOverlay`. (Verified by R3 import-graph test.)

Dispatch flow when agent calls a pinned op:
1. Dispatcher decodes envelope (existing path in `sandbox/daemon/rpc/dispatcher.py`, unchanged).
2. Op-table lookup hits `pinned_workspace_ops.<verb>`. Handler fetches the `PinnedWorkspaceHandle` by `session.agent_id` from `PinnedWorkspaceManager._by_agent` (S1 — no `handle_id` in request body). If the agent has no active pinned ws, return `no_pinned_workspace` error.
3. Handler invokes `PinnedWorkspaceManager.run_in_handle(handle, request_kind, payload)` which:
   - If the cgroup is frozen, `echo 0 > cgroup.freeze` first (§5).
   - Spawns the setns helper child (see §4 helper subprocess; R10 + R12 cover spawn discipline and ready-handshake).
   - Helper does the setns dance, forks, execs the verb-appropriate inner program (e.g., for `shell`: `/bin/sh -c …`; for `write_file`: a tiny `sandbox.daemon.scripts.in_ns_write` helper that opens/writes/closes the path).
   - Stdout / stderr / exit code are returned over the existing daemon pipe to the dispatcher.
   - Re-freeze the cgroup when child exits and no other in-flight ops for this handle.
   - Update `handle.last_activity`. Emit `sandbox_pinned_workspace_tool_call` audit event.
4. The default handlers (`shell.py`, `read.py`, `write.py`, `edit.py`, `search.py`) are NOT touched and have no `pinned_workspace_handle` arg awareness. Principle 2 (default flow untouched) is preserved by import-graph construction.

**Structural guarantee:** the import graph of `pinned_workspace_ops` excludes `sandbox.occ.*` and `sandbox.daemon.service.sandbox_overlay`. Any future refactor that tries to reuse "the existing overlay/publish helper" must literally add an import to the pinned ops module, which is caught at CI time by the R3 import-graph test.

### `exit_workspace() → {success, evicted_upperdir_bytes}` (S1)

Daemon RPC `api.pinned_workspace.exit`. Handler in same file. **No request payload required (S1):** handle resolved via `handle = _by_agent[session.agent_id]`. The agent never passes a `handle_id` because it doesn't have one — the daemon owns the mapping.

Sequence:
1. **Lookup + state transition.** Look up `handle = _by_agent.get(session.agent_id)` (S1); if absent, return `no_pinned_workspace` idempotently (a no-op exit is safe). Otherwise `status: active → exiting` under the manager lock.
2. **SIGTERM root_pid, wait up to `grace_s` (default 5s), SIGKILL on timeout.** Because the daemon owns `pidns_fd`, killing the pidns PID 1 reaps the *entire* pidns tree atomically (kernel guarantees zombie cleanup).
3. **Tear down netns.** Remove the veth host end from the bridge (the peer end disappears when the netns goes away): `ip link del {veth_host_name}`. Remove nftables rules tagged with that veth.
4. **Discard upperdir.** The mntns and pidns die with the holder's SIGKILL in step 2 — kernel reaps all mounts inside the new mntns automatically (the overlay at `workspace_root` and the tmpfs at `upperdir`). No explicit `umount` needed; trying to `umount workspace_root` from the daemon's mntns would be a no-op because the daemon never saw that mount. Belt-and-suspenders: `shutil.rmtree(handle.scratch_dir)` removes the daemon-side directory shells under `{scratch_root}/runtime/pinned-workspace/{handle_id}/`. **No `publish_*` exists in this function** — upperdir contents die with the tmpfs, never reconciled into the layerstack (R1/R2 structural separation enforces this; the function literally has no reachable call to `OccService`/`CommitQueue`).
5. **Release lease.** `layer_stack.release_lease(lease_id=handle.lease_id)` — exactly the same call `release_operation_overlay` uses today. This decrements `LeaseRegistry._refcounts` and allows squash/GC to proceed.
6. **Close ns FDs.** `os.close(...)` on all stored FDs (kernel reclaims the ns when the last ref drops, including any process refs — by this point the holder is dead).
7. **Deregister handle.** Remove from `_handles`, `_by_agent`. `status: exiting → stopped`.
8. **Emit** `sandbox_pinned_workspace_exit` audit event with `{handle_id, upperdir_bytes_discarded, lifetime_s}`.
9. **Return** `{success: true, evicted_upperdir_bytes}`.

### Re-entry: `enter_workspace()` while one is open (S1)

Per (b): **explicit error.** Returns `{error: {kind: "pinned_workspace_already_open", details: {created_at, last_activity}}}`. The SDK can then `exit_workspace() + enter_workspace()` to recreate. No `handle_id` is exposed (S1) — `created_at` / `last_activity` are diagnostic only, surfaced so an SDK can detect very-recent re-entries from buggy retry loops without needing to track the handle.

## 3. Interaction with LayerStack

### Lowerdir source — snapshot at `enter_workspace` (per RALPLAN-DR pick A1)

- `prepare_workspace_snapshot` (already in `sandbox/daemon/handler/workspace.py`) returns `lease_id`, `manifest`, `layer_paths`. We use these directly.
- The pinned ws's lowerdir = `layer_paths` at enter-time, period. If a peer agent publishes a new layer, the pinned ws is unaffected — its lease pins those exact layers against GC via `LeaseRegistry`.
- GC interaction: `LeaseRegistry.pinned_layers()` already returns pinned layers; the layer-stack squash code (`sandbox/layer_stack/squash.py`) already honors `squash_barrier_layers()`. **No layer-stack code change required.**

### Concurrent default-mode tool calls from the same agent while pinned ws is open

Allowed. The default flow (`SandboxOverlay.acquire_operation_overlay → publish_cycle → release_operation_overlay`) uses its own short-lived lease per call. Pinned and default coexist on different leases. The pinned ws is a side-channel — it doesn't appear in the active manifest, doesn't affect OCC validation.

### Concurrent agents

Each agent has its own quota (1 pinned ws default). Their leases pin their respective snapshots independently. The layerstack tip can advance freely; each pinned ws sees a frozen view of its enter-time snapshot.

### Behavior if the pinned ws's leased layer "would be GC'd"

`LeaseRegistry` already prevents this: as long as the lease is held, the layers cannot be GC'd or squashed-collapsed. The pinned ws is safe by construction. (If a future maintenance pass implements aggressive eviction, it must keep honoring `pinned_layers()` — which it does today, so the invariant holds.)

### No-upward-writes — the structural mechanism

1. **Different return type.** `PinnedWorkspaceHandle` is a new dataclass (not a subclass of `OperationOverlayHandle`). It has **no** `manifest`, `_overlay`, `release()` (it has `_pinned_release()` only), nor any callable that points at `SandboxOverlay.publish_*`.
2. **Different exit function.** `PinnedWorkspaceManager.exit(handle)` is the only sanctioned cleanup; it never imports `OccService`, `apply_changeset`, `publish_cycle`, or `publish_pending_changes`. Its full call graph is reachable from one file.
3. **Dispatcher-level op fork (R2).** Pinned-mode ops (`api.pinned_workspace.shell` / `read_file` / `write_file` / `edit_file` / `search_content`) live in a separate handler module `sandbox/daemon/handler/pinned_workspace_ops.py` whose imports are bounded to exclude `sandbox.occ.*` and `sandbox.daemon.service.sandbox_overlay`. Default handlers are unchanged. The two paths are mutually exclusive at the dispatcher layer.
4. **Unit test enforcement** (§7): (a) **Behavioral (R1):** mock `CommitQueue.apply` / `apply_sync` (the single OCC funnel for both `apply_changeset` and `commit_prepared`), drive a full pinned cycle, assert zero calls. (b) **Structural (R3):** import-graph walk of `pinned_workspace_ops` asserts no transitive import of `sandbox.occ.service`, `sandbox.occ.commit_queue`, `sandbox.daemon.service.sandbox_overlay`.

## 4. Networking

### netns creation

Via the `ns_holder` child's `unshare -Unpm`. `CLONE_NEWNET` is set at fork-time; we don't need to call `clone(2)` ourselves. Inside the netns, the holder sets `lo` up and waits.

### Bridge wiring (v2 — bridge + MASQUERADE, no allowlist)

**Egress model.** Isolated workspaces get full outbound connectivity through a daemon-owned NAT, identical to Docker's default bridge networking. Inbound from outside the daemon host is impossible by construction (no DNAT, ws IPs not routable externally). Two static deny rules harden the model: IMDS drop (cloud-credential exfil) and cross-veth isolation (peer-agent reachability).

- **One-time at daemon boot.** `PinnedWorkspaceManager.initialize()` installs the following network state (all idempotent — recreated only if absent):
  0. **v1→v2 migration sweep:** if a residual v1-named nft table exists (`inet eos_pinws`), flush + delete it (`nft delete table inet eos_pinws`). One-shot at boot only; not part of GC pass (GC only handles per-handle orphans, and v2 has no per-handle nft rules). Eliminates stale v1 allowlist tables across upgrades.
  1. **Bridge** `eos-shared0` with gateway IP `10.244.0.1/24`. **IP collision check:** if a bridge named `eos-shared0` already exists with a different gateway IP, OR if `10.244.0.0/24` is already routable on the host (operator's existing infra), fail fast with `isolated_workspace_init_failed: bridge_ip_collision` and the conflicting IP/subnet in the error detail. Optional override: `EOS_ISOLATED_WORKSPACE_BRIDGE_CIDR` lets operators pick a different /24 if `10.244.0.0/24` collides.
  2. **MASQUERADE rule** (static): `nft add rule inet eos_pinws_nat postrouting ip saddr 10.244.0.0/24 oifname != "eos-shared0" masquerade`. Source-NATs all outbound from the bridge subnet to the host's external interface.
  3. **IMDS drop rule** (static): `nft add rule inet eos_pinws_filter forward ip daddr 169.254.169.254 drop`. Blocks the cloud metadata service (EC2/GCP/Azure IAM-credential exfil path). Installed once at boot, never edited per-ws.
  4. **(Optional, opt-in) RFC1918 drop rule** (static, per Scenario 5): when `EOS_ISOLATED_WORKSPACE_RFC1918_EGRESS=deny` is set: `nft add rule inet eos_pinws_filter forward ip daddr {10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16} ip daddr != 10.244.0.0/24 drop`. Blocks ws-→-RFC1918 except the bridge subnet itself (so the ws can still reach its own gateway). When unset/`allow` (v2 default), this rule is absent — agent-permissive default.
  5. **(Optional, opt-in) Egress flow logging** (static, per Scenario 4): when `EOS_ISOLATED_WORKSPACE_AUDIT_EGRESS=true`: `nft add rule inet eos_pinws_filter forward iifname "veth-*" log prefix "iws_egress " counter`. Surfaces outbound destinations as kernel log lines; a daemon-side consumer (described in §7 observability) lifts these into `sandbox_pinned_workspace_egress_observed` audit events. Default `false` (volume).
  6. **Cross-agent isolation** is implemented via **Linux bridge port isolation** (kernel feature, ≥ 4.18), applied per-veth at enter time, not as a global rule. Each per-ws veth host-end gets `ip link set <veth-host> type bridge_slave isolated on` AND `ip link set <veth-host> type bridge_slave mcast_flood off` immediately after `master eos-shared0` (architect minor flag: `mcast_flood off` blocks mDNS / IPv6 NA-RA broadcast leak between ports; otherwise an attacker could announce a service on multicast that peer agents observe). Isolated ports cannot L2-forward to other isolated ports but can still reach the bridge (and via the bridge, the host's default route through MASQUERADE).
  7. **Operator-acknowledgement log:** at the end of `initialize()`, emit a `WARNING`-level log line enumerating all RFC1918 subnets reachable from the daemon's routing table (`ip -j route show | jq '.[] | select(.dst | startswith("10.") or startswith("192.168.") or startswith("172."))'`). This surfaces the daemon-host pivot surface (Scenario 5) to operators at every boot. If `EOS_ISOLATED_WORKSPACE_RFC1918_EGRESS_ACK=true` is set, log at `INFO` instead. Either way, never blocks boot — informational only.

  Why bridge port isolation, not an nft forward rule? Port isolation is a kernel-level data-plane mechanism and does not depend on `net.bridge.bridge-nf-call-iptables=1` (which is off by default in many deployments). It's a single property bit per bridge port — there's no rule-set drift surface.

- **Per-ws veth (in §2 step 6).** Replace step 6 of v1 with:
  - `ip link add <veth-host> type veth peer name <veth-ns>`
  - `ip link set <veth-host> master eos-shared0`
  - **`ip link set <veth-host> type bridge_slave isolated on`** ← cross-agent isolation
  - `ip link set <veth-host> up`
  - `ip link set <veth-ns> netns {root_pid}`
  - Inside the new netns (via `setns(netns_fd)` in a small helper): rename peer to `eth0`, `ip addr add <ns_ip>/24 dev eth0`, `ip route add default via 10.244.0.1`, `ip link set lo up`, `ip link set eth0 up`.

- **DNS strategy (NEW in v2, refined per architect feedback).** Isolated workspaces need name resolution for `pip install`, db hostnames, etc.
  - **Default path:** the lowerdir's `/etc/resolv.conf` (or the symlink target it resolves to) provides the nameservers. If it points to a routable resolver (`1.1.1.1`, `8.8.8.8`, a corporate VPC resolver IP — anything not in `127.0.0.0/8`), name resolution traffic exits via the bridge + MASQUERADE like any other UDP/TCP. Works out of the box.
  - **Detection MUST run inside the new mntns** (architect soundness fix). The lowerdir's `/etc/resolv.conf` may be a symlink (e.g., to `/run/systemd/resolve/stub-resolv.conf`); resolving the symlink from the daemon's mntns reads the daemon's `/run`, which is wrong. The setup helper (after `setns(CLONE_NEWNS)` and `mount_overlay` succeed, before signaling `ready`) opens `/etc/resolv.conf` *inside the new mntns* via `open("/etc/resolv.conf", O_RDONLY)` — this follows symlinks through the new mntns's filesystem view, giving the correct contents. It parses the first `nameserver` line and matches against `127.0.0.0/8`.
  - **Fallback path (systemd-resolved hosts):** if the first nameserver is in `127.0.0.0/8`, the helper bind-mounts a minimal `/etc/resolv.conf` containing `nameserver $EOS_ISOLATED_WORKSPACE_FALLBACK_DNS` (default `1.1.1.1`) at `/etc/resolv.conf` inside the new mntns. The bind mount stacks on top of the overlay's `/etc/resolv.conf` and is visible only inside the ws's mntns — daemon's resolver is unaffected.
  - **Survival across tool-call boundaries:** the fallback bind-mount is part of the mntns; it persists for the ws's lifetime (until SIGKILL of holder PID 1 reaps the mntns at exit). All subsequent tool calls in the same ws see the fallback resolv.conf.
  - **IPv6 caveat:** v2's MASQUERADE rule is IPv4-only (`inet nat POSTROUTING ip saddr 10.244.0.0/24 ...`). If the daemon host has IPv6 enabled and `/etc/resolv.conf` includes an IPv6 nameserver (`::1`, `2606:4700:4700::1111`, etc.), the ws may attempt IPv6 resolution which is not routed. **Mitigation:** the helper additionally strips IPv6 `nameserver` lines from the fallback resolv.conf (writing only the IPv4 fallback). If the lowerdir provides only IPv4 resolvers, no action needed. A future plan can add IPv6 NAT (`ip6table_nat` MASQUERADE for `fc00::/7` if a VPCv6 use case emerges); out of scope for v2.
  - **Verification:** `test_dns_resolves_inside_ns` in §7 covers two sub-cases (routable resolver + systemd-resolved fallback), both fixturable without external internet (see §7 fixture spec).

- **R13 daemon-boot viability probe (v2 — simplified).** Before `PinnedWorkspaceManager.initialize()` returns success:
  1. Confirm `CAP_NET_ADMIN` is held (read `/proc/self/status` for `CapEff`); fail fast with `isolated_workspace_init_failed: missing_cap_net_admin` if not.
  2. Test-create + delete a dummy bridge `eos-pinws-probe0`; fail fast if bridge ops error.
  3. Test-load + flush a no-op nft table `inet eos_pinws_probe`; fail fast if nft binary missing or unprivileged.
  4. Verify the three static rules (bridge create, MASQUERADE, IMDS drop) successfully install; fail fast on any error.
  5. Test-apply `bridge_slave isolated on` to the probe bridge's veth; fail fast with `isolated_workspace_init_failed: bridge_port_isolation_unsupported` if the kernel doesn't support it (kernel < 4.18 — should never happen given the 5.11 floor below, but checked anyway).
  6. Confirm cgroup v2 + freezer; fail fast with `isolated_workspace_init_failed: cgroup_v2_freezer_unavailable`.
  7. Confirm `kernel.unprivileged_userns_clone=1` via `sysctl` / `/proc/sys/kernel/unprivileged_userns_clone` OR daemon is running as root.
  8. Confirm Linux kernel ≥ 5.11 (overlay-in-userns; also covers ≥ 4.18 for bridge port isolation).

  If any probe fails: `EOS_ISOLATED_WORKSPACE_ENABLED` is forced to `false` for this daemon lifetime; `api.runtime.ready` returns `capabilities.isolated_workspace=false` with `init_failure_reason`; isolated ops return `feature_disabled`. Fail loud, not silent.

- **IP allocation (v2 — simplified).** Daemon-owned pool `10.244.0.0/24` (256 addresses; `.1` reserved as bridge gateway, `.0` / `.255` are network/broadcast). Allocate `/32` per workspace from `.2 – .254`. Track in `PinnedWorkspaceManager._allocated_ips: set[IPv4Address]`. **Pool ceiling = 253 simultaneous workspaces**, ~4× headroom over `EOS_ISOLATED_WORKSPACE_TOTAL_CAP=64`. Reconciled from `manager.json` on daemon restart. Allocator: lowest unused IP, O(N) scan over the in-memory set (N≤253). If the pool exhausts before `TOTAL_CAP`, log and fail with `isolated_workspace_ip_pool_exhausted` — this should never happen given the 4× headroom.

- **Default route (v2 — exists):** `default via 10.244.0.1 dev eth0` inside the netns. Outbound packets traverse: ws process → eth0 → veth-ns → veth-host → eos-shared0 bridge → host route → external interface (with MASQUERADE source-NAT applied).

- **Docker / compose config for orchestrator.** The daemon container needs `cap_add: [NET_ADMIN]`. **No `network_mode: service:...` is required for shared services** (none exist in v2 — agents reach external databases via their normal IPs through MASQUERADE, not through a shared daemon netns).

- **What's NOT in v2 (removed from v1):**
  - The R4 attach-mechanism table (sibling-container shared-netns picked) — gone; not relevant without shared services.
  - `EOS_PINNED_WS_SHARED_SERVICES` env var — gone; no shared-services concept.
  - The `pinned_workspace_network` block in `api.runtime.ready` exposing the allowlist — gone; nothing to publish (the only network state visible to clients is `capabilities.isolated_workspace=true|false`).
  - Per-ws nft allowlist install / per-veth tagged rules — gone; the only per-ws network state is the veth + IP allocation + port-isolation flag.

### Daemon `setns` flow per tool call

```
fd_user = handle.userns_fd; fd_mnt = handle.mntns_fd; fd_pid = handle.pidns_fd; fd_net = handle.netns_fd
setns(fd_user, CLONE_NEWUSER)   # if userns in use
setns(fd_mnt,  CLONE_NEWNS)
setns(fd_pid,  CLONE_NEWPID)    # affects *children's* pidns, not self
setns(fd_net,  CLONE_NEWNET)
# now os.fork() and the child is in the pinned pidns
```

**Python 3.11 caveat:** `os.setns` was added in Python 3.12. This project pins `python_version = "3.11"` (`pyproject.toml`), so the helper wraps `setns(2)` via `ctypes.CDLL("libc.so.6", use_errno=True).setns(fd, nstype)` and translates `errno` to a Python exception. Encapsulated in `sandbox.daemon.scripts._setns_libc` (new) so a future Python 3.12 upgrade swaps to `os.setns` in one place.

This runs in a *helper subprocess* (`sandbox.daemon.scripts.setns_exec`), not in the daemon directly — because `setns(CLONE_NEWPID)` is per-process and affects only future children. Wrapping in a helper subprocess keeps the daemon's own ns membership clean.

**R10 — helper spawn discipline (load-bearing for `setns(CLONE_NEWUSER)`):**
- The helper MUST be spawned via `os.posix_spawn` (or `subprocess.Popen` with `start_new_session=True`) from a daemon code path that has NOT yet initialized any thread pool. `setns(CLONE_NEWUSER)` fails with `EINVAL` if the calling thread is part of a multi-thread process (kernel requires single-threaded process at the time of the call).
- The helper module's **top-level imports** are strictly bounded to `os`, `sys`, `ctypes`, and `sandbox.daemon.scripts._setns_libc`. **No `asyncio`, no `logging`, no `subprocess`, no third-party deps**, no other `sandbox.*` modules. The helper does a single setns dance → fork → exec; it has no logging until after fork+exec, when it can use stderr writes directly.
- The post-fork child does its setns operations in a single-threaded context (just-forked) and is therefore safe. Pre-fork imports must not have started any threading.
- **Unit test (added to §7):** `test_setns_exec_helper_imports_are_minimal` — AST-parses `sandbox/daemon/scripts/setns_exec.py`, collects every `Import` / `ImportFrom` at module-level, asserts the set is exactly `{os, sys, ctypes, json, sandbox.daemon.scripts._setns_libc}` (json allowed for payload parsing). Any addition (e.g., `import logging`) fails the test.

### Reachability matrix (v2)

| From → To | Allowed? | Mechanism |
|---|---|---|
| Ws → `localhost:*` inside same ns | ✅ | netns `lo` |
| Ws → main workspace's postgres / external db / corporate VPC service | ✅ | Default route → bridge → MASQUERADE → host route |
| Ws → public internet (pypi.org, github.com, etc.) | ✅ | Same — `pip install` works |
| Ws → DNS resolver | ✅ | Routable resolver in `/etc/resolv.conf`; falls back to `1.1.1.1` if base image used `127.0.0.53` |
| Ws → peer agent's ws IP | ❌ | **Bridge port isolation** (`bridge_slave isolated on`) — kernel-level L2 forwarding block between isolated ports. No nft rule; no ruleset drift surface. |
| Ws → host metadata (169.254.169.254) | ❌ | **Single static nft drop rule** on `forward` chain. Installed once at boot; never edited per-ws. |
| Host / daemon → ws IP (inbound) | ❌ for agents | No DNAT; ws IPs (`10.244.0.X/32`) are not routable from outside the daemon's network namespace. Daemon process can `setns` into a ws's netns for tool-call execution; agents cannot reach netns IPs externally. |
| External (outside daemon host) → ws | ❌ | Daemon host's external interface has no route to `10.244.0.0/24` from external networks. |
| Ws → ws (same agent) | N/A | Quota=1 per agent (existing rule); only one ws per agent exists at a time. |

**Asymmetry guarantee:** outbound is fully permissive (modulo IMDS drop + cross-peer drop); inbound is structurally impossible. The asymmetry is the security model.

## 5. Resource controls

### Idle freeze via cgroup v2 freezer

- **SIGSTOP-to-PID-1-of-pidns does NOT freeze descendants.** Only SIGKILL has that special pidns-wide kernel behavior. SIGSTOP delivered to a single PID stops only that PID; children keep running and listening sockets keep accepting. Therefore use cgroup v2's freezer.
- At `enter_workspace`: create `/sys/fs/cgroup/eos-pinws-{handle_id}/`, write `+freezer` to its parent's `cgroup.subtree_control`, move the holder PID into `cgroup.procs`. On each `run_in_handle`, write each spawned child PID into the same cgroup *before* the helper does setns+fork+exec (set up via `cgroup.procs` from the daemon side using the helper's PID).
- Freeze (idle): `echo 1 > cgroup.freeze`. **The freezer is asynchronous (R11):** the kernel begins freezing the tree but processes in syscalls may complete those calls before stopping. Wait for completion by polling `cgroup.events` for the `frozen 1` line (use `inotify` if available, else poll every 50 ms) with a **2 s timeout**. On timeout: log + emit `pinned_workspace.freezer_stall_total` counter + fall back to per-pid `SIGSTOP` best-effort over the cgroup's `cgroup.procs`. The handle stays in `active` state with a `freezer_degraded=True` flag so the GC pass knows to use `SIGCONT` on thaw. Listening sockets remain kernel-side either way.
- Unfreeze (incoming tool call): `echo 0 > cgroup.freeze`; poll `cgroup.events` for `frozen 0` (or send `SIGCONT` to cgroup procs if `freezer_degraded`). Update `handle.last_activity`.
- Bonus: same cgroup gets us free per-ws CPU + memory accounting (read `memory.current`, `cpu.stat`) for the metrics gauges in §7.
- Kernel prereq: cgroup v2 with `freezer` controller available. Documented in §8.

### TTL eviction

- Configurable: `EOS_PINNED_WS_TTL_S` default 1800 (30 min).
- A background `asyncio` task in `PinnedWorkspaceManager` sweeps every 60s: any `handle` with `now - last_activity > TTL` gets auto-exited (same path as `exit_workspace`). Emit `sandbox_pinned_workspace_evicted` audit event with `reason: "ttl"`.

### Per-agent quota

- `EOS_PINNED_WS_PER_AGENT` default 1.
- Enforced at `enter_workspace` via the `_by_agent` map check (B1 — explicit error).

### Host-RAM gate (R6)

- Before accepting `enter_workspace`, the manager reads `MemAvailable` from `/proc/meminfo` and enforces:
  ```
  (active_pinned_ws + 1) × EOS_PINNED_WS_UPPERDIR_BYTES  ≤  0.5 × MemAvailable
  ```
  If breached, return `enter_workspace_failed: host_capacity_exceeded` with the computed budget vs. required values in the error details.
- This is independent of `EOS_PINNED_WS_TOTAL_CAP` (which is a static count cap): on a small host the RAM gate hits first; on a large host the count cap hits first. Both are AND-ed.
- The 0.5 ratio is configurable via `EOS_PINNED_WS_MEMAVAIL_FRACTION` (default 0.5). Test scaffolds in CI may want 0.3 to leave headroom for the runner itself.
- The gate is also re-evaluated by the TTL sweep: if current host pressure would have refused new entries, the sweep evicts LRU pinned ws proactively (down to the ceiling). Audit event `sandbox_pinned_workspace_evicted` with `reason: "host_pressure"`.

### Per-ws disk quota

- Upperdir is a tmpfs mount with `size=1G` (configurable `EOS_PINNED_WS_UPPERDIR_BYTES`). Out-of-space inside the pinned ws is a normal ENOSPC for the agent's commands — natural backpressure.
- Workdir on tmpfs too (overlay requirement: upper and work on same fs).

### Orphan PID reaping

- Pidns root strategy guarantees: SIGKILL of PID 1 reaps the entire pidns tree (kernel-level). No straggling PIDs from the daemon's view, because they're not in the daemon's pidns anyway.
- Worst case (daemon crash, see Scenario 2): pidns becomes orphaned; init (the actual host PID 1) reparents the holder. On daemon restart, GC pass finds orphan holders by naming convention and SIGKILLs them.

### Daemon-restart GC pass (per pre-mortem Scenario 2)

`PinnedWorkspaceManager.startup_gc()` — **strict ordering required (R5)**:

0. **Read on-disk state file** (`{scratch_root}/runtime/pinned-workspace/manager.json` — see R9 schema), if present, to compute the live-handle set vs. orphan set. **Also reconstruct `_allocated_ips` from `manager.json:handles[].ns_ip` here** (architect soundness fix: IP-pool init before any reap so a concurrent `enter_workspace` doesn't double-allocate an IP that step 5 will later free). The manager refuses `enter_workspace` calls until `startup_gc()` has finished step 0 — guard via an `_init_complete: asyncio.Event` set after step 8.

**Reap order, with rationale per step:**
1. **Unfreeze orphan cgroups (N4 — branch-aware).** For each `/sys/fs/cgroup/eos-pinws-*/` whose handle_id is not in the live set, do BOTH unconditionally: (a) `echo 0 > cgroup.freeze`, (b) read `cgroup.procs` and send `SIGCONT` to every PID listed. **Belt-and-suspenders picked over branching on persisted `freezer_degraded`:** `freeze=0` is a no-op for SIGSTOP'd processes (R11 freezer-stall fallback used per-pid `SIGSTOP` to freeze the tree, not the cgroup freezer), and `SIGCONT` is a no-op for processes that were frozen via the cgroup freezer and are now thawed — so applying both covers both code paths regardless of what `manager.json:freezer_degraded` says (and it's robust to a missing or version-mismatched `manager.json`, per N5). Then wait for `cgroup.events: frozen 0` if the cgroup-freezer path was active (best-effort 1 s; ignore timeout — step 2 will SIGKILL regardless). *Why first:* frozen tasks ignore SIGKILL until thawed; killing before unfreezing leaves zombies.
2. **SIGKILL pids in those cgroups.** Read `cgroup.procs`, `os.kill(pid, SIGKILL)` for each, wait for `cgroup.procs` to become empty (timeout 5 s; on timeout, log and continue — the kernel will eventually reap on init reparent).
3. **rmdir cgroup directories.** Only safe once `cgroup.procs` is empty.
4. **(v2 — simplified)** ~~Prune orphan nft rules~~ — **no-op in v2.** v2 has no per-ws nft rules to prune (the only nft rules are static: MASQUERADE + IMDS drop, both daemon-scope, never tied to a handle). Step number retained for diff clarity vs v1; implementation is a single comment + no-op. **v1→v2 migration:** if the daemon detects a residual `inet eos_pinws` table (v1 naming) at boot, it flushes and deletes the table — a one-shot operation in `initialize()` step 4 (see §4 bridge wiring + §8 migration), NOT recurring in GC.
5. **Delete orphan veths.** `ip link del {veth_host_name}` for any `eos-pinws-*` veth not in the live set. *Why before netns reap:* netns can be force-removed but kernel logs warnings when active veths still reference it; clean order avoids dmesg noise. **Free their IPs back to the pool:** for each orphan veth, look up the corresponding `ns_ip` from `manager.json` (if matching record exists) and remove from `_allocated_ips`. If no record (manager.json corrupted/missing), the IP stays in `_allocated_ips` from step 0; next allocator scan skips it — pool exhaustion risk is bounded by `EOS_ISOLATED_WORKSPACE_TOTAL_CAP=64`.
6. **Reap orphan netns names.** Any `ip netns` entry matching `eos-pinws-*` not in the live set.
7. **Release orphan layer-stack leases.** Walk `LeaseRegistry` (needs a new `iter_leases()` accessor returning `(lease_id, owner_request_id)`), release any whose `owner_request_id` starts with `pinned-` and have no matching live handle.
8. **rmtree orphan scratch dirs.** `find {scratch_root}/runtime/pinned-workspace/* -mtime +1h -not -in-live-set` and rmtree. Last because losing this leaves only stale upperdir bytes — non-blocking compared to leaked kernel resources. **After rmtree completes:** set `_init_complete.set()` so the manager begins accepting `enter_workspace` calls.

### Cost summary (v2)

Per-workspace cost at steady state, by resource:

| Resource | Per-ws cost | Scaling | Bound |
|---|---|---|---|
| **Disk** | **O(1)** — only the scratch-dir mount points (~1KB inodes) and a ~500-byte `manager.json` record | O(1) per ws | trivial |
| **Lowerdir bytes** | 0 (zero-copy via `prepare_workspace_snapshot(materialize=False)` — references shared layer dirs) | O(0) | n/a |
| **RAM (upperdir)** | tmpfs `size=1G` (default, configurable via `EOS_ISOLATED_WORKSPACE_UPPERDIR_BYTES`); consumed lazily on actual writes | O(N × upperdir_size) | R6 host-RAM gate: `(N+1) × upperdir ≤ 0.5 × MemAvailable` |
| **Kernel objects** | 1 cgroup, 1 mntns, 1 pidns, 1 netns, 1 userns, 1 veth pair, 1 bridge port-isolation flag, 4–5 FDs | O(N) per ws | `EOS_ISOLATED_WORKSPACE_TOTAL_CAP=64` (default); 253 IP-pool ceiling |
| **Network state** | 1 IP allocation (/32), 1 veth pair, 1 bridge port. NO per-ws nft rule | O(N) | Same as kernel-objects bound |
| **`manager.json`** | ~500 bytes per record | O(N) | ≤ 64 × 500B ≈ 32KB total |

**Aggregate at TOTAL_CAP=64:** disk ≈ 100KB; kernel-state ≈ negligible; RAM ≈ 64GB ceiling at default upperdir size (R6 gate prevents exceeding host budget; in practice far less because upperdir is consumed lazily).

### Env-var rename (v2)

All v1 `EOS_PINNED_WS_*` env vars are renamed to `EOS_ISOLATED_WORKSPACE_*`:

| v1 | v2 |
|---|---|
| `EOS_PINNED_WORKSPACE_ENABLED` | `EOS_ISOLATED_WORKSPACE_ENABLED` |
| `EOS_PINNED_WS_TTL_S` | `EOS_ISOLATED_WORKSPACE_TTL_S` |
| `EOS_PINNED_WS_PER_AGENT` | `EOS_ISOLATED_WORKSPACE_PER_AGENT` |
| `EOS_PINNED_WS_TOTAL_CAP` | `EOS_ISOLATED_WORKSPACE_TOTAL_CAP` |
| `EOS_PINNED_WS_UPPERDIR_BYTES` | `EOS_ISOLATED_WORKSPACE_UPPERDIR_BYTES` |
| `EOS_PINNED_WS_MEMAVAIL_FRACTION` | `EOS_ISOLATED_WORKSPACE_MEMAVAIL_FRACTION` |
| `EOS_PINNED_WS_SETUP_TIMEOUT_S` | `EOS_ISOLATED_WORKSPACE_SETUP_TIMEOUT_S` |
| `EOS_PINNED_WS_SHARED_SERVICES` | **removed** (no shared-services concept in v2) |
| — | **NEW:** `EOS_ISOLATED_WORKSPACE_FALLBACK_DNS` (default `1.1.1.1`) for systemd-resolved-host DNS fallback |

References in earlier sections of this doc that still use the old names should be read as the new names.

## 6. Threat / failure model

| Failure | Mechanism | Recovery |
|---|---|---|
| Daemon crash mid-pinned-ws | Holder orphaned (reparented to init, possibly frozen); cgroup leaks; veth + bridge port leaks; nft rules orphaned; upperdir scratch leaks; lease orphaned | Daemon-restart GC pass (§5, R5-ordered: unfreeze → kill → rmdir cgroup → nft prune → veth-del → netns-reap → lease release → scratch rmtree). Naming convention `eos-pinws-{handle_id}` is the GC key. `manager.json` (R9 schema) supplies the live-handle set; raw FDs not persisted — orphans reaped by convention. |
| Agent disconnect mid-test | Pinned ws unaffected — daemon owns it. Stays alive until TTL or agent reconnects on the same session (S1 — handle is keyed on `session.agent_id` in `_by_agent`, not on a token the agent must remember). | TTL eviction (§5). Audit event `evicted` with `reason: "ttl"`. |
| ns FD leak | Explicit `os.close()` in `exit_workspace`. Daemon supervises. | If daemon forgets a close, kernel still GCs when holder dies and FDs are dropped, but we lose track. Mitigation: FDs stored on `PinnedWorkspaceHandle`; manager iterates all handles on shutdown and closes. |
| Disk pressure from upperdir | tmpfs `size=1G` per ws; further commands ENOSPC | Agent test fails naturally; no global impact. |
| Resource exhaustion DoS (many enters) | Per-agent quota=1; global cap `EOS_PINNED_WS_TOTAL_CAP` default 64; **host-RAM gate (R6)** `(N+1)×upperdir ≤ 0.5×MemAvailable` | Reject `enter_workspace` with `quota_exceeded` or `host_capacity_exceeded`. |
| Host RAM pressure during steady state | TTL sweep re-evaluates host-RAM gate; evicts LRU pinned ws proactively if current state would refuse new entries | Audit `sandbox_pinned_workspace_evicted reason=host_pressure`. |
| Hung server, refuses SIGTERM | SIGKILL fallback after `grace_s=5s` (§2 exit step 2) | SIGKILL of pidns PID 1 reaps everything. |
| **Freezer stall** (cgroup v2 freezer doesn't reach `frozen 1` within 2 s — R11) | Log + counter `freezer_stall_total`; fall back to per-pid `SIGSTOP` over `cgroup.procs`; mark handle `freezer_degraded=True` so the next thaw uses `SIGCONT` instead | Ws remains usable; degraded flag surfaces in audit + `/api.pinned_workspace.status`. |
| **Setup timeout / D-state during fsmount** (N1) | Holder fails to reach `ready` within `EOS_PINNED_WS_SETUP_TIMEOUT_S` (default 30 s); typical cause is `mount(2)` wedged in `D` state under kernel/storage pressure | SIGKILL holder; partial rollback gated by per-step booleans; return `enter_workspace_failed: setup_timeout` with `failed_step` detail. Distinguishes infra wedge from genuine config error. |
| `setns` syscall fails | Helper `setns_exec` exits non-zero; tool call surfaces error to agent | No state corruption — pinned ws still active, just this call failed. |
| Overlay mount fails inside mntns | Same as default-mode mount failure path (existing `mount_failed` fallback logic in `PrivateNamespaceStrategy`) | Return error; agent retries or aborts. |
| **IMDS drop rule wiped** (operator runs `nft flush table inet eos_pinws_filter`) | Agent could reach `169.254.169.254` and steal IAM creds | Daemon-startup probe (R13 step 4) reinstalls the rule on boot. There is no in-flight reinstall — if an operator manually wipes nft at runtime, the rule stays gone until daemon restart. Integration test (`test_imds_dropped`) validates the rule is present after `initialize()`. Acceptable: manual `nft flush` requires root + intent. |
| **Bridge port isolation flag dropped** (someone manually removes `isolated on` from a veth) | Peer agents on the same bridge become L2-reachable | Two layers of defense: (a) the flag is set as part of veth creation at enter time, so a single-run lifecycle is correct by construction; (b) integration test `test_cross_agent_unreachable` asserts isolation across two real isolated workspaces. Manual mutation requires root + intent (same as above). |
| **Agent exfiltrates host secrets via arbitrary egress** (e.g., compromised pip dep POSTs env vars to attacker.com) | Outbound is permissive in v2 by design | Accepted residual risk consistent with the agent-permissive driver. Mitigations: (i) IMDS drop blocks the largest-impact target; (ii) daemon container should not have host secrets in env; (iii) lowerdir is read-only from agent view, so main-workspace secrets must be in the layerstack to be exfilable — they shouldn't be; (iv) opt-in flow logging via `EOS_ISOLATED_WORKSPACE_AUDIT_EGRESS=true` installs a single nft log+counter rule on the forward chain, surfacing outbound destinations into `sandbox_pinned_workspace_egress_observed` audit events (see Scenario 4 in pre-mortem). If higher isolation needed, run with an opt-in allowlist feature flag (future plan, not v2 default). |
| **Daemon-host network position becomes pivot surface** (v2 NEW row — architect-flagged, Scenario 5) | Daemon host's routing table determines what RFC1918 / VPC peers / internal admin APIs an agent's `pip` or `curl` can reach. MASQUERADE inherits the daemon-host's network position, not "the internet." A compromised dep can pivot into the daemon's VPC neighbors (RDS endpoints, internal services, possibly assumed IAM roles) — IMDS drop does NOT cover this. | **Operator-facing notice:** at `initialize()`, the daemon logs a `WARNING` enumerating all RFC1918 subnets present in the host's routing table; operator must acknowledge by setting `EOS_ISOLATED_WORKSPACE_RFC1918_EGRESS_ACK=true` (default unset → WARNING logs but does not block boot — this is informational at v2 baseline, not blocking, to preserve driver #4 ergonomics). **Opt-in deny:** `EOS_ISOLATED_WORKSPACE_RFC1918_EGRESS=deny` installs a static nft drop rule for RFC1918 dst (excluding the bridge's own 10.244.0.0/24, allowing in-ws reachability of own gateway), blocking VPC-peer pivot while leaving public-internet egress intact. Tested by `test_rfc1918_egress_drop`. Operators in production-VPC deployments should set `=deny` unless their use case explicitly requires VPC-peer reachability. |
| **IPv6 default route bypasses MASQUERADE** (architect-flagged) | If the daemon host has IPv6 enabled and the netns gets an IPv6 default route via veth (e.g., from router advertisements on the bridge if `accept_ra=1`), outbound IPv6 traffic bypasses the IPv4 MASQUERADE rule. IMDS drop is IPv4-only. | **Two-line mitigation in `enter_workspace` ns setup:** `ip -6 route del default || true` (drop any inherited v6 default) and `sysctl -w net.ipv6.conf.eth0.accept_ra=0` inside the ns. Net effect: v2 ws are IPv4-only by design. Tested by `test_no_ipv6_default_route_in_ns`. If a future use case needs IPv6 egress, a follow-up plan adds matching ip6tables NAT + drop rules. |
| **DNS misconfig in base image** (lowerdir `/etc/resolv.conf` points to `127.0.0.53` but fallback path didn't trigger) | `pip install pypi.org` hangs / fails inside ws; agent gets confusing timeout | Detection logic in enter-sequence checks first nameserver against `127.0.0.0/8` and bind-mounts fallback. Integration test `test_dns_resolves_inside_ns` covers both default and fallback paths. |
| Race: peer agent advances tip while pinned ws is open | None — pinned ws sees frozen snapshot (A1) | By design. |

## 7. Tests (deliberate mode — unit / integration / e2e / observability)

### Unit (`backend/tests/sandbox/daemon/service/test_pinned_workspace.py` — new)

| Test | Asserts |
|---|---|
| `test_pinned_handle_dataclass_has_no_publish_callable` | `PinnedWorkspaceHandle` instance has no attribute referencing `publish_cycle`, `publish_pending_changes`, `apply_changeset`. |
| `test_pinned_cycle_never_calls_commit_queue` (**primary, behavioral — R1**) | Patch BOTH `sandbox.occ.commit_queue.CommitQueue.apply` AND `CommitQueue.apply_sync` with `AsyncMock` / `MagicMock`. Drive a full `enter_workspace → pinned op (write a file via in-ns path) → exit_workspace` cycle. Assert `mock_apply.call_count + mock_apply_sync.call_count == 0`. **Why this layer:** `CommitQueue.apply` / `apply_sync` (at `backend/src/sandbox/occ/commit_queue.py:118,121`) is the *single* funnel for every OCC mutation — both `OccService.apply_changeset` (used by `SandboxOverlay._publish_upperdir` / `publish_workspace_paths`) and `OccService.commit_prepared` (used directly by `handler/edit.py:118` and `handler/write.py:130`) flow through it. Mocking `apply_changeset` alone misses `commit_prepared`; the commit-queue layer catches both. Test survives any `OccService` refactor. |
| `test_pinned_ops_module_import_graph_excludes_occ` (R3 + **N2**) | After importing `sandbox.daemon.handler.pinned_workspace_ops` (per R2), use `importlib.util.find_spec` + a transitive-import walk (BFS over each module's `__dict__` for `types.ModuleType` values, plus AST parse of each module's source for `from X import …` statements) and assert NONE of `sandbox.occ.service`, `sandbox.occ.commit_queue`, `sandbox.daemon.service.sandbox_overlay` are reachable. **N2 — close the dynamic-import loophole:** the AST walk additionally flags these node patterns in `pinned_workspace_ops` and its transitive deps: (1) `ast.Call(func=ast.Attribute(value=ast.Name(id='importlib'), attr='import_module'))` — `importlib.import_module(...)`; (2) `ast.Call(func=ast.Name(id='__import__'))` — `__import__(...)`; (3) `ast.Call(func=ast.Attribute(attr='import_module'))` — covers aliased `from importlib import import_module as foo; foo(...)` (best-effort, may have false positives on unrelated `.import_module()` methods — acceptable). On any match, fail with file:line of the offending node and the matched call shape. *Defense in depth:* catches a refactor that tries to reach `sandbox.occ.*` via dynamic import instead of `from … import …`. Structural complement to the behavioral R1 test. |
| `test_exit_call_graph_excludes_occ_source_scan` (secondary, weak signal — R16) | `inspect.getsource(PinnedWorkspaceManager.exit)` plus its in-module callables contains no reference to `apply_changeset`, `commit_prepared`, `publish_cycle`, `publish_pending_changes`, `publish_workspace_paths`. **Demoted:** signal-only; misses dynamic `getattr` and re-imports. Removable if noisy. Load-bearing defense is the behavioral test above + the R3 import-graph test. |
| `test_state_transitions` | `active → exiting → stopped` via mocked manager; invalid transitions raise. |
| `test_quota_one_per_agent` | Second `enter_workspace()` on the same session returns `pinned_workspace_already_open` (S1 — quota lookup is keyed by session-resolved `agent_id`; no request payload needed to drive the test). |
| `test_ttl_evict` | Manager with TTL=0.1s, enter, sleep, sweep — handle evicted, audit event emitted. |
| `test_ns_fd_close_on_exit` | Mock `os.close`; assert all four fds closed exactly once. |
| `test_cgroup_freeze_thaw_on_idle` | Mock the cgroup freezer write (`Path.write_text` on `…/cgroup.freeze`); assert `freeze=1` is written after `run_in_handle` returns with no other in-flight calls, and `freeze=0` is written before the next `run_in_handle` spawns its setns helper. |

### Test fixtures (v2 — added per critic C2)

Tests must not depend on uncontrolled public internet. Two pytest markers and one daemon-host fixture replace v1's external-host dependencies:

- **`@pytest.mark.requires_namespaces`** — needs `CLONE_NEW{NET,PID,MNT,USER}`; gated by env probe in `conftest.py`.
- **`@pytest.mark.requires_internet`** — needs outbound to public internet (pypi, cloudflare); CI fails loudly with `SkipReason: internet_required_but_unavailable` if not present. Tests that require this marker must be EXPLICITLY chosen to skip — never silently skipped.
- **`http_fixture_on_host` fixture** — boots a tiny Python `aiohttp` server on the daemon host's primary network interface (not the bridge), listening on a free port. Yields `(ip, port)`. Used by egress tests so MASQUERADE is exercised against a known target without hitting external internet.
- **`dns_resolver_on_host` fixture** — boots a tiny UDP DNS responder on the daemon host that answers a fixed name (`fixture.test`) → host's primary IP. Used by `test_dns_resolves_inside_ns` to validate resolution end-to-end without depending on PyPI.
- **`sentinel_layer` fixture** — publishes a layer containing `/testbed/sentinel-{uuid}.txt` with body `lowerdir-visible-{uuid}`. Yields the uuid. Used by `test_lowerdir_visible_inside_mntns` for exact-content asserts.

### Integration (`backend/tests/sandbox/daemon/integration/test_pinned_workspace_integration.py` — new, marked `@pytest.mark.requires_namespaces`)

| Test | Asserts |
|---|---|
| `test_server_survives_tool_call_boundary` | Enter; tool_call A starts `python -m http.server 8080 &` and returns. Tool_call B does `curl localhost:8080` — gets the directory listing. Assert PID of the http.server process is the same one A spawned (via `pgrep -f http.server` in another tool_call). |
| `test_netns_isolation_same_port` | Enter ws_A; start server on `:8080` inside. Enter ws_B (different agent); start server on `:8080` inside — no `EADDRINUSE`. Both reachable from their own tool calls; neither reachable from the other (no IP route). |
| `test_v2_driver_4_acceptance` (**v2 NEW — driver #4 measurable criterion**) | Uses `http_fixture_on_host` + `dns_resolver_on_host` fixtures. Enter ws (no operator config). Inside ws: (a) `getent hosts fixture.test` returns the host's primary IP; (b) `curl http://fixture.test:<port>/probe` returns HTTP 200; (c) fixture-side log shows the connection's source IP equals the daemon host's external IP (MASQUERADE SNAT verified); (d) no `EOS_ISOLATED_WORKSPACE_*ALLOWLIST*` env var was set. Test FAILS — not skips — if any of (a)–(d) fail. Marker: `@pytest.mark.requires_namespaces` only (no internet required). |
| `test_arbitrary_egress_works` (**v2 NEW**) | Uses `http_fixture_on_host`. Enter ws. Tool_call: `curl -s --max-time 5 http://<fixture_ip>:<port>/probe`. HTTP 200. Validates default route + MASQUERADE happy path with NO external internet dependency. Marker: `@pytest.mark.requires_namespaces`. |
| `test_external_internet_egress` (**v2 NEW, opt-in**) | Optional companion to the fixture-based test: `curl -s --max-time 5 https://www.cloudflare.com/cdn-cgi/trace` succeeds. Marker: `@pytest.mark.requires_internet`. Skipped only with loud `SkipReason: internet_required_but_unavailable`; pass/fail does NOT gate CI but skip-vs-pass must be visible in the CI report. |
| `test_imds_dropped` (**v2 NEW** — replaces v1 `test_no_host_metadata`) | Tool_call: `curl --max-time 2 http://169.254.169.254/` — connection refused / times out. From the daemon's own netns (outside ws), the same curl should succeed (or at least not be blocked by this rule) — confirms the drop is scoped to forward chain, not output. |
| `test_imds_rule_reinstalled_on_boot` (**v2 NEW**) | Stop daemon. From host (with root): `nft delete table inet eos_pinws_filter`. Restart daemon. Assert: `capabilities.isolated_workspace=true` (R13 probe reinstalled); `test_imds_dropped` passes again. Validates static-rule idempotent reinstall. |
| `test_masquerade_rule_reinstalled_on_boot` (**v2 NEW, M4**) | Same as above but delete the NAT table: `nft delete table inet eos_pinws_nat`. Restart daemon. Assert MASQUERADE rule present and `test_arbitrary_egress_works` passes. |
| `test_cross_agent_unreachable` (**v2** — mechanism changed) | Enter ws_A (`10.244.0.2`) and ws_B (`10.244.0.3`) as different agents. From ws_A: `ping -c 1 -W 2 10.244.0.3` — fails. `curl --max-time 2 http://10.244.0.3:<any-port>` — fails. Mechanism: Linux bridge port isolation (`bridge_slave isolated on`), NOT an nft rule. |
| `test_port_isolation_flag_present` (**v2 NEW, M4**) | After enter, daemon executes `bridge -j -d link show dev <veth-host>`; assert returned JSON has `isolated: true`. Detects accidental flag drop at runtime (e.g., a network-restart that re-attaches the veth without re-applying the flag). |
| `test_rfc1918_egress_drop` (**v2 NEW, Scenario 5**) | Boot daemon with `EOS_ISOLATED_WORKSPACE_RFC1918_EGRESS=deny`. Bring up `http_fixture_on_host` listening on an RFC1918 IP (`10.x.y.z`). Enter ws. Tool_call: `curl --max-time 2 http://10.x.y.z:<port>` → drops. Tool_call: `curl --max-time 5 http://1.1.1.1` → succeeds (or fixture on public IP if 1.1.1.1 unsuitable). Validates opt-in VPC-peer pivot block. |
| `test_no_ipv6_default_route_in_ns` (**v2 NEW, IPv6 mitigation**) | Enter ws. Tool_call: `ip -6 route show default` returns no default route. `sysctl net.ipv6.conf.eth0.accept_ra` returns `0`. Validates the IPv6 mitigation lines in `enter_workspace` ns setup. |
| `test_dns_resolves_inside_ns` (**v2 NEW**) | Uses `dns_resolver_on_host` fixture. Two sub-cases driven by a fixturable lowerdir: (a) lowerdir's `/etc/resolv.conf` = `nameserver <fixture_resolver_ip>` (routable path): enter ws, `getent hosts fixture.test` returns fixture's IP. (b) lowerdir's `/etc/resolv.conf` = `nameserver 127.0.0.53` (systemd-resolved fallback path): enter ws, daemon detects 127.0.0.0/8 INSIDE THE NEW MNTNS (architect fix), bind-mounts fallback; `getent hosts fixture.test` returns IP via fallback DNS. Marker: `@pytest.mark.requires_namespaces`. |
| `test_dns_fallback_survives_tool_call_boundary` (**v2 NEW**) | Sub-case (b) of above: after fallback bind-mount applied, run a SECOND tool call; assert `cat /etc/resolv.conf` still shows fallback nameserver. Validates bind-mount lifetime equals mntns lifetime. |
| `test_dns_symlinked_resolv_conf` (**v2 NEW, architect soundness**) | Lowerdir ships `/etc/resolv.conf` as a symlink to `/run/systemd/resolve/stub-resolv.conf`. Daemon host's own `/run/systemd/resolve/stub-resolv.conf` points to `127.0.0.53` (or doesn't exist). After enter, detection must resolve the symlink INSIDE the new mntns (where `/run/systemd/resolve/` may not exist or differ). Assert fallback was applied based on the in-mntns symlink-target reading, not the daemon's. |
| `test_ip_pool_exhaustion` (**v2 NEW, M4**) | Set `EOS_ISOLATED_WORKSPACE_TOTAL_CAP=4` (test-friendly low value) and an IP pool of /29 (6 usable). Enter 4 ws successfully. Mock the cap to allow 7 enters; the 5th attempt returns `isolated_workspace_ip_pool_exhausted` with clear error detail. Validates allocator behavior under boundary conditions. |
| `test_lowerdir_visible_inside_mntns` (**v2 NEW** — sharp-corner test) | Uses `sentinel_layer` fixture. Enter ws. Tool_call: `cat /testbed/sentinel-{uuid}.txt`; assert exact body `lowerdir-visible-{uuid}`. Validates `setns(CLONE_NEWNS)` + `fsmount` correctly sees daemon-side layer dirs through new mntns's mount propagation. Regression guard for any future change to `unshare --propagation private` semantics. |
| `test_upperdir_discarded_on_exit` | Enter; tool_call writes `/testbed/scratch.txt`. Exit. Enter again with same lowerdir snapshot. Tool_call `cat /testbed/scratch.txt` — file not found. |
| `test_lowerdir_pinned_against_peer_publish` | Enter ws_A. Peer agent publishes a new layer (via existing default-mode flow that calls `publish_workspace_paths`). Tool_call in ws_A: assert pre-publish view of files is intact (e.g., a file the peer deleted is still readable). |
| `test_daemon_restart_gc` | Enter ws. Kill daemon. Restart daemon. Assert orphan veth `eos-pinws-*` and netns gone; lease released; cgroup dir rmdir'd; scratch dir rmtree'd; IP returned to pool. Order is verified by inspecting daemon logs for the R5 sequence. (v2: no nft rule prune step since there are no per-ws nft rules.) |
| `test_imds_rule_reinstalled_on_boot` (**v2 NEW**) | Stop daemon. From host (with root): `nft delete table inet eos_pinws_filter`. Restart daemon. Assert: (a) `api.runtime.ready` returns `capabilities.isolated_workspace=true` (R13 probe reinstalled the static rules); (b) `test_imds_dropped` passes again. Validates that the static rules are idempotently reinstalled at every daemon boot. |

### E2E (`backend/tests/live_e2e_test/sandbox/test_pinned_workspace_e2e.py` — new, gated by existing live_e2e flag)

| Test | Asserts |
|---|---|
| `test_pytest_against_inproc_server` | Enter; tool_call 1 boots `flask run` on `:5000`; tool_call 2 runs `pytest tests/integration -k http` against `localhost:5000`. Pytest passes. |
| `test_full_lifecycle_with_real_db` (**v2 — no allowlist arg**) | Bring up postgres reachable at a known IP on the host's network (no shared-netns config required in v2 — postgres can be anywhere routable from the daemon host). Enter ws (no args). Tool_call: `alembic upgrade head` against the db IP (writes to db, not workspace). Tool_call: `pytest tests/db`. Exit. Re-enter: no test artifacts in workspace (`/testbed`), but db state from migrations still in postgres (because that's external). |
| `test_pip_install_then_run` (**v2 NEW** — `@pytest.mark.requires_internet` + `requires_namespaces`) | Enter ws. Tool_call: `pip install --target /tmp/pkg httpx` (writes to upperdir; uses default route + DNS + HTTPS to PyPI end-to-end). Tool_call: `PYTHONPATH=/tmp/pkg python -c "import httpx; r = httpx.get('https://httpbin.org/get', timeout=5); print(r.status_code)"` — prints 200. Validates the entire networking stack (DNS + bridge + MASQUERADE + outbound HTTPS) and that dep-install + dep-use across tool-call boundaries works. **CI behavior:** skipped LOUDLY with `SkipReason: internet_required_but_unavailable` if no internet. Local-wheelhouse fallback (`pip install --no-index --find-links /var/wheelhouse httpx`) is the offline-CI alternative if the local wheelhouse is provisioned. Test name implies internet; offline alt is a separate test `test_pip_install_offline_wheelhouse`. |
| `test_agent_concurrent_default_and_pinned` | Agent has pinned ws open; agent also issues a default-mode tool_call. Default-mode call sees the layerstack tip (which may have advanced from peer publishes); pinned-mode call sees frozen snapshot. Neither interferes. |

### Observability

- **Audit events** (new enum values, new entries in `sandbox_events.jsonl` via existing `AuditRecorder._record_sandbox_event` hook):
  - `sandbox_pinned_workspace_enter` — payload: `{handle_id, agent_id, manifest_root_hash, manifest_version, ns_ip, dns_fallback_applied: bool, rfc1918_egress_mode: "allow"|"deny"|"cidr"}` (v2: `allowlist_size` removed — no allowlist; new fields capture v2-specific config snapshots).
  - `sandbox_pinned_workspace_tool_call` — payload: `{handle_id, op, exit_code, duration_s, idle_unstop_s, idle_stop_s}`.
  - `sandbox_pinned_workspace_exit` — payload: `{handle_id, reason: "explicit"|"ttl"|"daemon_shutdown", lifetime_s, upperdir_bytes_discarded, kill_required: bool}`.
  - `sandbox_pinned_workspace_gc_orphan` — payload: `{kind: "veth"|"netns"|"scratch"|"lease"|"ip_alloc", identifier}`.
  - **(v2 NEW, opt-in)** `sandbox_pinned_workspace_egress_observed` — emitted only when `EOS_ISOLATED_WORKSPACE_AUDIT_EGRESS=true`. Payload: `{handle_id, dst_ip, dst_port, proto, bytes_estimate, ts}`. Sourced from nft `log` rule output, parsed by a daemon-side `nftables` log consumer. Disabled by default (volume) but specified so a security-incident response can flip the bit + restart daemon + start collecting outbound destinations.
- **Metrics** (extend `MetricsAggregator` in `task_center_runner/audit/metrics.py`):
  - `pinned_workspace.active_count` — gauge.
  - `pinned_workspace.netns_count` — gauge (should equal active_count).
  - `pinned_workspace.bridge_ports_in_use` — gauge.
  - `pinned_workspace.evicted_total{reason}` — counter.
  - `pinned_workspace.tool_call_duration_s` — histogram.
- **Structured logs** (existing `logging.getLogger("sandbox.daemon.service.pinned_workspace")`):
  - `setns_failed` — log + control_ref-style error JSON returned to agent. Includes which ns FD failed.
  - `mount_failed_inside_ns` — same.
  - `veth_create_failed` — same.
  - `lease_release_failed_during_exit` — log at ERROR; do not block exit (exit must complete to free veth/ns).

## 8. Migration

- **Purely additive code path (R2).** Default `workspace-per-tool_call` (existing `SandboxOverlay.acquire_operation_overlay` → `publish_cycle` → `release_operation_overlay`) is untouched. Existing dispatcher ops (`api.v1.shell` / `api.read_file` / `api.write_file` / `api.edit_file` / `api.search_content`) and their handlers (`shell.py`, `read.py`, `write.py`, `edit.py`, `search.py`) are literally unmodified. Isolated-workspace ops are wholly new dispatcher entries pointing at the new `pinned_workspace_ops` handler module, registered under the `api.isolated_workspace.*` namespace.
- **Feature gate (v2 rename).** Env flag `EOS_ISOLATED_WORKSPACE_ENABLED` (default `false`). When false, daemon does not initialize `PinnedWorkspaceManager`, does not create `eos-shared0`, does not install any of the v2 static rules (MASQUERADE / IMDS drop), does not register `api.isolated_workspace.*` ops. Returns `feature_disabled` error if those ops are called.
- **Capability negotiation (v2 rename).** `api.runtime.ready` response gains a `capabilities: {isolated_workspace: bool}` field. Agent SDK exposes `enter_isolated_workspace` / `exit_isolated_workspace` tools only when daemon advertises `isolated_workspace=true`. If init fails (R13 probe), `isolated_workspace=false` with an `init_failure_reason` string.
- **Orchestrator config (v2 — simpler than v1).** Daemon container requires `cap_add: [NET_ADMIN]`. **No `network_mode: service:<daemon>` is required** (v1 needed it for sibling shared-service containers; v2 has no shared-services concept — agents reach external dbs via their normal IPs through MASQUERADE). External services postgres/redis/etc. can run anywhere routable from the daemon host's network namespace (same compose project, sibling project, external host, cloud-managed db, etc.) — they just need to be reachable by IP.
- **Rollout phases:**
  1. **Dev-mode** (devs only, behind flag): unit + integration tests passing; manual smoke from sweevo runner.
  2. **CI-mode** (CI runners only): full e2e test suite passes in CI; observability dashboards built.
  3. **Prod (sweevo benchmark runners)**: enable in default sweevo config; isolated-workspace mode becomes opt-in per scenario in `backend/src/benchmarks/sweevo/sandbox.py`.
- **No data migration.** No layer-stack on-disk format change. No OCC schema change. New `manager.json` is greenfield and disposable (rebuilt by GC pass).
- **No shared schema change** beyond the new enum values added to `task_center_runner/audit/events.py`.

---

## Plausible new module paths

| Path | Role |
|---|---|
| `backend/src/sandbox/daemon/service/pinned_workspace.py` | `PinnedWorkspaceManager`, `PinnedWorkspaceHandle` (new) |
| `backend/src/sandbox/daemon/handler/pinned_workspace.py` | RPC handlers `api.pinned_workspace.enter|exit|status` (new) |
| `backend/src/sandbox/daemon/handler/pinned_workspace_ops.py` | RPC handlers `api.pinned_workspace.{shell,read_file,write_file,edit_file,search_content}` — **import-bounded module** excluding `sandbox.occ.*` and `sandbox.daemon.service.sandbox_overlay` (new, R2) |
| `backend/src/sandbox/daemon/scripts/setns_exec.py` | helper child for `setns`+fork+exec (new; R10 import discipline) |
| `backend/src/sandbox/daemon/scripts/_setns_libc.py` | `ctypes` wrapper around libc `setns(2)` for Python 3.11 (new) |
| `backend/src/sandbox/daemon/scripts/in_ns_write.py` | tiny in-ns write helper used by `pinned_workspace.write_file` op (new) |
| `backend/src/sandbox/daemon/scripts/ns_holder.py` | long-lived holder, PID 1 of new pidns; two-step handshake per R12 (new) |
| `backend/src/sandbox/daemon/service/pinned_network.py` | bridge + veth + nftables management, IP pool (new) |
| `backend/src/task_center_runner/audit/events.py` | add `sandbox_pinned_workspace_*` `EventType` enum values (edit, R8) |
| `backend/src/sandbox/daemon/rpc/dispatcher.py` | register 3 + 5 new pinned ops in `_load_peer_bootstraps` (edit, ~16 lines) |
| `backend/src/sandbox/daemon/__main__.py` | construct `PinnedWorkspaceManager`, call `initialize()` and `startup_gc()` (edit) |
| `backend/src/sandbox/daemon/handler/health.py` | extend `api.runtime.ready` response with `capabilities.pinned_workspace` and `pinned_workspace_network` block (edit, R4/R13) |

**Not edited (per R2):** `backend/src/sandbox/daemon/handler/{shell,read,write,edit,search}.py` are literally untouched. Default ops route to the same code as before; pinned ops route through the new dispatcher entries to the bounded handler module.
| `backend/tests/sandbox/daemon/service/test_pinned_workspace.py` | unit (new) |
| `backend/tests/sandbox/daemon/integration/test_pinned_workspace_integration.py` | integration (new) |
| `backend/tests/live_e2e_test/sandbox/test_pinned_workspace_e2e.py` | e2e (new) |

All class/function names are **working names**; the implementation may shorten or rename for clarity.

---

## ADR (v2)

**Decision.** Introduce a daemon-side `enter_isolated_workspace` / `exit_isolated_workspace` mode that pins per-agent {net, pid, mnt, user} namespaces + an overlay (snapshot-at-enter lowerdir + tmpfs upperdir) for the lifetime of multiple consecutive tool calls. Upperdir writes are ephemeral and structurally cannot reach OCC. Per-ws netns has full outbound connectivity via daemon-owned bridge + MASQUERADE; inbound from outside the daemon host is impossible by construction. Two static deny rules harden the model (IMDS drop, bridge-port-isolation against peer-agent reachability). No operator-declared allowlist.

**Drivers.**
1. **Stateful test scaffolding correctness** (servers survive tool-call boundaries).
2. **No-merge-back invariant survives future refactors** (structural separation from OCC: distinct handle type + distinct exit path + import-bounded handler module + behavioral-mock unit test).
3. **Bounded resource cost at rest** (idle freezer; tmpfs upperdir; host-RAM gate; quota; TTL).
4. **(v2 NEW) Agent egress works without operator pre-config.** Agents must be able to `pip install` from PyPI, reach external databases by hostname/IP, and behave like a normal Linux process from a network standpoint. This rules out allowlist-based egress filters.

**Alternatives considered.**
- Lowerdir source: snapshot-at-enter (A1, picked) vs live-tip (A2, rejected — breaks reproducibility, mid-test lowerdir mutation).
- Re-entry: explicit error (B1, picked) vs implicit recreate (B2, rejected — silent server discard).
- OCC unreachability: distinct handle class (C1) + distinct exit RPC (C2) (stacked, picked) vs runtime string assertion (C3, rejected — rots).
- **Network egress model (v2 — replaces v1's D-series + R4):**
  - **D-v2-α.** Bridge + MASQUERADE, no allowlist, IMDS drop + cross-veth port isolation. **Picked.** Matches driver #4; static rules don't rot; same threat profile as "developer with internet on their laptop."
  - D-v2-β. Pure isolation (loopback only, no outbound). Rejected — breaks `pip install` and external-db connectivity.
  - D-v2-γ. Unix-socket bind-mount for declared services. Rejected — still requires operator per-service pre-config; doesn't support `pip install` to PyPI (no socket interface).
  - D-v2-δ (v1's pick). Bridge + nftables allowlist + `EOS_PINNED_WS_SHARED_SERVICES` operator config. Rejected — allowlist drift is the load-bearing security risk (R15 was needed to catch silent default-allow on misconfig); operator pre-config burden contradicts driver #4.
  - D-v2-ε. slirp4netns userspace networking. **Deferred with explicit trigger.** Reconsidered if **(a)** any target deployment refuses `CAP_NET_ADMIN` (e.g., a hardened Kubernetes runtime with restrictive PodSecurityPolicy), OR **(b)** v2's veth/bridge/IP-pool GC complexity becomes a maintenance burden documented in a post-mortem. Currently neither holds: sweevo runs in a NET_ADMIN-grantable container; v2's GC is well-specified (R5). Pros not enumerated in v1's rejection but worth recording: no host bridge, no IP-pool, no kernel-4.18 floor for port isolation, simpler GC, but throughput ceiling ~1-2 Gbps (kernel bridge gives ~10+ Gbps).
- **Cross-agent isolation mechanism (NEW in v2):**
  - **Bridge port isolation** (`bridge_slave isolated on`, kernel ≥ 4.18). **Picked.** Single property bit per veth; no nft rule; no `net.bridge.bridge-nf-call-iptables` dependency.
  - nft forward DROP rule between veth interfaces. Rejected — requires bridge-nf-call-iptables, more moving parts.
  - Per-ws subnet (each ws on its own /29 with separate bridge). Rejected — multiplies bridge state per ws.
- Routing fork (R14, e): per-handler arg branch (e1, rejected — edits 5 hot files, violates Principle 2) vs dispatcher op-level fork (e2, picked — preserves import-graph bound) vs separate dispatcher namespace (e3, rejected — overkill).
- IP pool layout (v2 revision): `/24` flat with `/32` per ws (picked) vs v1's `/20` carved into `/29`s (rejected — wasteful, math was wrong).
- Doing nothing / host-port DNAT mapping: rejected — port-collision problem is the whole motivation.
- Full per-agent Docker container: rejected — heavier than netns; redundant with existing daemon/sandbox architecture.

**Why chosen.** A1 + B1 + (C1+C2) + **D-v2-α** + bridge port isolation reuses existing primitives (`LeaseRegistry`, `OperationOverlayHandle` pattern, `prepare_workspace_snapshot`, `mount_overlay`, `AuditRecorder._record_sandbox_event`) with minimal new surface area. The structural separation against OCC is double-locked (distinct type + distinct code path) and verifiable by unit test. The v2 networking model collapses ~40% of v1's network complexity (allowlist lifecycle, shared-services env var, `network_mode:` orchestrator config, R15 default-deny test) while preserving the only two security properties that matter: inbound-impossible (kernel-level: ws IPs not routable externally) and IMDS-unreachable (single static rule).

**Consequences.**
- **New module surface** (~8 new files, ~3 edited files; no edits to `shell.py`/`read.py`/`write.py`/`edit.py`/`search.py`). ~950 LoC core + ~1300 LoC tests. Manageable.
- **Disk cost is strictly O(1) per ws** (see §5 cost summary). RAM is O(N × upperdir_size) gated by R6. Kernel resources O(N) gated by `EOS_ISOLATED_WORKSPACE_TOTAL_CAP=64`.
- **Network surface (v2):** 1 bridge, 1 MASQUERADE rule, 1 IMDS drop rule — total 3 pieces of network state at daemon scope, all static and installed once. Per-ws: 1 veth + 1 IP + 1 port-isolation flag. No per-ws nft rules.
- **Daemon-restart GC** is a hard correctness requirement (R5 ordering is non-negotiable: unfreeze → kill → rmdir cgroup → veth-del → netns-reap → lease release → scratch rmtree → IP-pool reconcile). Skipping or reordering leaks kernel resources. v2 drops the "prune orphan nft rules" step (no per-ws rules exist).
- **The setns helper module's import discipline (R10)** is a maintenance constraint — adding `import logging` to `setns_exec.py` will break `setns(CLONE_NEWUSER)`. AST test guards this.
- **Freezer non-atomicity (R11)** means there is a brief (≤2 s) window after `freeze=1` where in-cgroup tasks can still execute; the SIGSTOP fallback covers timeouts but yields a `freezer_degraded` flag in audit.
- **Agent API is intentionally minimal (S1).** `enter_isolated_workspace()` / `exit_isolated_workspace()` take no arguments — agent identity is daemon-resolved from session state; quota=1 makes per-agent `handle_id` tracking unnecessary. No allowlist arg, no per-service config — agents reach external resources by their normal IPs.
- **v2 threat-model shift (explicit acceptance):** outbound is permissive. A compromised agent (e.g., via malicious PyPI dep) can exfiltrate data to arbitrary internet IPs. The IMDS drop mitigates the highest-impact cloud-credential leak; other exfil is accepted as residual risk consistent with "developer running pip install on their laptop." Operators handling adversarial code at higher isolation requirements should run with an opt-in allowlist (a future, separate feature flag — NOT v2 default).
- **Kernel + runtime prerequisites** (must hold on every host that enables `EOS_ISOLATED_WORKSPACE_ENABLED`):
  - `unshare(CLONE_NEWUSER)` requires `kernel.unprivileged_userns_clone=1` or daemon running as root.
  - **Overlay-in-userns** requires Linux kernel ≥ 5.11 (when overlayfs gained unprivileged userns mount support).
  - **Bridge port isolation** requires Linux kernel ≥ 4.18 (subsumed by the 5.11 floor above).
  - **cgroup v2** mounted at `/sys/fs/cgroup` (cgroup v1 mode is unsupported) with the `freezer` controller available.
  - `python_version = "3.11"`: `setns(2)` accessed via ctypes wrapper around libc (§4).
  - `ip` and `nft` binaries present; CAP_NET_ADMIN required for bridge/veth/nft management. No `network_mode: service:...` config required (v1 needed it; v2 doesn't).
  - DNS: lowerdir `/etc/resolv.conf` must point to a routable resolver, or the daemon-side fallback (`EOS_ISOLATED_WORKSPACE_FALLBACK_DNS`, default `1.1.1.1`) applies.
  - Document all of these in `backend/README.md` (or wherever runtime prereqs already live) before phase-2 rollout.

**Follow-ups (out of scope for this plan).**
- Explicit cache primitive for "I want pip wheels to survive `exit`" (per pre-mortem Scenario 3). Should be a separate plan; isolated mode does NOT solve this.
- **Opt-in egress allowlist feature flag** for higher-security deployments (re-introduces v1's allowlist mechanism behind an opt-in flag, NOT default). Out of scope for v2 baseline.
- Per-egress flow logging for observability ("which test phoned home where"). nft can emit log entries on the forward chain if needed; out of scope.
- Optional inbound host-port publish for non-testing use cases (e.g., interactive debugging — would re-introduce DNAT). Out of scope.
- Migration of existing `PrivateNamespaceStrategy` (which is per-call mount-namespace) to share infrastructure — likely not worth it; they serve different roles.
- Userns uid-mapping policy: do we map agent's uid 1:1 or shift? Needs a follow-on decision before prod rollout.
