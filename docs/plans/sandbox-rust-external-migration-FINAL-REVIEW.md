# FINAL REVIEW REPORT — sandbox-rust-external-migration-PLAN.md

Reviews the fully-amended plan against **every** page of `docs/architecture/sandbox/` (details, invariants, performance + resource requirements). 13-agent consensus (10 per-page coverage + Architect + Critic + synthesis), primary-source-verified.

## 1. Verdict

**APPROVE WITH CONDITIONS.** The amended 11-crate structure is architecturally sound, the HINGE (`eos-isolated ⊥ eos-occ`) is source-verified to hold at exactly one severable touch site, and every architecture performance/resource requirement maps to a falsifiable gate — with **one genuinely under-gated captured invariant** (single-linearization-point applied to the PPC self-managed plugin OCC callback as a *second* entry point), which is a required gate-text change before execution, not a ship-as-is blocker.

## 2. Architecture Coverage Matrix

| Arch page | Key perf/resource requirement | Covered? | Plan anchor / gap |
|---|---|---|---|
| **overview** | O(1) snapshot (lease+layer_paths, never rendered tree) | Yes | §1; CP-0; `test_*_lowerdir_disk_is_o1*` |
| | Manifest CAS = single linearization point | Yes (1 hole) | AV-1c(1) + CP-4 final-state hash; **MF-1: 2nd OCC entry point (PPC self-managed callback) not gated** |
| | Capture+publish = 1 atomic unit / op | Yes | AV-1/AV-3 + §7 |
| | OCC batching (N disjoint = 1 CAS, near-linear) | Yes | PV-3 params (0.002/64/3) + CP-4. Near-linear is a design property, no numeric SLO to gate |
| | Isolated never publishes | Yes | Build-time `eos-isolated ⊥ eos-occ` (HINGE) + `test_full_cycle_never_calls_occ` + AV-9 |
| | Dual-layer lease (flock + refcounted RLock) | Yes | PV-1 |
| | Per-op fixed cost; signature fail-closed; put_archive | Yes | CP-2a/CP-2b; AV-8; CP-1 |
| | Isolated upperdir RAM/TTL/cap budgets | Yes (name it) | Enforced + test-backstopped; **SF-1: name cap parity in AV-9** |
| **layerstack** | O(1) snapshot; CAS; leased_layers vs lease_head_layers; deferred-GC | Yes | §1; AV-1c(1)(2); §7 property tests |
| | Manifest depth ceiling (~16-layer mount(8)) | Yes | CP-0 syscall floor; `max_depth` is a runtime param |
| | Squash acceptance/concurrency + single-worker guard | Yes (verify it) | PV-3 single-writer; CP-4 op-set incl squash/GC; **SF-5: verify-then-name squash second-writer guard** |
| | CP-0..CP-5 parametric gates; AV-1c; AV-7 rollback | Yes | §3; §4 |
| **overlay** | O(1) snapshot; CAS; capture+publish atomicity; mount-ns lifecycle | Yes | CP-0/AV-1/AV-1c; eos-overlay/eos-runner; Phase 1–3.5 |
| | Per-op fixed cost amortization (isolated) | Yes | CP-2a/CP-2b; Phase 3.5 |
| | Shell-free IPv6 hardening (rtnetlink + /proc/sys) | Yes | §3.5 + CP-1b read-only-rootfs + §7 |
| | Cancellation/process-group teardown; no leaked mounts | Yes | AV-3 |
| **workspaces** | O(1); CAS; squash; isolated-never-publishes | Yes | §1; HINGE; AV-9 |
| | Enter-gate on bg work; exit-drain | Yes | AV-9 |
| | `active_calls` guard + `ttl_sweep()` (idle>TTL AND active_calls==0) | Partial | **SF-4: name reproduce-exactly in Phase 3.5** |
| | 9-var config contract + network constants + audit JSONL schema | Partial | **SF-6: reference `_control_plane/types.py:165-183` + audit schema as SoT** |
| | File-API fast path (read: LayerStack; write: OCC direct) | Yes | CP-2a/CP-2b; AV-1 |
| **space-model** | O(1) lowerdir; O(n×changed) writable; N-agent scaling | Yes | §1; existing O(1) suites |
| | Isolated upperdirs bounded by ENV (only mode scaling linearly) | Yes (name it) | **SF-1** |
| | Manifest depth not free / auto-squash | Yes (verify it) | CP-0 floor; **SF-5** |
| | Dual-layer lock; commit-queue params; layer_digest byte-identity | Yes | PV-1; PV-3; AV-1c(2) |
| **daemon** | In-flight tracking; cancellation; TTL reaper | Yes | Phase 3; AV-3 |
| | Heartbeat cadence (`EOS_BACKGROUND_HEARTBEAT_INTERVAL_S`) | Partial | **SF-4: name the constant** |
| | `api.layer_metrics` (storage bytes / active leases / manifest depth) | Partial | **SF-3: add to AV-1 frozen-fixture set** |
| | Isolated enter/exit; networking; readiness envelope; 97/98 | Yes | Phase 2/3.5; AV-2/AV-9; §2 |
| | OCC services cache RLock + LRU (256); audit drop-free | Yes | CP-5; AV-4 |
| | Request bounds (MAX_REQUEST_BYTES, read-timeout) | Yes | Phase 2 (behavior reproduced) |
| **provider** | ProviderAdapter contract preserved (host stays Python) | Yes | §2/§11 |
| | put_archive single-stream; minisign fail-closed; AF_UNIX 97/98 + cache invalidation | Yes | CP-1; AV-8; AV-2 |
| | Protocol version field; readiness envelope; kernel-variance degrade | Yes | §2; pre-mortem #2 |
| **plugins** | PPC out-of-process protocol replaces importlib | Yes | §0 PPC; AV-10 |
| | Warm per-session server (Pyright cold-start amortized) | Yes | §0; AV-10(iii); AV-3 |
| | 3 intent modes; bidirectional callback; isolated-mode-blocks-plugins | Yes | AV-10(i)(ii)(iv) |
| | Self-managed `apply.py` callback RPCs OCC back to eosd | **Partial — under-gated** | results gated (AV-10 ii) but **MF-1: serialization through single per-`layer_stack_root` commit-queue + flock not gated; CP-4 op-set excludes plugin ops** |
| | Plugin payloads optional via put_archive (Node-for-Pyright) | Yes | §0 scope (b); DoD §9 |

Scoped out with rationale: Daytona (Docker-only §0); `plugins/catalog/*` impls (scope option b); LSP Node-only fold (post-GA §10).

## 3. MUST-FIX

**MF-1 — Gate the PPC self-managed plugin OCC callback as a second OCC entry point through the same single-writer serialization.**

- **Invariant at risk:** Manifest CAS = single linearization point; one `occ-commit-queue` writer per `layer_stack_root` (overview §1.1; occ §4.4; layerstack §2.4).
- **Why it's the real risk (source-verified):** Today the singleton commit-queue is owned by the per-`layer_stack_root` OCC services bundle (`daemon/occ_runtime_services.py:44-90`), and the self-managed plugin publishes through the same overlay's `publish_cycle()` (`ephemeral_workspace/plugin/overlay_dispatch.py:72`) keyed by the same `get_occ_runtime_services(layer_stack_root)` — so in Python the plugin callback shares the single writer **by construction**. The Rust PPC design has the self-managed plugin RPC OCC ops back to eosd over the bidirectional channel — a structurally **separate** entry point. If Rust routes that callback to anything other than the same `layer_stack_root`-keyed commit-queue + storage flock, single-linearization breaks **while byte-identical-results tests still pass**.
- **Where it falls through:** CP-4 op-set (§3) excludes plugin ops; the §7 plugin differential is single-shot, not under contention; PV-3 names the single-writer for the primary path only.
- **Falsifiable gate to add (required before execution):**
  1. In §0 PPC / AV-10 (§4): state self-managed plugin OCC callbacks route through the **same** per-`layer_stack_root` single `occ-commit-queue` writer **and** the same `storage_lock` flock+RLock as the primary path — no second writer instance.
  2. Extend CP-4's op-set (§3) — or the §7 plugin differential — to include **concurrent self-managed plugin writes interleaved with primary-path publishes**, gated by final-workspace-state-hash parity (`manifest_root_hash` + per-layer `layer_digest`, AV-1c) under contention.

*(The Architect's isolated-resource-caps MUST-FIX was downgraded: primary-source verification found the caps ARE enforced + test-backstopped — `workspace_handle_lifecycle.py:58` `quota_exceeded` asserted by `test_isolated_pipeline_unified_lifecycle.py:176`; `pipeline.py:122` `host_ram_pressure` asserted by `:185-190`. Name-it, not build-it → SF-1.)*

## 4. SHOULD-FIX

- **SF-1 — Name isolated-workspace resource-cap parity in AV-9 (§4).** Reference `_control_plane/types.py:165-183` `from_env()` defaults (`TTL_S=1800`, `TOTAL_CAP=5`, `UPPERDIR_BYTES=1GiB`, `MEMAVAIL_FRACTION=0.5`) as SoT for `TOTAL_CAP` quota + `host_ram_pressure`.
- **SF-3 — Add `api.layer_metrics` to the AV-1 frozen-fixture set (§4).** Real endpoint (daemon §6.4) exposing storage bytes / active leases / **manifest depth** — the observability surface for the depth invariant.
- **SF-4 — Name reproduce-exactly constants currently described by behavior:** `EOS_BACKGROUND_HEARTBEAT_INTERVAL_S` (daemon §6.8) and the `active_calls` guard + `ttl_sweep()` semantics (sweep only `idle>TTL AND active_calls==0`; increment before runtime, decrement in finally). Add to Phase 3/3.5 as PV-3-style params.
- **SF-5 — Verify-then-name the squash auto-trigger + second-writer guard.** *(Correction: the coverage agent claimed `AutoSquashMaintenancePolicy` was absent; primary-source re-check by the Planner found it DOES exist at `occ/maintenance.py:29` (+ `_LayerSquashPort` Protocol :21); `layer_stack/squash.py` holds `SquashPlan`/`LayerCheckpointSquasher`.)* During Phase 3, verify the squash worker's second-writer guard (must exist given the `storage_lock` single-owner lease — PV-1) and name it alongside PV-3. CP-4 op-set already lists squash/GC as a contention backstop → SHOULD not MUST.
- **SF-6 — Reference (lightest form, no inlining) the isolated network constants + audit schema as SoT:** `_control_plane/types.py` (`10.244.0.x/24`, `eos-shared0`, `accept_ra=0`, veth `eos-iws-<short>n`, `FALLBACK_DNS=1.1.1.1`) and the audit-event/JSONL schema (`EOS_ISOLATED_WORKSPACE_AUDIT_PATH`, `pipeline_registry.py:104`). Config-binding contracts, not architectural defects.

**Retired (no action):** near-linear throughput (design property, not SLO); shell-free IPv6 (already fixed+gated); `start=False` caching (impl detail); max-depth "constant" (it's a runtime param); host post-lifecycle (adapted, semantics preserved); READ_ONLY impl change (gated by AV-10); pre-mount maintenance (no arch budget); Node-only fold (post-GA).

## 5. What the Plan Already Covers Well

- **HINGE is real, not aspirational** — source-verified single touch site; relocating snapshot/lease to `eos-layerstack` makes `eos-isolated ⊥ eos-occ` a build-time guarantee.
- **Parametric-vs-CP-0 gating discipline** — CP-2a/2b/3/4/5 all expressed as ratios against a checked-in `bench/baseline-{arch}.json` (records kernel + userns/overlay config); no quoted absolutes.
- **CAS byte-identity carved narrowly + correctly** to the two correctness-bearing hashes (`manifest_root_hash` + `layer_digest`), distinct from on-disk serialization; AV-1c + AV-7 forward+backward rollback parity.
- **Dual-layer lock + commit-queue params reproduced exactly** (PV-1 flock+RLock; PV-3 0.002/64/3).
- **put_archive (CP-1), minisign fail-closed (AV-8), per-sandbox A/B (AV-5b), audit drop-free (AV-4), shell-free IPv6 (CP-1b).**
- **PND single-threaded holder** is kernel-forced; **PPC warm-server** is workload-forced — both mirror existing topology, not new abstractions.

**With MF-1's gate text added, every architecture performance and resource requirement is covered by a falsifiable gate, and all six captured invariants are gated rather than prose.**
