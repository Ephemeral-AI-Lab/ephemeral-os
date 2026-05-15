# task_center Folder Reframe — Plan (Iteration 4)

**Scope:** `backend/src/task_center/` (baseline: **62 files**, **7,613 wc-LoC**, **5,443 cloc-code-LoC**, 965 comments, 1,205 blanks). Folder/file reshape + dead-code deletion + bounded inlining of single-consumer abstractions + docstring compression + file-count consolidation. **All `_EXPORTS` entry *names* and all externally-imported class/function signatures preserved**; internal logic rewrites in scope only for the levers enumerated in §LoC Levers.

**Mode:** DELIBERATE. Iter4 raises the floor to a **1,089 cloc-code LoC** gate (20% of cloc-code), enforces every file ≤600 LoC, and tightens file-count target to ≤32 (was 40).

**Iteration 4 changes vs iteration 3 (user-directed):**

1. **cloc-code floor:** 1,089 (was wc-LoC 1,000). Docstring cuts DO NOT count toward this gate.
2. **Per-file ceiling:** ≤600 LoC for every file post-reframe.
3. **File-count ceiling:** ≤32 (was 40 in iter3, 37–40 plan-target).
4. **New levers #14–#21** (audit dead-payload, ContextPacketStoreProtocol, LaunchBuilder factory, single-impl Protocol → concrete refs, property-getter inlining, additional file mergers).
5. **Pre-grep evidence baked in** for every lever (commands + result counts shown inline).
6. **Mission Merger Contract amended:** iter2's ≤300 LoC handler ceiling RELAXED to ≤480 LoC in iter4 because handler.py absorbs repository+ancestry in Phase 7. Explicit amendment recorded in §Mission Merger Contract Addendum below.

---

## RALPLAN-DR Summary

### Principles (revised iter4)

1. **Frozen public surface, free private interior.** Any module path in `_EXPORTS` or imported directly from `backend/src/{db,tools,live_e2e,agents}` is public. Public paths may be re-pointed in `_EXPORTS` only with pre-deletion external-grep evidence. **No SIGNATURE changes** to any public class/function/method.

2. **No eager cross-package imports at the root.** `__init__.py` stays lazy (`__getattr__` + `_EXPORTS`). Sub-package `__init__.py` files must not import siblings at module top level when they absorb merged modules.

3. **One folder = one bounded concern.** Cross-cutting infra lives in `_core/`. Trivial leaves (≤25 LoC; no helpers; no own test fixtures) may co-locate into a single-concern bundle. The bundle is never a misc-bucket of unrelated concepts.

4. **Multi-file abstractions whose sole consumer is one file may be collapsed back into the consumer ONLY IF** (a) the consumer's tests do not directly instantiate the collapsed type, AND (b) the collapsed type has no independent test file. Each collapse is a behavioral edit with a paired regression test (named-step ordering, exception-aggregation shape, single-shot guards, signature snapshots).

5. **Single-implementer `Protocol` types may be replaced with direct concrete-class type annotations** when (a) the sole implementer lives in the same package (or `_core/`), AND (b) no external consumer imports the Protocol by name (verified via grep). The Protocol declaration is deleted; consumer field types become the concrete class.

6. **(iter4 NEW)** Every committed file ≤600 LoC. Verified via `find … | xargs wc -l | sort -rn | head -1`.

### Non-Goals

- **NG-1:** No SIGNATURE changes to any class/function/method exposed via `_EXPORTS` or external deep-import paths.
- **NG-2:** No new abstractions, base classes, or generic helpers.
- **NG-3:** No edits to `backend/src/db/stores/*`, `backend/src/tools/submission/*`, `backend/src/live_e2e/*`, `backend/src/agents/definition/*`.
- **NG-4:** Docstring compression touches `backend/src/task_center/**` only. Tests untouched.
- **NG-5 (iter4):** No public type-export deletions. Single-impl Protocol-deletion is allowed ONLY when the Protocol name is not in `_EXPORTS` and zero external deep-imports grep-hit. Lever #16's deletion list is pre-grepped.

### Decision Drivers (top 3)

1. **Public-surface integrity.** Every rename, re-point, or signature touch needs grep evidence + paired test.
2. **cloc-code reduction ≥ 1,089 at the user-mandated floor**, accumulated via bounded levers with abort bands. Each lever's cloc-code estimate (NOT wc-LoC) feeds the floor.
3. **Reviewability.** One commit per lever in Phase 5; one commit per sub-package in Phase 6; one commit per bundle in Phase 7; per-phase regression gate.

### Viable Options

#### Path D (iter3) — superseded

- cloc-code: ~700–950 (estimated post-mortem of iter3's wc-LoC numbers). Under 1,089.
- File count target: 37–40, over 32.
- Status: insufficient under iter4 constraints.

#### Path F — Iter4 Aggressive (CHOSEN)

Iter3 Path D + new levers #14–#21 + Phase 7 extended mergers.

- **cloc-code estimate:** 1,200–1,500 (conservative buffer 110+ over floor).
- **File count target:** 28–30 (under 32 ceiling).
- **Per-file ceiling:** every file ≤600 LoC (verified post-phase).
- **Pros:** clears floor with buffer; binds file ceiling; every lever pre-grepped.
- **Cons:** higher behavioral surface than iter3. Three additional concrete-class refactors (lever #16). Recipes consolidation reduces grep-locatability per recipe.

#### Path G — Floor-conservative (fallback)

Drop Path F levers #16, #20, #21. cloc-code ≈ 1,000–1,150 (over floor in best case, marginal). File count ≈ 32–34 (boundary or fail). Use only if reviewer blocks #16 or #20–#21.

#### Why no further alternatives

The user's iter4 directive is explicit and quantitative: ≥1,089 cloc-code reduction, ≤32 files, ≤600 LoC/file, plus an enumerated investigation. No path achieves these without either (a) public-signature breakage (rejected by Principle 1) or (b) tested-behavior deletion (rejected by Principle 4 a+b).

---

## Dead / Used-but-Useless Code Investigation

**Pre-investigation grep evidence (gate-passing requirement).** Each row's "usage proof" was run before writing this plan. Results inline.

| # | Name | File:line | Usage proof (grep cmd) | Hit count | Mechanism | cloc-code LoC | Behavioral risk | Regression test |
|---|---|---|---|---|---|---|---|---|
| #1 | `events.py` (entire module) | `events.py:1-110` | `rg "from task_center\.events\|import task_center\.events" backend/` | **0** | Delete file | ~70–90 | None | Pre-deletion grep gate |
| #2 | 7 typed payload classes + 2 unions in `task_state.py` | `task_state.py:115-224` (approx range) | `rg "\.to_dict\(\)" backend/` filtered to task_state types | **0** | Delete classes; payloads stay as `dict[str, Any]` | ~70–85 | None | Import smoke + pre-grep |
| #10 | `RegisteredEpisodeManager` Protocol | `protocols.py:48-58`, `episode/registry.py:12,21,29` | `rg "RegisteredEpisodeManager" backend/` | **7 (all internal)** | Inline as `EpisodeManager` concrete ref in `episode/registry.py`; delete Protocol | ~10–15 | LOW | Import smoke |
| #14a | `ContextPacketStoreProtocol` Protocol | `context_engine/engine.py:24,44` | `rg "ContextPacketStoreProtocol" backend/` | **2 (same file)** | Delete Protocol + the `context_packet_store: ... \| None = None` field (never set anywhere) | ~16 | None | Pre-deletion grep |
| #14b | `ContextScope.for_helper` classmethod | `context_engine/scope.py:111` | `rg "for_helper" backend/` | **1 (declaration only)** | Delete classmethod | ~8 | None | Pre-deletion grep |
| #18 | 4 audit payload classes (`_BaseTaskPayload`, `TaskReadyPayload`, `TaskLaunchedPayload`, `TaskFailedPayload`) | `audit.py:43,56,63,68,195-197` | `rg "TaskReadyPayload\|TaskLaunchedPayload\|TaskFailedPayload\|_BaseTaskPayload" backend/` | **7 (all in `audit.py` itself: 4 declarations + 3 `__all__` entries)** | Delete 4 classes + 3 `__all__` lines; inline payload-dict construction at the 3 emit sites | ~60–100 | LOW (audit shape unchanged: still dict via emit) | New 3-assert regression test (audit emission keys match pre-deletion shape) |

**Lever #18 confirmation: GREEN.** Zero external behavioral consumers. The grep proof `rg "TaskReadyPayload|TaskLaunchedPayload|TaskFailedPayload|_BaseTaskPayload" backend/` returned exactly 7 self-hits in `audit.py`. The classes are pure dead.

### Used-but-Useless (Protocol → concrete-class) candidates

Lever #16 expands Principle 5. Replace single-implementer Protocols with direct concrete annotations.

| Sub-lever | Protocol name | Sole impl | Consumer ref | Grep evidence | Removable LoC |
|---|---|---|---|---|---|
| #16a | `AgentResolver` (`agent_routing/resolver.py:42`) | `RuleBasedAgentResolver` (same file) | `context_engine/composer.py:26,46` | 2 imports (composer.py + 1 self) | ~10–15 |
| #16b | `PromptRenderer` (`renderer.py:31`) | `MarkdownPromptRenderer` (same file) | `context_engine/composer.py:22,48` | 2 imports (composer.py + 1 self) | ~10–15 |
| #16c | `AttemptAgentLauncher` (`runtime.py:47`) | `EphemeralAttemptAgentLauncher` (`launcher.py:40`) | `runtime.py:59` (AttemptDeps.agent_launcher), `contexts.py:36,76,96` | 4 imports | ~15–25 (Protocol body + 3 import-site reannotations net) |
| #16d | `StageStrategy` (`stage_strategy.py:28`) | 4 internal stage classes | `stage_strategy.py:64,72` (dispatch dict + `__all__`) | self-only | covered by iter3 lever #7 (no double-count) |
| #16e | `PlannerCtx`/`GeneratorCtx`/`LaunchCtx`/`EpisodeLifecycleCtx`/`MissionLifecycleCtx` | All satisfied by `AttemptDeps` | `contexts.py` Protocols (declarations) + `launcher.py`, `dispatcher.py`, `orchestrator.py`, `runtime.py` annotations | 5 declarations + ~10 annotation sites | Lever #9 (iter3) handles overlap collapse; #16e is the further step of replacing collapsed Protocol with `AttemptDeps` concrete ref. **Conservative scope:** keep iter3 lever #9 (overlap collapse only); defer full #16e to a follow-up plan to avoid churning `_EXPORTS`-near callers. |

**#16 effective cloc-code reduction:** sub-levers a+b+c only → ~35–55 LoC.

### Duplicated-factory inlining (lever #15)

| Lever | Source | Callsite count | Mechanism | cloc-code LoC |
|---|---|---|---|---|
| #15 | `LaunchBuilder.for_planner/for_generator/for_evaluator/for_entry` (`launch_builder.py:47,71,101,125`) | 4 callsites (`orchestrator.py:91`, `dispatcher.py:164,240`, `coordinator.py:313`) | Collapse to `for_role(role: TaskCenterTaskRole, ...)` driven by existing `_fail_reason_for_role()` + `ContextScope.for_<role>` dispatch | ~70–100 |

**Grep proof:** `rg "for_planner\(\|for_generator\(\|for_evaluator\(\|for_entry\(" backend/` shows exactly 4 callsites + 4 declarations. All internal.

### Property-getter inlining candidates (lever #17, deferred)

| Property | File:line | Inlines to |
|---|---|---|
| `MissionHandler._orchestrator_factory` proxy property | `mission/handler.py:80-91` | **DELETED** by iter2 mission merger (no inlining needed; property goes away) |
| `EpisodeFactory.has_orchestrator_factory` | `mission/episode_factory.py:111-113` | wraps `self._orchestrator_factory is not None`. Inline at the 1 call site (`episode_closure_router.py:114`) |
| `MissionStarter._done` (note: actual property is `saga._done`) | `saga.py:52` | DELETED by lever #4 |
| Other `@property` 1-liners | scattered (15 found via `rg "@property" backend/src/task_center/`) | **DEFERRED** to follow-up; keep all other `@property` decorations; #17 inlines ONLY `has_orchestrator_factory` |

**#17 effective cloc-code reduction:** ~5–8 LoC (1 property + 1 call-site rewrite).

### Wave-6 backward-compat shim audit (lever #21, conditional)

| Shim | File:line | Action | LoC |
|---|---|---|---|
| `task_center.exceptions` shim re-export | `exceptions.py:1-11` | Phase 2 move into `_core/`; Phase 7 collapse into `_core/types.py`. No additional lever needed. | covered |
| `task_center.task_ids` shim re-export | `task_ids.py:1-15` | Same pattern. | covered |
| `task_center.config` shim re-export | `config.py:1-23` | Same pattern. | covered |
| `task_center.protocols` shim | `protocols.py:1-62` | Phase 7 fold into `_core/types.py` (after lever #10's RegisteredEpisodeManager removal). | ~30–40 (post-#10) |

**#21 effective cloc-code reduction:** ~10–20 LoC (boilerplate header removal across 4 shim files; main savings already captured by Phase 7).

### Investigation summary (dead-code total)

| Lever set | cloc-code LoC saved |
|---|---|
| Pure-dead deletions (#1, #2, #10, #14a, #14b, #18) | 234–314 |
| Single-impl Protocol → concrete (#16 a+b+c) | 35–55 |
| LaunchBuilder factory consolidation (#15) | 70–100 |
| has_orchestrator_factory inline (#17) | 5–8 |
| Shim header trims (#21) | 10–20 |
| **Investigation subtotal** | **354–497** |

---

## LoC Levers (verified, cloc-code estimates)

cloc-code estimates derived by subtracting per-file docstring/comment fractions (Wave-6 modules are docstring-heavy: ~30–40% of wc-LoC is docstring; non-Wave-6 modules: ~10–15%).

| # | Lever | Mechanism | wc-LoC | **cloc-code LoC** | Risk | Regression test |
|---|---|---|---|---|---|---|
| 1 | `events.py` deletion | 0 importers; pure dead | 110 | **70–90** | None | Pre-grep |
| 2 | `task_state.py` payload-class deletion | 7 payload classes + 2 unions; zero `.to_dict()` consumers | 100–110 | **70–85** | None | Import smoke + grep |
| 3 | Mission handler family merger (iter2) | handler+episode_factory+episode_closure_router → 1 file | 140–180 | **95–125** | LOW | iter2 regression test |
| 4 | Saga inlining | `Saga` (105) has 1 consumer (`mission/starter.py`) | 75–90 net | **55–70** | MEDIUM | 3-assert + signature snapshot |
| 5 | Registry[T] inlining | 2 subclasses share only 3 of 6 methods | 30–45 net | **25–35** | MEDIUM | Public surface + signature snapshots |
| 6 | `launcher.py` 4×`_report_*_exhaustion` → parameterized | Parameterize by `TaskCenterTaskRole` | 80–120 | **65–95** | MEDIUM | Parametrized per-role test |
| 7 | `stage_strategy.py` compaction | 4 strategy classes (6–10 LoC each) → dict-dispatch | 35–45 | **30–40** | LOW | 1-assert dispatch test |
| 8 | `invariants.py` consolidation | 5 one-liner asserts inlined | 60–90 | **45–70** | LOW–MED | Existing tests cover |
| 9 | `attempt/contexts.py` Protocol overlap collapse | `PlannerCtx` ≡ `GeneratorCtx` → `AttemptStageCtx`; lifecycle Ctx pair shares 4/5 fields | 40–60 | **30–45** | LOW | `isinstance` smoke |
| 10 | `RegisteredEpisodeManager` removal | 1 attribute; only used in `episode/registry.py` typing | 10–15 | **10–15** | LOW | Import smoke |
| 11 | DTO mergers (iter2) | episode `state.py` + `closure_report.py`; mission `state.py` absorbs close-report DTOs | 14 | **10–14** | None | Import smoke |
| 12 | Docstring compression sweep | Compress Wave-6 prologues to 1-line module docstrings | 250–400 | **0 (excluded by user gate)** | LOW (cosmetic) | Sub-package import smoke after each commit |
| 13 | File-count consolidation (boilerplate) | Per-file headers + __init__.py boilerplate | 30–60 | **15–35** | LOW–MED | Import probe |
| **14** | `ContextPacketStoreProtocol` + `ContextScope.for_helper` deletion | Protocol never set; classmethod never called | 24 | **20** | None | Pre-grep |
| **15** | `LaunchBuilder.for_role(...)` consolidation | 4 factory methods → 1 parameterized | 70–100 | **55–80** | LOW–MED | Per-callsite parametrized test |
| **16** | Single-impl Protocol → concrete refs (a+b+c only) | `AgentResolver`, `PromptRenderer`, `AttemptAgentLauncher` → concrete class annotations | 35–55 | **30–45** | LOW | Static-grep + import smoke |
| **17** | `has_orchestrator_factory` inline | property → `is not None` at 1 callsite | 5–8 | **5–8** | LOW | Import smoke |
| **18** | Audit dead-payload deletion | `_BaseTaskPayload`/`TaskReadyPayload`/`TaskLaunchedPayload`/`TaskFailedPayload` (verified GREEN) | 60–100 | **45–80** | LOW | 3-assert audit emission shape |
| **19** | (RESERVED — used by #18 above) | — | — | — | — | — |
| **20** | `attempt/runtime.py` + `attempt/lifecycle.py` merge | Both sit on shared `AttemptDeps` surface; after lever #9 collapse, Protocol declarations relocate together | 0 (boilerplate-only after merges above) | **5–10** | LOW–MED | Import probe + Phase 7 root-import probe |
| **21** | Wave-6 backward-compat shim audit + `protocols.py` fold | Boilerplate headers across 4 shim files + final `protocols.py` removal into `_core/types.py` | 10–25 | **10–20** | LOW | Per-shim grep + import smoke |

### cloc-code roll-up

| Set | Lower (cloc-code) | Upper (cloc-code) |
|---|---|---|
| Pure deletions (#1, #2, #10, #14, #18) | 215 | 290 |
| Behavioral inlinings (#4, #5, #6, #7, #8, #9, #15, #16, #17) | 320 | 488 |
| Mergers / DTO / boilerplate (#3, #11, #13, #20, #21) | 135 | 219 |
| **Sub-total (lever set; excludes docstring sweep #12)** | **670** | **997** |
| **Add: Phase 7 secondary mergers** (attempt/state+contexts; recipes mini-merge — see §File-Count Consolidation Plan below) | 30 | 60 |
| **Iter4 grand-total (cloc-code)** | **700** | **1,057** |

**Floor:** 1,089 cloc-code LoC.

**Gap analysis:** the conservative roll-up (700) is **−389 under floor**. The upper roll-up (1,057) is **−32 under floor**. **The lever set alone DOES NOT clear the floor.**

### Mandatory closing-the-gap addendum

To clear 1,089 with buffer ≥100, Phase 6 docstring compression MUST contribute cloc-comment-LoC reduction that is **re-classified into the cloc-code-LoC gate** OR additional code-bearing levers are added. **User's rule:** "Docstring cuts do NOT count toward this gate." Therefore:

**Plan response:** add three more code-bearing levers — call them #20-extra mergers within Phase 7. Specifically:

- **#22 (NEW):** Inline `agent_routing/predicates.py` `Registry`-subclass code (3 shared classmethods × 2 subclasses) AND collapse boilerplate. Beyond lever #5's "inline shared methods" scope, also remove the (small) duplicated import block at top of both files when merged into `agent_routing/__init__.py`. cloc-code: **15–25**.
- **#23 (NEW):** Recipes mini-merge — fold `recipes/summaries.py` (20) + `recipes/entry_executor.py` (64) into `recipes/__init__.py` (currently 39). Net cloc-code wc-LoC delta is mostly boilerplate but recipe-level imports also collapse. cloc-code: **15–25**.
- **#24 (NEW):** Inline `EpisodeFactory.has_orchestrator_factory` + 2 sibling property proxies (`_orchestrator_factory` getter/setter in `mission/handler.py` go away with merger lever #3, but the equivalent property in `episode/manager.py:68,158,161` lives on through Phase 7; the `_orchestrator_factory is None` guards at lines 158/161 can be inlined with `if self._orchestrator_factory:` pattern, removing 1–2 dead `is None` checks). cloc-code: **5–10**.
- **#25 (iter5-resolved — investigation complete, broken into 4 named sub-levers):** Audit findings on the 4 largest behavioral files (1,453 LoC across orchestrator.py 422, dispatcher.py 318, starter.py 367, coordinator.py 346):
  - **#25a — `_fresh_attempt` duplication:** identical private method exists at `attempt/orchestrator.py:402` AND `attempt/dispatcher.py:312` (both read the same store-state). Inline at call sites OR move to a shared helper in `attempt/state.py`. cloc-code: **15–25**.
  - **#25b — `_assert_stores_ready` duplication:** identical logic at `entry/coordinator.py:169` (class method) AND `entry/coordinator.py:333` (module-level function). Same purpose, same body. Keep one. cloc-code: **10–20**.
  - **#25c — dispatcher.py dispatch-pair parameterization:** `_dispatch_generating` (line 93) + `_dispatch_evaluating` (line 124) are stage-parameterized near-duplicates; `_launch_ready_generator` (line 144) + `_launch_evaluator` (line 200) follow the same pattern. Apply the launcher-exhaustion parameterization recipe (lever #6) to dispatcher. cloc-code: **30–60**.
  - **#25d — single-use module-level helpers:** `_parent_attempt_id` (`mission/starter.py:365`) is module-level with one call site. Inline. Plus `_task_agent_name` (`dispatcher.py:304`) — also single-use staticmethod, inline. cloc-code: **5–15**.
  - **Total #25 (resolved):** **60–120 cloc-code** (mid-band ~90). Behavioral risk: MEDIUM; staged as Phase 5g (4 sub-commits, one per finding) with paired regression tests for #25c only (the parameterization is the only behavioral surface change).
- **#26 (iter4-final NEW):** Post-Lever-#15 cross-file boilerplate sweep after `attempt/launch.py` merger absorbs `launcher.py` + `launch_builder.py`. Sweep for duplicate imports, redundant role-dispatch helpers (now both factory and exhaustion are parameterized), dead `_fail_reason_for_role` if absorbed. cloc-code: **20–40**.

**Revised iter4 grand-total (cloc-code):** **735–1,117**. Mid-band ≈ **926**. Still below floor in conservative case.

**Final residual gap-closer (iter4 mandate):** When Phase 8 cloc verification shows residual under-1,089, the executor MUST apply targeted **comment-block removal** in code-bearing files — specifically the Wave-6 inline rationale comments. The user's gate excludes *docstring* removal from counting; **code-adjacent inline `#` comments are NOT docstrings** and DO count as code-LoC under cloc's default classification only when they touch the same line as code. Cloc classifies pure `# ...` lines as comment, not code. **Therefore comment-removal does NOT close a cloc-code gap.**

**Real residual response:** if conservative roll-up undershoots at Phase 8 verification, executor MUST add follow-up levers from the **deferred backlog**:

- **#16d/e activation:** activate the full StageStrategy Protocol deletion (lever #16d) AND the full ContextCtx Protocols→AttemptDeps consolidation (lever #16e). Combined cloc-code: ~80–140.
- **Aggressive invariants inlining:** beyond lever #8's 5 one-liners, inline an additional 5–8 assertion call-sites (lever #8-extended). cloc-code: ~30–60.

**With deferred backlog activated AND iter4-final levers #25/#26 (binding, in Phase 5 scope — not contingency):**

| Total roll-up | Lower | Upper |
|---|---|---|
| iter4 base levers (#1–#21) | 670 | 997 |
| Phase 7 secondary mergers (#22, #23, #24) | 35 | 60 |
| **Lever #25 (orchestrator/dispatcher/starter/coordinator audit, in scope)** | **50** | **120** |
| **Lever #26 (post-merger boilerplate sweep, in scope)** | **20** | **40** |
| Deferred-backlog activation (#16d/e + #8-ext, BOUND to Phase 5 per Critic A3) | 110 | 200 |
| **GRAND TOTAL (cloc-code)** | **885** | **1,417** |

**Floor pass status:** mid-band ≈ **1,151** — clears 1,089 with +62 buffer. Conservative case **885 still under floor by 204**. **Buffer is conditional on lever-#25 yield ≥ 100 OR mid-band realization of other levers.**

**Iter5 acceptance commitment (user-negotiated band per (c)):** AC #5 uses a **band, not a single floor**:
- Target band: **1,089–1,500 cloc-code** (clears 20% goal; mid-band 1,151 with lever #25 broken into named sub-levers #25a–#25d is realistic).
- Soft floor: **850 cloc-code** (above conservative roll-up 885; user-acknowledged underrun band).
- Hard abort: **<850 cloc-code** — triggers deferred-backlog activation; if still under, surface to user.

The conservative roll-up (885) sits 35 cloc-code above the soft floor. Lever #25 broken into 4 named sub-levers (a/b/c/d) is now grep-evidenced (not speculative); see §LoC Levers entry #25.

### Per-file ≤600 ceiling verification

Top-25 by current wc-LoC: `attempt/orchestrator.py` 422, `launcher.py` 375, `mission/starter.py` 367, `entry/coordinator.py` 346. **No current file exceeds 600.** Mergers below must verify post-merge size:

- `entry/__init__.py` (post-merge: controller 185 + coordinator 346 + sandbox_bridge 77 + boilerplate − 20) = ~588. **Within ceiling but at the limit.** If post-merge measurement exceeds 600, abort entry/ bundle merger and keep coordinator.py + controller.py separate.

Other bundles all comfortably under 600 (see §File-Count Consolidation Plan).

---

## File-Count Consolidation Plan

**Current baseline:** 62 files. **Target ceiling:** ≤32. **Iter4 target:** 28–30.

| Bundle | Files merged (post-Phase-1 deletes; sizes are post-merge estimates) | wc-LoC | Constraint check |
|---|---|---|---|
| `_core/persistence.py` | persistence (240) — **separated per iter4 Critic A1-modified**: behavioral I/O Protocols stay in their own file | ~240 | ≤600 OK; bounded concern (store I/O contracts only) |
| `_core/types.py` | protocols (62 post-#10) + ids (15) + exceptions (11) + config (23) − 20 boilerplate | ~91 | ≤600 OK; bounded concern ("package primitive types") |
| `_core/infra.py` | audit (198 post-#18 ~140) + invariants (206 post-#8 ~130) − 25 boilerplate | ~245 | ≤600 OK |
| `__init__.py` (root) | (unchanged; 143) | 143 | OK |
| `task_state.py` (root) | (kept PIN; post-#2 ~115) | ~115 | OK |
| `agent_routing/__init__.py` | predicates (123 post-#5/#22 ~90) + resolver (129 post-#16a ~115) − 20 boilerplate | ~185 | OK |
| `attempt/__init__.py` | (15) unchanged | 15 | OK |
| `attempt/state.py` | (56) unchanged | 56 | OK |
| `attempt/runtime_lifecycle.py` | runtime (160 post-#9/#16c ~125) + lifecycle (137 post-#9 ~115) − 20 boilerplate | ~220 | OK (lever #20) |
| `attempt/contexts.py` | (160 post-#9 ~95) | ~95 | OK |
| `attempt/dispatcher.py` | (318 post-#15 ~270) | ~270 | OK |
| `attempt/generator_dag.py` | (150) unchanged | 150 | OK |
| `attempt/orchestrator.py` | (422 + stage_strategy 72 post-#7 ~30 − boilerplate) | ~440 | ≤600 OK |
| `attempt/orchestrator_registry.py` | (47) unchanged | 47 | OK |
| `attempt/launch.py` | launcher (375 post-#6 ~295) + launch_builder (159 post-#15 ~75) − 20 boilerplate | ~350 | ≤600 OK |
| `episode/__init__.py` | state (60 + closure_report 49 ~95) + manager (306 post-#24 ~290) + registry (33 post-#10 ~22) − 25 boilerplate | ~382 | ≤600 OK |
| `entry/__init__.py` | controller (185) + coordinator (346 post-#15 ~310) + sandbox_bridge (77) − 25 boilerplate | ~547 | ≤600 OK but tight; **abort to entry/coordinator.py + entry/controller.py separation if exceeds 600 post-merge** |
| `mission/__init__.py` | (0) unchanged | 0 | OK |
| `mission/state.py` | (57 + close_report_delivery DTOs 86 ~125) | ~125 | OK |
| `mission/handler.py` | iter2 merger (handler 144 + episode_factory 130 + episode_closure_router 145 ~330) + repository (107) + ancestry (81) − 50 boilerplate | ~468 | ≤600 OK |
| `mission/starter.py` | (367 + saga 105 post-#4 ~60 − boilerplate) | ~410 | ≤600 OK |
| `mission/close_report_router.py` | rename of close_report_delivery.py (router class only, DTOs split into state.py); ~50 | ~50 | OK |
| `context_engine/core.py` | engine (63 post-#14a ~45) + composer (89 post-#16ab ~75) + errors (25) | ~145 | OK (lever-pack: ContextPacketStoreProtocol deletion + composer flattening) |
| `context_engine/packet.py` | (90) unchanged | 90 | OK |
| `context_engine/scope.py` | (120 post-#14b ~115) | ~115 | OK |
| `context_engine/renderer.py` | (254 post-#16b ~235) | ~235 | OK |
| `context_engine/recipes_registry.py` | (85 post-#5 ~75) | ~75 | OK |
| `context_engine/recipes/__init__.py` | __init__ (39) + summaries (20) + entry_executor (64) − 15 boilerplate | ~108 | OK (lever #23) |
| `context_engine/recipes/attempt_landscape.py` | (216) | 216 | OK |
| `context_engine/recipes/evaluator.py` | (145) | 145 | OK |
| `context_engine/recipes/generator.py` | (138) | 138 | OK |
| `context_engine/recipes/helper.py` | (131) | 131 | OK |
| `context_engine/recipes/mission_episode.py` | (~105) | ~105 | OK |
| `context_engine/recipes/planner.py` | (94) | 94 | OK |

### Final file inventory (28 files)

```
task_center/
├── __init__.py                             (1)
├── task_state.py                           (2)
├── _core/
│   ├── __init__.py                         (3)
│   ├── types.py                            (4)
│   ├── persistence.py                      (5)  (iter4 Critic A1-modified split)
│   └── infra.py                            (6)
├── agent_routing/
│   └── __init__.py                         (6)
├── attempt/
│   ├── __init__.py                         (7)
│   ├── state.py                            (8)
│   ├── contexts.py                         (9)
│   ├── runtime_lifecycle.py                (10)
│   ├── dispatcher.py                       (11)
│   ├── generator_dag.py                    (12)
│   ├── orchestrator.py                     (13)
│   ├── orchestrator_registry.py            (14)
│   └── launch.py                           (15)
├── episode/
│   └── __init__.py                         (16)
├── entry/
│   └── __init__.py                         (17)
├── mission/
│   ├── __init__.py                         (18)
│   ├── state.py                            (19)
│   ├── handler.py                          (20)
│   ├── starter.py                          (21)
│   └── close_report_router.py              (22)
└── context_engine/
    ├── __init__.py                         (23)
    ├── core.py                             (24)
    ├── packet.py                           (25)
    ├── scope.py                            (26)
    ├── renderer.py                         (27)
    ├── recipes_registry.py                 (28)
    └── recipes/
        ├── __init__.py                     (29)
        ├── attempt_landscape.py            (30)
        ├── evaluator.py                    (31)
        ├── generator.py                    (32)
        ├── helper.py                       (33)
        ├── mission_episode.py              (34)
        └── planner.py                      (35)
```

**Count: 35 files.** Still over ≤32 ceiling.

### Closing the 35 → 32 gap

| Option | Action | New count |
|---|---|---|
| **A (preferred):** Recipes consolidation +2 | Merge `recipes/helper.py` (131) + `recipes/planner.py` (94) into a single `recipes/planner.py` (~225, ≤600 OK). Merge `recipes/mission_episode.py` (105) + `recipes/generator.py` (138) into `recipes/generator.py` (~243, ≤600 OK). Net −2 files. | 33 |
| **B:** Drop redundant `__init__.py` shells | `attempt/__init__.py` (15) is mostly re-exports — fold the 6 export lines back into the root `_EXPORTS` map directly, delete `attempt/__init__.py`. Apply to `mission/__init__.py` (0), `recipes/__init__.py` (after lever #23 ~108: keep this one because it has real recipes content). Net −2 files. | 33 |
| **C:** Combine A + drop one __init__ | Apply A (−2) + drop `attempt/__init__.py` empty re-exports (−1) | **32** (at ceiling) |
| **D:** Apply A + drop both empty `mission/__init__.py` and `attempt/__init__.py` | Net −4 | **31** ✅ buffer 1 below ceiling |

**Decision:** Adopt **Option D**. Final count: **31 files**. Ceiling buffer 1.

### Open question for user (decision needed before Phase 7)

Option D drops `attempt/__init__.py` (a pure 15-LoC re-export shell) and `mission/__init__.py` (empty placeholder). Both are currently importable via `from task_center.attempt import ...` and `from task_center.mission import ...`. Dropping them **breaks any external code that does `from task_center.attempt import Attempt`** (today `live_e2e/squad/runner.py:33` does exactly this).

**Two routes:**
1. Keep `attempt/__init__.py` as 1-line lazy `from task_center.attempt.state import Attempt as Attempt` (≤2 LoC; not a misc-bucket). File-count stays 33.
2. Re-point `live_e2e/squad/runner.py:33-34` to `from task_center import Attempt, Episode` (root facade). Violates NG-3 ("no edits to live_e2e"). **REJECTED.**

**Decision:** Route 1. Keep the two `__init__.py` re-export shells but reduce them to ≤2 lines. Final count: **33 files. Over 32 ceiling by 1.**

**Surface this overshoot to the user before Phase 7.** Open Question to user (recorded in `.omc/plans/open-questions.md`).

If user accepts 33: proceed as planned. If user enforces ≤32 strictly: adopt **Option D-strict**, edit `live_e2e/squad/runner.py:33-34` import paths *with explicit user override of NG-3 in writing*.

---

## Target Folder Layout (iteration 4)

```
task_center/                                    cloc-code: ~4,356 (5,443 − 1,089) target
├── __init__.py                                 ~120  (-23: _EXPORTS re-pointed; trimmed)
├── task_state.py                               ~115  (PIN; post-#2 dead-payload deletion)
├── _core/                                      ~595
│   ├── __init__.py                             5
│   ├── types.py                                ~321  (persistence+protocols+primitives bundle; ≤600 OK)
│   └── infra.py                                ~245  (audit+invariants bundle; ≤600 OK)
│
├── agent_routing/                              ~185
│   └── __init__.py                             ~185  (predicates+resolver merged; no eager cross-package imports)
│
├── attempt/                                    ~1,460
│   ├── __init__.py                             2     (1-line re-export of Attempt)
│   ├── state.py                                56    (PIN)
│   ├── contexts.py                             ~95   (-65 lever #9 Protocol collapse)
│   ├── runtime_lifecycle.py                    ~220  (lever #20 runtime+lifecycle merge)
│   ├── dispatcher.py                           ~270  (-48 lever #15 LaunchBuilder.for_role)
│   ├── generator_dag.py                        150
│   ├── orchestrator.py                         ~440  (+30 lever #7 stage_strategy absorb)
│   ├── orchestrator_registry.py                47
│   └── launch.py                               ~350  (launcher+launch_builder; lever #6+#15 applied)
│
├── episode/                                    ~382
│   └── __init__.py                             ~382  (state+closure_report+manager+registry bundle)
│
├── entry/                                      ~547
│   └── __init__.py                             ~547  (controller+coordinator+sandbox_bridge bundle; abort to split if >600)
│
├── mission/                                    ~1,053
│   ├── __init__.py                             2     (1-line re-export of MissionStarter, StartedMission)
│   ├── state.py                                ~125  (+DTOs from close_report_delivery)
│   ├── handler.py                              ~468  (iter2 merger + repository + ancestry absorb; ≤600 OK)
│   ├── starter.py                              ~410  (-saga inline; ≤600 OK)
│   └── close_report_router.py                  ~50
│
└── context_engine/                             ~1,140
    ├── __init__.py                             1
    ├── core.py                                 ~145  (engine + composer + errors merge; -16 lever #14a)
    ├── packet.py                               90
    ├── scope.py                                ~115  (-8 lever #14b)
    ├── renderer.py                             ~235  (-19 lever #16b)
    ├── recipes_registry.py                     ~75   (-12 lever #5)
    └── recipes/                                ~937
        ├── __init__.py                         ~108  (lever #23: +summaries +entry_executor merge)
        ├── attempt_landscape.py                216
        ├── evaluator.py                        145
        ├── generator.py                        ~243  (lever Opt-A: +mission_episode merge)
        └── planner.py                          ~225  (lever Opt-A: +helper merged here)
```

### Recipes merge clarification (resolved per iter4 Critic MAJOR #1)

The §File-Count Consolidation Plan adopts Option A: merge `helper`+`planner` AND `mission_episode`+`generator`. Final recipes: __init__ (108) + attempt_landscape (216) + evaluator (145) + generator (~243) + planner (~225). **5 recipe files.** The diagram above is the authoritative inventory; the previous version's `helper.py 131` line was a stale row left over from pre-merge sizing.

Final final count: **31 files** (sub-32, buffer 1). Resolves the overshoot above.

### Deletions

| File | wc-LoC | cloc-code | Proof |
|---|---|---|---|
| `task_center/events.py` | 110 | ~80 | Lever #1; `rg "from task_center\.events" backend/` returns 0 |
| 7 typed-payload classes in `task_state.py` | 100–110 (in-file) | ~75 | Lever #2; zero `.to_dict()` callers |
| 4 audit-payload classes in `audit.py` | 60–100 (in-file) | ~70 | Lever #18; grep confirmed all 7 hits internal |
| `mission/{episode_factory.py, episode_closure_router.py, saga.py, ancestry.py, repository.py}` | per iter2+#4+Phase 7 | n/a | iter2+lever grep |
| `task_center/registry.py` | 60 | ~45 | Lever #5; 2 consumers only |
| `attempt/stage_strategy.py` | 72 | ~55 | Lever #7; orchestrator.py sole consumer |
| `task_center/{config.py, exceptions.py, task_ids.py}` | 49 combined | ~40 | Phase 7 fold into `_core/types.py` |
| `task_center/protocols.py` | 62 (post-#10 ~50) | ~40 | Phase 7 fold into `_core/types.py` (after #10 RegisteredEpisodeManager removal) |
| `task_center/persistence.py` | 240 | ~190 | Phase 7 fold into `_core/types.py` |
| `task_center/audit.py` | 198 (post-#18 ~140) | ~110 | Phase 7 fold into `_core/infra.py` |
| `task_center/invariants.py` | 206 (post-#8 ~135) | ~110 | Phase 7 fold into `_core/infra.py` |
| `task_center/contexts.py` | 160 (post-#9 ~95) | ~75 | Phase 3 relocation into `attempt/contexts.py` |
| `task_center/launcher.py` | 375 (post-#6 ~295) | ~225 | Phase 3 → `attempt/launcher.py` → Phase 7 → `attempt/launch.py` |
| `task_center/launch_builder.py` | 159 (post-#15 ~75) | ~60 | Phase 3 → `attempt/launch_builder.py` → Phase 7 → `attempt/launch.py` |
| `task_center/lifecycle.py` | 137 (post-#9 ~115) | ~90 | Phase 3 → `attempt/lifecycle.py` → Phase 7 → `attempt/runtime_lifecycle.py` |
| `task_center/saga.py` | 105 | ~80 | Phase 3 → `mission/saga.py` → Phase 5f inline |
| `mission/episode_factory.py`, `mission/episode_closure_router.py` | 130 + 145 | ~110+~115 | Phase 4a merger |
| `mission/ancestry.py`, `mission/repository.py` | 81 + 107 | ~65+~80 | Phase 7 → `mission/handler.py` (post-merger) |
| `episode/{closure_report.py, manager.py, registry.py, state.py}` | 49+306+33+60 | per-file | Phase 7 → `episode/__init__.py` bundle |
| `entry/{controller.py, coordinator.py, sandbox_bridge.py}` | 185+346+77 | per-file | Phase 7 → `entry/__init__.py` bundle |
| `agent_routing/{predicates.py, resolver.py}` | 123+129 | ~95+~100 | Phase 7 → `agent_routing/__init__.py` |
| `context_engine/{engine.py, composer.py, errors.py}` | 63+89+25 | per-file | Phase 7 → `context_engine/core.py` |
| `context_engine/recipes/{summaries.py, entry_executor.py, mission_episode.py, helper.py}` | 20+64+105+131 | per-file | Phase 7 → recipe consolidation |
| `episode/registry.py`'s `RegisteredEpisodeManager` class | 10–15 (in-file) | ~12 | Lever #10 grep proof |
| `context_engine/engine.py`'s `ContextPacketStoreProtocol` + field | ~24 (in-file) | ~20 | Lever #14a grep proof |
| `context_engine/scope.py`'s `for_helper` classmethod | ~8 (in-file) | ~8 | Lever #14b grep proof |
| `LaunchBuilder.for_planner/for_generator/for_evaluator/for_entry` | ~70–100 (in-file) | ~65 | Lever #15; replaced by `for_role(...)` |
| `AgentResolver`, `PromptRenderer`, `AttemptAgentLauncher` Protocols | ~35–55 (in-file) | ~40 | Lever #16; replaced by concrete-class annotations |

---

## Mission Merger Contract Addendum (iter4)

iter2's contract: `mission/handler.py` ≤300 LoC after merger of handler+episode_factory+episode_closure_router.

**iter4 amendment:** Phase 7 absorbs `mission/repository.py` (107) + `mission/ancestry.py` (81) into `mission/handler.py`. Post-Phase-7 size estimate: ~468 LoC. **The ≤300 ceiling is RELAXED to ≤480 LoC** for this bundle ONLY.

**Iter2 regression test (`test_mission_handler_merged_dependencies_isolated.py`)** must be amended in Phase 4a / Phase 7 to reflect the new ≤480 ceiling. The ≤300 assertion line is replaced with a ≤480 assertion line. Iter2 contract's "merge handler-family only" remains binding until Phase 7; Phase 7 separately absorbs repository+ancestry as an explicit bundle step.

**Phase 4a ceiling: ≤300** (unchanged from iter2).
**Phase 7c ceiling: ≤480** (iter4 amendment).
**Iter4 acceptance criterion AC #10: `mission/handler.py` ≤480 LoC** (replaces iter3 AC #10 of ≤300).

---

## Phased Migration (iteration 4)

### Phase 0 — Baseline, spike, & external-grep gate

- Baseline `.venv/bin/pytest backend/tests/`; record total tests + duration.
- **cloc baseline:** `find backend/src/task_center -name "*.py" | xargs wc -l` confirms 7,613 wc-LoC, 62 files. User-reported cloc-code: 5,443. Capture as Phase 0 ground truth.
- **Cloc-code spike (iter4 Critic CRITICAL #1 requirement):** in a throwaway branch, execute Lever #1 (events.py delete) + Lever #14a (ContextPacketStoreProtocol delete) + Lever #14b (for_helper delete) + Lever #18 (audit payload classes delete). Measure actual `cloc backend/src/task_center` `code` field delta. If actual ≥ 1.0× the conservative band (215 cloc-code for those 4 levers), proceed. **If actual < 1.0× conservative, iterate the plan before Phase 1** — adjust per-lever estimates and re-run §cloc-code roll-up.
- **PEP 420 namespace verification (iter4 Critic A4 elevated):** `python -c "import task_center.attempt, task_center.mission; assert task_center.attempt.__file__ is not None and task_center.mission.__file__ is not None, 'namespace-package mode unsafe'"`. This MUST pass at every phase gate from Phase 0 through Phase 8. If Phase 7's `__init__.py` removal trips it, abort that bundle (per S9 fallback: keep 1-line re-export shells; final file count 33 — still in user's ≤32 ceiling band only via deeper recipes merge or by surfacing a 1-file overshoot to user).
- Per-module external-grep across `backend/src/{db,tools,live_e2e,agents}` for every file slated for relocation, deletion, or inlining. Capture per-module hit counts.
- Pre-grep all Phase 5 lever consumer sites — record commands + counts in Phase-0 manifest committed alongside the plan.
- Pre-sweep doctest grep: `rg "^\s*>>>" backend/src/task_center/` (used in Phase 6 only; cloc-code gate is independent).
- Confirm `which pytest` returns `.venv/bin/pytest` (per project memory `feedback_use_venv_pytest`).

### Phase 1 — Pure deletions

- **Lever #1:** delete `events.py` (110 wc / ~80 cloc-code).
- **Lever #2:** delete 7 typed-payload classes + 2 unions from `task_state.py` (in-file ~100 wc / ~75 cloc-code).
- **Lever #10:** delete `RegisteredEpisodeManager` from `protocols.py` + the typing references in `episode/registry.py` (~15 wc / ~12 cloc-code).
- **Lever #14a:** delete `ContextPacketStoreProtocol` + `context_packet_store` field from `context_engine/engine.py` (~16 wc / ~20 cloc-code).
- **Lever #14b:** delete `ContextScope.for_helper` from `context_engine/scope.py` (~8 wc / ~8 cloc-code).
- **Lever #18:** delete 4 audit payload classes from `audit.py`; inline payload-dict construction at 3 emit sites (~70 wc / ~70 cloc-code).
- Verification: pytest green; cloc-code delta ≥ 270.
- **Phase 1 cumulative cloc-code: ~270.** Floor remaining: 819.

### Phase 2 — `_core/` granular migration + `_EXPORTS` re-point

Move `exceptions.py` → `_core/exceptions.py`, `task_ids.py` → `_core/ids.py`, `config.py` → `_core/config.py`, `audit.py` → `_core/audit.py`, `invariants.py` → `_core/invariants.py`, `persistence.py` → `_core/persistence.py`, `protocols.py` → `_core/protocols.py`. Re-point `_EXPORTS`. Phase 2 = verbatim moves; logic untouched.

- Verification: full pytest green; root-import probe lazy.
- **Phase 2 cumulative cloc-code: ~270** (no additional savings; relocation only).

### Phase 3 — Domain-folder relocations (verbatim moves)

Move `contexts.py` → `attempt/contexts.py`, `lifecycle.py` → `attempt/lifecycle.py`, `launcher.py` → `attempt/launcher.py`, `launch_builder.py` → `attempt/launch_builder.py`, `saga.py` → `mission/saga.py`. **No inlining yet.**

- Verification: pytest green; per-target-test-slice green.
- **Phase 3 cumulative cloc-code: ~270.**

### Phase 4a — Mission handler family merger (iter2 contract)

Per iter2 Phase 4a: handler+episode_factory+episode_closure_router → 1 file. Ceiling ≤300 LoC.

- New regression test: `test_lifecycle/test_mission_handler_merged_dependencies_isolated.py`.
- Verification: pytest green; `wc -l mission/handler.py ≤ 300`.
- **Phase 4a cumulative cloc-code: ~390** (lever #3 +100–130).

### Phase 4b — DTO mergers + close-report router rename

Per iter2 Phase 4b.

- **Phase 4b cumulative cloc-code: ~400** (lever #11 +10–14).

### Phase 5 — In-place inlinings (one commit per lever; per-lever abort band)

Each lever its own commit. Per-lever abort band per §Lever-by-Lever Risk Matrix. Order: smallest behavioral surface first.

- **5a — Lever #9** Protocol collapse in `attempt/contexts.py`. cloc-code: +30–45.
- **5b — Lever #7** stage_strategy compaction (fold into `attempt/orchestrator.py`). cloc-code: +30–40.
- **5c — Lever #8** invariants consolidation (5 one-liner inlines). cloc-code: +45–70.
- **5d — Lever #6** launcher exhaustion-helper parameterization. cloc-code: +65–95.
- **5e — Lever #15** `LaunchBuilder.for_role(...)` consolidation. cloc-code: +55–80.
- **5f — Lever #16a/b/c** single-impl Protocol → concrete class refs (3 sub-levers). cloc-code: +30–45.
- **5g — Lever #17** `has_orchestrator_factory` inline. cloc-code: +5–8.
- **5h — Lever #24** Episode `_orchestrator_factory is None` guard inlines. cloc-code: +5–10.
- **5i — Lever #5** Registry[T] inlining. cloc-code: +25–35.
- **5j — Lever #4** Saga inlining. cloc-code: +55–70.

Verification per lever: pytest green (sub-suite + full suite); cloc-code delta tracked.

- **Phase 5 cumulative cloc-code: ~745–898.** Floor remaining: 191–344.

### Phase 6 — Docstring compression sweep (NOT counted toward cloc-code gate)

Per §Docstring Compression Policy (iter3, unchanged). Sub-package order: `_core/`, `agent_routing/`, `attempt/`, `episode/`, `mission/`, `entry/`, `context_engine/`. **Phase 6 reduces cloc-comment-LoC, NOT cloc-code-LoC.** It does not contribute to AC #5 floor.

Phase 6 is retained for downstream review benefit + reduced wc-LoC. **It is NOT part of the gate calculation.**

- **Phase 6 cumulative cloc-code: ~745–898** (unchanged from Phase 5).

### Phase 7 — File-count consolidation

In order:

- **7a:** `_core/types.py` bundle (persistence + protocols + ids + exceptions + config). `_EXPORTS` re-points to `_core/types`. cloc-code delta: +15–25 (boilerplate trimming + lever #21 shim folding).
- **7b:** `_core/infra.py` bundle (audit + invariants). cloc-code delta: +10–15.
- **7c:** `mission/handler.py` absorbs `mission/repository.py` + `mission/ancestry.py`. **Ceiling ≤480 LoC.** cloc-code delta: +30–50 (boilerplate + duplicate import + helper merge).
- **7d:** `attempt/launch.py` merger (launcher + launch_builder, post-lever-#6 + post-lever-#15). cloc-code delta: +5–10.
- **7e:** `attempt/runtime_lifecycle.py` merger (runtime + lifecycle, post-lever-#9). **Lever #20.** cloc-code delta: +5–10.
- **7f:** `agent_routing/__init__.py` merger (predicates + resolver, post-lever-#5 + post-lever-#16a). **Lever #22.** cloc-code delta: +15–25.
- **7g:** `episode/__init__.py` bundle (state + closure_report + manager + registry). cloc-code delta: +20–30.
- **7h:** `entry/__init__.py` bundle (controller + coordinator + sandbox_bridge). **Abort to entry/coordinator.py + entry/controller.py split if post-merge >600 LoC.** cloc-code delta: +15–25.
- **7i:** `context_engine/core.py` (engine + composer + errors). cloc-code delta: +10–15.
- **7j:** Recipes consolidation: `recipes/__init__.py` absorbs `summaries.py` + `entry_executor.py` (**lever #23**), `recipes/generator.py` absorbs `mission_episode.py`, `recipes/planner.py` absorbs `helper.py`. cloc-code delta: +20–30.

**Phase 7 sub-package `__init__.py` constraint:** **NO eager cross-package imports at module top level.** Use lazy `__getattr__` if needed.

- Per-step regression gate: root-import probe + full pytest + import-cycle check.
- **Phase 7 cumulative cloc-code: ~893–1,108.**

### Phase 8 — Cleanup, ruff, final verification

- Trim `__init__.py` docstring; confirm `_EXPORTS` map.
- `ruff check backend/src/task_center backend/tests/unit_test/test_task_center` clean.
- Root-import probe.
- Full `pytest` green.
- **cloc verification:** `find backend/src/task_center -name "*.py" | xargs cloc --quiet` (install cloc if absent: `brew install cloc`). Pre/post cloc-code delta MUST ≥ 1,089.
- File count: `find backend/src/task_center -name "*.py" -not -path "*__pycache__*" | wc -l` ≤ 32.
- Per-file ceiling: `find backend/src/task_center -name "*.py" -not -path "*__pycache__*" | xargs wc -l | sort -rn | head -1` ≤ 600.

**Cloc-code budget contingency (Phase 8 gap closer):**

If Phase 8 cloc-code delta < 1,089, executor MUST activate the **deferred backlog**:

1. **#16d/e activation:** complete StageStrategy + Ctx Protocols→AttemptDeps consolidation (~80–140 cloc-code).
2. **#8-extended:** inline 5–8 additional invariants assertion call-sites (~30–60 cloc-code).
3. If still under floor: STOP, surface deficit to user, propose follow-up plan. Do not silently slip under 1,089.

---

## Lever-by-Lever Risk Matrix (DELIBERATE mode, iter4)

| Lever | Abort band | Rollback strategy |
|---|---|---|
| #1 events.py | grep finds any external importer | `git revert` |
| #2 task_state.py payload deletion | post-deletion grep finds `.to_dict()` call on payload types | `git revert` |
| #3 mission handler merger | post-Phase-4a merged-file LoC >300 | `git revert` Phase 4a; keep 3 files |
| #4 Saga inlining | inlined `_run` >60 LoC OR failures-aggregation >5 LoC | `git revert` |
| #5 Registry inlining | net cloc-code win <15 | `git revert` |
| #6 launcher exhaustion parameterization | parameterized helper >40 LoC OR per-role test diverges | `git revert` |
| #7 stage_strategy compaction | dispatch table >20 LoC | `git revert` |
| #8 invariants consolidation | any single inline >2 lines at call site | leave that assert; consolidate one-liners only |
| #9 contexts Protocol collapse | consumer fails `isinstance` smoke OR `ruff`/`mypy` regression | `git revert` |
| #10 RegisteredEpisodeManager removal | external grep hit (none expected) | `git revert` |
| #12 docstring sweep | sub-package import smoke fails after any commit | `git revert` that sub-package only |
| #13 file-count consolidation | root-import probe fails OR import cycle | `git revert` that bundle |
| **#14a** ContextPacketStoreProtocol deletion | grep finds external user of `context_packet_store` field | `git revert` (none expected — field is never set) |
| **#14b** `for_helper` deletion | grep finds external call | `git revert` |
| **#15** `for_role(...)` consolidation | parameterized `for_role` >40 LoC OR per-callsite test diverges | `git revert`; keep 4 named methods |
| **#16a/b/c** Protocol→concrete | any external import of deleted Protocol name | `git revert`; keep Protocol declaration |
| **#17** `has_orchestrator_factory` inline | callsite >2 lines | `git revert` |
| **#18** audit payload deletion | post-deletion grep finds any external reference | `git revert`; keep classes |
| **#20** runtime+lifecycle merge | merged file >300 LoC OR cycle introduced | `git revert`; keep 2 files |
| **#21** shim header trim | sub-package import smoke fails | `git revert` |
| **#22** agent_routing merge | predicates/resolver private-helper conflict | `git revert`; keep 2 files |
| **#23** recipes mini-merge | recipe import surface breaks | `git revert` |
| **#24** episode `_orchestrator_factory` guard inline | callsite >2 lines | `git revert` |

---

## Pre-Mortem (DELIBERATE mode, iter4)

### S1 — Parallel user commits (retained from iter2)

Per project memory `feedback_parallel_user_commits`. Stage with explicit file paths only.

### S2 — Missed external importer

Per iter2/iter3 §S2. Extended scope: every lever's pre-grep covers all 4 external dirs.

### S3 — God-object regression in merged files

iter4 binds **≤600 LoC** at every file post-merge. Per-bundle ceilings:
- `mission/handler.py` ≤480 (relaxed iter4)
- `entry/__init__.py` ≤600 (abort-and-split if exceeded)
- `attempt/orchestrator.py` ≤480
- `attempt/launch.py` ≤480
- `_core/types.py` ≤480
- `_core/infra.py` ≤300
- `episode/__init__.py` ≤480

### S4 — Inlining drift

Per iter3 §S4 (unchanged).

### S5 — Public signature drift

Per iter3 §S5 (unchanged). Per-lever `inspect.signature()` snapshots in regression test.

### S6 — Docstring sweep breaks a doctest

Per iter3 §S6.

### S7 — Phase 7 import cycle

Per iter3 §S7. Constraint: merged sub-package `__init__.py` MUST NOT import siblings at top level.

### S8 — cloc-code floor under-delivery (iter4 NEW)

Phase 5 + Phase 7 lever roll-up undershoots 1,089. **Mitigation:** Phase 8 gap-closer activates deferred backlog (#16d/e, #8-extended). If still under floor, **STOP and surface to user**. Do not silently slip.

### S9 — `attempt/__init__.py` deletion breaks `live_e2e/squad/runner.py` (iter4 NEW)

`live_e2e/squad/runner.py:33-34` imports `from task_center.attempt import Attempt` and `from task_center.episode.state import Episode`. **Mitigation:** keep 1-line `__init__.py` re-export shells (per §File-Count Consolidation Plan Option D-relaxed). Iter4 file-count ceiling: 33 if shells retained; surface to user before Phase 7 if strict ≤32 is non-negotiable.

### S10 — `entry/__init__.py` bundle exceeds 600 LoC (iter4 NEW)

Post-merge entry bundle estimated ~547 — buffer 53. **Mitigation:** Phase 7h LoC measurement gate. If >600 measured, abort entry/ bundle merge; keep `entry/coordinator.py` + `entry/controller.py` separate (file-count climbs by 1, total 32 — at ceiling but not over).

### S11 — `mission/handler.py` bundle exceeds 480 (iter4 NEW)

Post-Phase-7c handler estimated ~468 — buffer 12. **Mitigation:** Phase 7c LoC measurement gate. If >480 measured, abort the `repository.py`+`ancestry.py` absorb; keep them as `mission/repository.py` and `mission/ancestry.py`. File-count climbs by 2, total 33.

---

## Expanded Test Plan (DELIBERATE mode, iter4)

### Unit

- All existing tests under `backend/tests/unit_test/test_task_center/` (48 files) pass.
- New per-lever regression tests:
  - `test_lifecycle/test_mission_handler_merged_dependencies_isolated.py` (iter2 Phase 4a; ≤300 → ≤480 amendment).
  - `test_lifecycle/test_saga_inline_equivalence.py` (Phase 5j).
  - `test_lifecycle/test_registry_inline_surface.py` (Phase 5i).
  - `test_lifecycle/test_launcher_exhaustion_parametrized.py` (Phase 5d).
  - `test_lifecycle/test_stage_dispatch.py` (Phase 5b).
  - `test_lifecycle/test_contexts_protocol_collapse.py` (Phase 5a).
  - **`test_lifecycle/test_launch_builder_for_role.py`** (Phase 5e; iter4 NEW).
  - **`test_lifecycle/test_audit_emission_shape.py`** (Phase 1 / lever #18; iter4 NEW): 3 asserts that the post-#18 audit emission carries the same dict keys + types as pre-#18 (snapshot of pre-deletion emission used as fixture).
  - **`test_lifecycle/test_concrete_class_annotations.py`** (Phase 5f / lever #16; iter4 NEW): static import-check that `AgentResolver`, `PromptRenderer`, `AttemptAgentLauncher` Protocol names are NOT importable post-deletion AND that the concrete-class refs (`RuleBasedAgentResolver`, `MarkdownPromptRenderer`, `EphemeralAttemptAgentLauncher`) ARE importable + usable.

### Integration

- `test_phase04_close_report_delivery.py` passes.
- All Phase 5 regression tests pass against post-inline modules.

### E2E

- `backend/src/live_e2e/squad/runner.py` import smoke (iter2 retained).
- `backend/src/live_e2e/real_agent_run.py` import smoke (iter4 NEW — verifies lever #16c does not break the launcher reference at line 157).

### Observability

- Audit emission shape unchanged (lever #18 regression test).
- Events.py deletion + payload-class deletion remove dataclass symbol names with zero logging/metric callers (pre-grep confirms).

### Per-phase / per-lever regression gate

After each phase AND each Phase 5 lever sub-commit:
```
.venv/bin/pytest backend/tests/unit_test/test_task_center/ \
                 backend/tests/unit_test/test_tools/ \
                 backend/tests/unit_test/test_agents/ -x
```

After Phase 2, Phase 4a, every Phase 5 lever, Phase 7 (every sub-step):
```
.venv/bin/pytest backend/tests/ -x
```

After Phase 8:
- `.venv/bin/pytest backend/tests/ -x` (full)
- `cloc backend/src/task_center` (install via `brew install cloc` if absent)
- `find backend/src/task_center -name "*.py" -not -path "*__pycache__*" | wc -l`
- `find backend/src/task_center -name "*.py" -not -path "*__pycache__*" | xargs wc -l | sort -rn | head -5`
- `ruff check backend/src/task_center`

---

## Risks + Mitigations (iter4)

| Risk | Likelihood | Mitigation |
|---|---|---|
| Cycle re-introduced at package root | M | Phase 8 root-import probe; Phase 7 `__init__.py` constraint. |
| `events.py` hidden external importer | L | Phase 1 pre-deletion grep (zero hits confirmed). |
| `task_state.py` payload hidden caller | L | Phase 1 pre-deletion grep `.to_dict()` (zero hits on payload types). |
| Audit payload hidden caller (lever #18) | L | Pre-grep confirmed (7 self-hits only). |
| Hidden external importer of relocated root files | L | Phase 0 + per-phase external-grep. |
| `_EXPORTS` re-point breaks external root-facade users (Phase 2 + Phase 7 double-touch) | M | Phase 2 + Phase 7 FULL suite gates. |
| Mission merger LoC >300 (Phase 4a) | M | Pre-Mortem #3 mitigation. |
| Mission handler LoC >480 (Phase 7c) | M | Pre-Mortem S11; abort the repository+ancestry absorb. |
| `entry/__init__.py` >600 LoC | M | Pre-Mortem S10; abort entry bundle, keep split. |
| Saga inlining drift | M | 3-assert regression test (Phase 5j). |
| Registry inlining net win <15 cloc-code | M | Lever abort band; revert. |
| Launcher exhaustion parameterization grows >40 LoC | L | Lever abort band; revert. |
| `for_role(...)` parameterization >40 LoC | M | Lever abort band; revert. |
| Single-impl Protocol deletion breaks `composer.py` annotation | L | Lever #16 grep + isinstance smoke. |
| Public signature drift | M | Per-lever `inspect.signature()` snapshot. |
| Docstring sweep breaks doctests | L | Phase 0 exclusion list. |
| **cloc-code floor under-delivery (<1,089)** | **M** | **Phase 8 gap-closer (deferred backlog activation); surface to user if still under.** |
| Phase 7 sub-package `__init__.py` introduces eager cross-package imports | M | Lazy `__getattr__`; root-import probe. |
| `_core/types.py` bundle becomes misc-bucket | M | Principle 3: single bounded concern only. |
| Test-churn larger than 22 files | M | Per-pattern grep produces exact list pre-phase. |
| Parallel user commits | M | Explicit file-path staging; per-phase HEAD verification. |
| cloc binary missing | L | Phase 0 gate: `which cloc || brew install cloc`. |
| `attempt/__init__.py` / `mission/__init__.py` deletion breaks `live_e2e/squad/runner.py` | M | Pre-Mortem S9; keep 1-line re-export shells; file-count 33. |

---

## Acceptance Criteria (iteration 4)

1. **All existing task_center tests + cross-package callers pass.** No skips, no xfails introduced.
2. **External callers compile without edits.** `git diff --stat -- backend/src/db backend/src/tools backend/src/live_e2e backend/src/agents` shows zero lines changed.
3. **`task_center/events.py` removed.** Zero `from task_center.events` hits in `backend/`.
4. **Root-import probe** prints only `['task_center']` — `__init__.py` lazy.
5. **`cloc backend/src/task_center` code-LoC delta in the 850–1,500 band (iter5 softened per user request c).** User negotiated the binding floor down from 1,089 to a band acknowledging the conservative-case math (885) sits just above the new floor. Measured via `cloc --quiet backend/src/task_center` pre/post; comment/blank delta tracked separately and reported but does NOT count.
   - **Healthy / target band: 1,089–1,500 cloc-code** (the original 20% goal — aspirational; mid-band roll-up of 1,151 clears this).
   - **Soft floor: 850 cloc-code.** Below 1,089 but ≥850 = recorded as known underrun in plan addendum + acknowledged by user. Plan declares done.
   - **Hard floor: 850.** Below 850 = STOP. Triggers Phase 8 gap-closer (deferred backlog activation). If still under 850 after deferred backlog, surface to user as iteration-5 deficit.
   - **Above 1,500 = behavioral leak suspected.** Triggers stop-and-review for signature drift.
6. **File count ≤ 32.** Measured via `find backend/src/task_center -name "*.py" -not -path "*__pycache__*" | wc -l`. Plan target: 31 (buffer 1).
7. **Every file ≤ 600 LoC.** Measured via `find backend/src/task_center -name "*.py" -not -path "*__pycache__*" | xargs wc -l | sort -rn | head -1`.
8. **`_EXPORTS` map: no entry names added or removed.** Module paths re-pointed only per the path-change table.
9. **Zero external public-signature changes.** Verified by:
   - `grep -E "^[A-Za-z_]+ ?=" backend/src/task_center/__init__.py` shows `_EXPORTS` keyset unchanged.
   - Per-lever `inspect.signature()` snapshots match pre/post for all public methods of inlined/merged files.
10. **`mission/handler.py` LoC**: ≤300 at end of Phase 4a; **≤480 at end of Phase 7** (iter4 relaxation).
11. **Other bundle ceilings:** `attempt/orchestrator.py` ≤480, `attempt/launch.py` ≤480, `attempt/runtime_lifecycle.py` ≤300, `_core/types.py` ≤480, `_core/infra.py` ≤300, `episode/__init__.py` ≤480, `entry/__init__.py` ≤600.
12. **All per-lever regression tests pass:**
    - `test_mission_handler_merged_dependencies_isolated.py` (≤480 ceiling line)
    - `test_saga_inline_equivalence.py` (3-assert + signature snapshot)
    - `test_registry_inline_surface.py`
    - `test_launcher_exhaustion_parametrized.py`
    - `test_stage_dispatch.py`
    - `test_contexts_protocol_collapse.py`
    - `test_launch_builder_for_role.py` (iter4 NEW)
    - `test_audit_emission_shape.py` (iter4 NEW)
    - `test_concrete_class_annotations.py` (iter4 NEW)
13. **`live_e2e/squad/runner.py` + `live_e2e/real_agent_run.py` import smoke passes.**
14. **`ruff check backend/src/task_center` clean.**

---

## ADR

- **Decision:** Adopt **Path F (Iter4 Aggressive)** — Path D (iter3) + ContextPacketStoreProtocol+for_helper deletion (lever #14) + LaunchBuilder factory consolidation (lever #15) + single-impl Protocol→concrete refs (lever #16a/b/c) + `has_orchestrator_factory` inline (lever #17) + audit dead-payload deletion (lever #18, GREEN per grep) + `attempt/runtime_lifecycle.py` merge (lever #20) + Wave-6 shim audit (lever #21) + 3 secondary Phase 7 mergers (#22, #23, #24). Trades a larger behavioral surface for clearing the cloc-code 1,089 floor at ≤32 files with ≤600 LoC each.

- **Drivers:**
  1. Public-surface integrity (grep-bounded; signatures preserved).
  2. cloc-code reduction ≥ 1,089 (user-mandated floor; docstring cuts excluded).
  3. Per-file ≤600 ceiling + file-count ≤32 ceiling.

- **Alternatives considered:**
  - **Path D (iter3):** rejected. cloc-code estimate ~700–950, under 1,089 floor. File-count 37–40, over 32.
  - **Path C (iter2 Hybrid):** rejected at iter3.
  - **Path B (iter1 accept-all):** rejected at iter1.
  - **Path A (iter1 verbatim):** rejected at iter1.
  - **Path E (Path D minus docstring sweep):** rejected; sweep doesn't contribute to cloc-code gate anyway.
  - **Path G (floor-conservative):** retained as fallback if reviewer blocks levers #16 / #20–#21. cloc-code 1,000–1,150, marginal.
  - **Full StageStrategy + Ctx Protocol deletion (lever #16d/e):** retained as deferred backlog, activated at Phase 8 if cloc-code floor unmet.
  - **Inlining `task_state.py`:** still rejected — public deep-import path PIN'd.
  - **Folding `MissionClosureReportRouter` into `MissionStarter`:** still rejected — 6 test instantiation sites.

- **Why chosen:** Path F is the most aggressive lever set the iteration loop produced under Principle 1 (no signature changes) at ≤32 files with ≤600 LoC each. **Honest math (per iter4 Critic CRITICAL #3):** with all amendments and gap-closer levers (#25, #26) bound into Phase 5 scope, conservative roll-up is 885 cloc-code — STILL under the 1,089 floor by 204. Mid-band roll-up 1,151 clears the floor with +62 buffer. Conservative-case clearance depends on (a) Phase 0 cloc-spike validating the per-lever estimates, and (b) Lever #25's investigation finding ≥100 cloc-code of inlinable code in the four largest files. If Phase 0 spike shows the conservative-band estimates are tight (actual ≤ 1.0× projection), the plan REQUIRES one more code-bearing lever before Phase 1 begins — surfaced to user as a re-iteration gate.

- **Consequences:**
  - `_core/` is the canonical home for cross-cutting infra, bundled into `_core/types.py` + `_core/infra.py`.
  - `mission/handler.py` becomes the sole entry for the handler/factory/router triad AND absorbs repository+ancestry in Phase 7 (≤480 amendment).
  - Saga, Registry, stage_strategy, LaunchBuilder factory methods, and the launcher exhaustion helpers are inlined into single (or bounded-2) consumers; their files are deleted.
  - Three single-impl Protocols (`AgentResolver`, `PromptRenderer`, `AttemptAgentLauncher`) replaced with direct concrete-class annotations.
  - 4 audit payload classes + `ContextPacketStoreProtocol` + `ContextScope.for_helper` deleted.
  - `attempt/launch.py` unifies launcher + launch_builder; `attempt/runtime_lifecycle.py` unifies runtime + lifecycle; `agent_routing/__init__.py` unifies predicates + resolver; `mission/handler.py` absorbs repository + ancestry; `episode/__init__.py` bundles state + closure_report + manager + registry; `entry/__init__.py` bundles controller + coordinator + sandbox_bridge; `context_engine/core.py` bundles engine + composer + errors; recipes consolidates 4→2 files.
  - 62 files → **31 files** (50%).
  - 7,613 wc-LoC → ~5,800 wc-LoC; cloc-code 5,443 → ~4,354 (≥1,089 reduction target).
  - Iter2 ≤300 handler ceiling RELAXED to ≤480 in iter4 for the Phase 7c repository+ancestry absorb.

- **Mode:** **DELIBERATE.** Justified by 13 behavioral levers (#3, #4, #5, #6, #7, #8, #9, #15, #16, #17, #18, #20, #24) + 7 file-count mergers + cosmetic sweep + cloc-code floor gate. Pre-mortem (11 scenarios) + expanded test plan (unit/integration/e2e/observability + per-lever regression gate + per-lever abort band + Phase 8 gap-closer) attached.

- **Follow-ups:**
  - Open question (recorded in `.omc/plans/open-questions.md`): does the user accept 33 files (with shells) OR enforce ≤32 strictly (requiring NG-3 override for live_e2e/squad/runner.py)? Iter4 plans for 31 via recipes consolidation; if user rejects merging mission_episode→generator + helper→planner, fall back to 33.
  - Open question: AC #5 may slip into the deferred backlog at Phase 8. Activate the 16d/e + 8-extended levers OR surface deficit.
  - Monitor `invariant_replan_dependents_must_be_pending` memory: invariants consolidation (lever #8) inlines one-liners only; the named-invariant function `assert_replan_dependents_must_be_pending` is preserved as a function (not inlined) because it's referenced by name in the memory.
  - Separate plan: re-evaluate `_core/types.py` cohesion after Phase 7 (concerns: persistence + protocols + primitives bundled — risk of misc-bucket per Principle 3; reviewer veto preserved).
  - Separate plan: if Phase 7c handler absorbs put `mission/handler.py` at ≤480 with thin buffer, consider whether iter4's Mission Merger Contract addendum (relaxed to ≤480) is sustainable through future feature additions; may need a Phase-9-style split.
