# FINAL REVIEW REPORT — sandbox-rust-external-migration-PLAN.md

Reviews the fully-amended plan against **every** page of `docs/architecture/sandbox/` (details, invariants, performance + resource requirements). 13-agent consensus (10 per-page coverage + Architect + Critic + synthesis), primary-source-verified.

**Execution follow-up (2026-06-02):** this report is retained as the review artifact, but the live plan now states that MF-1 gate text is applied and SF-1/3/4/5/6 are folded. Current Rust code/docs implement the non-plugin SF-4/SF-5/SF-6 daemon/layerstack surfaces and the Phase 3T isolated command-routing/control-plane slice. Full AV-9 isolated lifecycle parity, including isolated handle TTL/phase-budget and foreground exit-drain behavior, remains later-phase scope. Plugin AV-10/Pyright parity remains the skipped/open Phase 3T tail.

## 1. Verdict

**APPROVE WITH CONDITIONS.** The amended crate structure is architecturally sound; after execution cleanup the live workspace is 10 runtime crates plus `xtask`, with the obsolete `eos-ephemeral` crate removed. The HINGE (`eos-isolated ⊥ eos-occ`) is source-verified to hold at exactly one severable touch site, and every architecture performance/resource requirement maps to a falsifiable gate. The original review condition was MF-1, the single-linearization-point gate text for PPC self-managed plugin OCC callbacks as a second entry point; that gate text has since been applied in the plan, while AV-10 execution remains separate skipped scope.

## 2. Architecture Coverage Matrix

| Arch page | Key perf/resource requirement | Covered? | Plan anchor / gap |
|---|---|---|---|
| **overview** | O(1) snapshot (lease+layer_paths, never rendered tree) | Yes | §1; CP-0; `test_*_lowerdir_disk_is_o1*` |
| | Manifest CAS = single linearization point | Yes (plugin tail open) | AV-1c(1) + CP-4 final-state hash; MF-1 gate text is applied for the PPC self-managed callback, while AV-10/Pyright execution remains open |
| | Capture+publish = 1 atomic unit / op | Yes | AV-1/AV-3 + §7 |
| | OCC batching (N disjoint = 1 CAS, near-linear) | Yes | PV-3 params (0.002/64/3) + CP-4. Near-linear is a design property, no numeric SLO to gate |
| | Isolated never publishes | Yes | Build-time `eos-isolated ⊥ eos-occ` (HINGE) + `test_full_cycle_never_calls_occ` + AV-9 |
| | Dual-layer lease (flock + refcounted RLock) | Yes | PV-1 |
| | Per-op fixed cost; signature fail-closed; put_archive | Yes | CP-2a/CP-2b; AV-8; CP-1 |
| | Isolated upperdir RAM/TTL/cap budgets | Partial | Rust `eos-isolated::ResourceCaps` enforces the matching `TOTAL_CAP` and host RAM admission contract; isolated handle TTL/phase-budget parity stays in AV-9 later-phase scope |
| **layerstack** | O(1) snapshot; CAS; leased_layers vs lease_head_layers; deferred-GC | Yes | §1; AV-1c(1)(2); §7 property tests |
| | Manifest depth ceiling (~16-layer mount(8)) | Yes | CP-0 syscall floor; `max_depth` is a runtime param |
| | Squash acceptance/concurrency + single-worker guard | Yes (verified/named) | Plan §3 SF-5; Rust `AutoSquashMaintenancePolicy`, `LayerStack::squash()`, and the storage-writer guard reproduce the trigger + single-writer boundary |
| | CP-0..CP-5 parametric gates; AV-1c; AV-7 rollback | Yes | §3; §4 |
| **overlay** | O(1) snapshot; CAS; capture+publish atomicity; mount-ns lifecycle | Yes | CP-0/AV-1/AV-1c; eos-overlay/eos-runner; Phase 1–3.5 |
| | Per-op fixed cost amortization (isolated) | Yes | CP-2a/CP-2b; Phase 3.5 |
| | Shell-free IPv6 hardening (rtnetlink + /proc/sys) | Yes | §3.5 + CP-1b read-only-rootfs + §7 |
| | Cancellation/process-group teardown; no leaked mounts | Yes | AV-3 |
| **workspaces** | O(1); CAS; squash; isolated-never-publishes | Yes | §1; HINGE; AV-9 |
| | Enter-gate on bg work; exit-drain | Later-phase | AV-9; Phase 3T Rust currently covers active-PTY exit blocking, not full Python foreground drain semantics |
| | `active_calls` guard + `ttl_sweep()` (idle>TTL AND active_calls==0) | Partial | Rust `InFlightRegistry::enter_call()` / `ttl_sweep()` close the daemon background-call registry guard; isolated workspace handle TTL/`active_calls` parity remains AV-9 later-phase work |
| | 9-var config contract + network constants + audit JSONL schema | Yes | Plan §3.5 SF-6 plus `workspaces.html`; Rust `eos-isolated` owns env caps, network constants, and JSONL audit sink |
| | File-API fast path (read: LayerStack; write: OCC direct) | Yes | CP-2a/CP-2b; AV-1 |
| **space-model** | O(1) lowerdir; O(n×changed) writable; N-agent scaling | Yes | §1; existing O(1) suites |
| | Isolated upperdirs bounded by ENV (only mode scaling linearly) | Yes (name it) | **SF-1** |
| | Manifest depth not free / auto-squash | Yes (verified/named) | CP-0 floor; Plan §3 SF-5; Rust daemon post-publish maintenance calls LayerStack auto-squash at `AUTO_SQUASH_MAX_DEPTH` |
| | Dual-layer lock; commit-queue params; layer_digest byte-identity | Yes | PV-1; PV-3; AV-1c(2) |
| **daemon** | In-flight tracking; cancellation; TTL reaper | Yes | Phase 3; AV-3 |
| | Heartbeat cadence (`EOS_BACKGROUND_HEARTBEAT_INTERVAL_S`) | Yes (reconciled) | Plan §3 SF-4 names the manager cadence; Rust registry documents the source-env reconciliation to `EOS_INFLIGHT_TTL_S` / `EOS_INFLIGHT_REAPER_INTERVAL_S` |
| | `api.layer_metrics` (storage bytes / active leases / manifest depth) | Yes | AV-1 SF-3 includes the endpoint; Phase 2 live proof exercised `api.layer_metrics` over AF_UNIX and TCP |
| | Isolated enter/exit; networking; readiness envelope; 97/98 | Partial | Phase 3T Rust control-plane/command-routing slice is closed; full AV-9 lifecycle parity remains later-phase |
| | OCC services cache RLock + LRU (256); audit drop-free | Yes | CP-5; AV-4 |
| | Request bounds (MAX_REQUEST_BYTES, read-timeout) | Yes | Phase 2 (behavior reproduced) |
| **provider** | ProviderAdapter contract preserved (host stays Python) | Yes | §2/§11 |
| | put_archive single-stream; minisign fail-closed; AF_UNIX 97/98 + cache invalidation | Yes | CP-1; AV-8; AV-2 |
| | Protocol version field; readiness envelope; kernel-variance degrade | Yes | §2; pre-mortem #2 |
| **plugins** | PPC out-of-process protocol replaces importlib | Yes | §0 PPC; AV-10 |
| | Warm per-session server (Pyright cold-start amortized) | Yes | §0; AV-10(iii); AV-3 |
| | 3 intent modes; bidirectional callback; isolated-mode-blocks-plugins | Yes | AV-10(i)(ii)(iv) |
| | Self-managed `apply.py` callback RPCs OCC back to eosd | **Partial — AV-10 open** | MF-1 gate text is applied and generic Rust callbacks route through daemon-owned OCC; representative Pyright/AV-10 parity and broader crash hardening remain skipped/open |
| | Plugin payloads optional via put_archive (Node-for-Pyright) | Yes | §0 scope (b); DoD §9 |

Scoped out with rationale: Daytona (Docker-only §0); `plugins/catalog/*` impls (scope option b); LSP Node-only fold (post-GA §10).

## 3. MUST-FIX

**MF-1 — Gate the PPC self-managed plugin OCC callback as a second OCC entry point through the same single-writer serialization.**

Status 2026-06-02: the required gate text has been applied to CP-4 and AV-10 in the plan, and the Rust daemon now routes generic connected self-managed OCC callbacks through the daemon OCC writer. Keep this as the plugin AV-10 closeout guard; it is no longer a non-plugin Phase 3T blocker.

- **Invariant at risk:** Manifest CAS = single linearization point; one `occ-commit-queue` writer per `layer_stack_root` (overview §1.1; occ §4.4; layerstack §2.4).
- **Why it's the real risk (source-verified):** Today the singleton commit-queue is owned by the per-`layer_stack_root` OCC services bundle (`daemon/occ_runtime_services.py:44-90`), and the self-managed plugin publishes through the same overlay's `publish_cycle()` (`ephemeral_workspace/plugin/overlay_dispatch.py:72`) keyed by the same `get_occ_runtime_services(layer_stack_root)` — so in Python the plugin callback shares the single writer **by construction**. The Rust PPC design has the self-managed plugin RPC OCC ops back to eosd over the bidirectional channel — a structurally **separate** entry point. If Rust routes that callback to anything other than the same `layer_stack_root`-keyed commit-queue + storage flock, single-linearization breaks **while byte-identical-results tests still pass**.
- **Where the original review found the gap:** CP-4 op-set (§3) excluded plugin ops; the §7 plugin differential was single-shot, not under contention; PV-3 named the single-writer for the primary path only. The live plan now includes the MF-1 plugin interleave text in CP-4/AV-10; representative AV-10 execution remains the skipped plugin closeout.
- **Falsifiable gate now applied in the live plan (retain through AV-10 closeout):**
  1. In §0 PPC / AV-10 (§4): state self-managed plugin OCC callbacks route through the **same** per-`layer_stack_root` single `occ-commit-queue` writer **and** the same `storage_lock` flock+RLock as the primary path — no second writer instance.
  2. Extend CP-4's op-set (§3) — or the §7 plugin differential — to include **concurrent self-managed plugin writes interleaved with primary-path publishes**, gated by final-workspace-state-hash parity (`manifest_root_hash` + per-layer `layer_digest`, AV-1c) under contention.

*(The Architect's isolated-resource-caps MUST-FIX was downgraded: primary-source verification found the caps ARE enforced + test-backstopped — `workspace_handle_lifecycle.py:58` `quota_exceeded` asserted by `test_isolated_pipeline_unified_lifecycle.py:176`; `pipeline.py:122` `host_ram_pressure` asserted by `:185-190`. Name-it, not build-it → SF-1.)*

## 4. SHOULD-FIX

Status 2026-06-02: SF-1/3/4/5/6 are folded into `sandbox-rust-external-migration-PLAN.md`; SF-4/SF-5/SF-6 have current Rust/doc evidence as noted above.

- **SF-1 — Closed for Rust admission caps:** AV-9 names isolated-workspace resource-cap parity against `_control_plane/types.py` env defaults; Rust `eos-isolated::ResourceCaps` now enforces `TOTAL_CAP` plus `host_ram_pressure`. Isolated handle TTL/phase-budget parity remains AV-9 later-phase scope.
- **SF-3 — Closed in plan/evidence:** AV-1 includes `api.layer_metrics`; Phase 2 live evidence exercised the endpoint over AF_UNIX and TCP.
- **SF-4 — Closed for daemon in-flight registry:** Plan §3 names the background lifecycle params and `ttl_sweep()` semantics. Rust `InFlightRegistry` documents the source-env reconciliation, increments `active_calls` before runtime, decrements via `ActiveCallGuard::drop`, and tests skip-then-reap behavior. This is not the isolated workspace handle TTL/foreground-drain parity gate; that remains under AV-9 later-phase work.
- **SF-5 — Closed in plan/code:** the squash split is named in Plan §3; Rust has `AutoSquashMaintenancePolicy` for the trigger and `LayerStack::squash()` under the storage-writer guard for the mechanics.
- **SF-6 — Closed in plan/docs/code:** Plan §3.5 and the architecture pages reference the isolated network/audit SoT; Rust `eos-isolated` owns the matching network constants and JSONL audit sink.

**Retired (no action):** near-linear throughput (design property, not SLO); shell-free IPv6 (already fixed+gated); `start=False` caching (impl detail); max-depth "constant" (it's a runtime param); host post-lifecycle (adapted, semantics preserved); READ_ONLY impl change (gated by AV-10); pre-mount maintenance (no arch budget); Node-only fold (post-GA).

## 5. What the Plan Already Covers Well

- **HINGE is real, not aspirational** — source-verified single touch site; relocating snapshot/lease to `eos-layerstack` makes `eos-isolated ⊥ eos-occ` a build-time guarantee.
- **Parametric-vs-CP-0 gating discipline** — CP-2a/2b/3/4/5 all expressed as ratios against a checked-in `bench/baseline-{arch}.json` (records kernel + userns/overlay config); no quoted absolutes.
- **CAS byte-identity carved narrowly + correctly** to the two correctness-bearing hashes (`manifest_root_hash` + `layer_digest`), distinct from on-disk serialization; AV-1c + AV-7 forward+backward rollback parity.
- **Dual-layer lock + commit-queue params reproduced exactly** (PV-1 flock+RLock; PV-3 0.002/64/3).
- **put_archive (CP-1), minisign fail-closed (AV-8), per-sandbox A/B (AV-5b), audit drop-free (AV-4), shell-free IPv6 (CP-1b).**
- **PND single-threaded holder** is kernel-forced; **PPC service process** is workload-forced — both mirror existing topology, not new abstractions.

**With MF-1's gate text added, every architecture performance and resource requirement is covered by a falsifiable gate, and all six captured invariants are gated rather than prose.**
