# Agents Module Simplification Plan

Scoped follow-up to `class_field_simplification_PLAN.md`, covering the
`backend/src/agents/` definition model only. Source of truth:
`backend/src/agents/definition/model.py` (`AgentDefinition`). Three field/enum
removals, ordered by blast radius. ① and ② are genuine low-risk redundancies;
③ is a design task, not a deletion.

Evidence base: code anchors verified 2026-05-30 against `main`.

## Progress

- **① — DONE** (2026-05-30). Field + Protocol + config knob removed; factory
  builds rules from triggers + defaults.
- **② — DONE** (2026-05-30). Field removed, gate collapsed, MDs cleaned.
- **③ — not started.** Design task; see section below.

Verification: `test_agents/`, `test_tools/test_submission_terminal_routing.py`,
`test_engine/`, `test_task_center/` green (583 passed). All 6 real profiles load
under `extra="forbid"`. One pre-existing failure
(`test_attempt_launcher_retry.py::test_attempt_harness_records_runner_token_usage`,
`EphemeralRunResult ... event_count`) belongs to the parallel mock event-source
migration — not this change.

---

## ① Collapse `notification_rules` → keep `notification_triggers`  (clean win)  ✅ DONE

**Finding.** The two fields are not duplicates of each other:
`notification_triggers: list[str]` is declarative frontmatter IDs;
`notification_rules: list[AgentNotificationRule]` is resolved rule *objects*.
But in the loaded path `notification_rules` is **dead**:

- No `.md` can populate it — the Protocol fields are `Callable`s,
  unexpressible in YAML. Every profile uses `notification_triggers:`.
- No production code constructs `AgentDefinition(notification_rules=...)`
  (grep empty outside tests).
- `factory.py:380-385` merges `list(agent_def.notification_rules)` (always
  `[]`) + `resolve_harness_notification_triggers(triggers)` + defaults into one
  list.

**Change.**
- Drop the `notification_rules` field from `AgentDefinition`.
- `factory.py` builds rules purely from `triggers + defaults`.
- **Cascade:** also delete the `AgentNotificationRule` Protocol and the
  `arbitrary_types_allowed=True` knob in `model_config` (its only reason to
  exist).

**Cost / surface.** Only test fixtures that inject rule objects directly need
migrating to `notification_triggers`.

**Verify.** `agents` unit tests + `engine/agent/factory` tests green; a loaded
profile still gets default + trigger-resolved rules at launch.

---

## ② Remove `dispatchable_by_planner`  (pure redundancy)  ✅ DONE

**Finding.** Single consumer — `_is_generator_capable_agent`
(`tools/submission/planner/_schemas.py:102`):

```python
return definition.dispatchable_by_planner and definition.agent_kind in {EXECUTOR, VERIFIER}
```

Frontmatter proves the flag adds nothing: exactly `executor.md` and
`generator_verifier.md` set `dispatchable_by_planner: true`, and those are
exactly the `{EXECUTOR, VERIFIER}` kinds. The two clauses select the same set.

**Change.**
- Remove the field from `AgentDefinition`.
- Collapse the gate to `definition.agent_kind in {EXECUTOR, VERIFIER}`.
- Drop `dispatchable_by_planner: true` from the two profile MDs.

**Coupling with ③.** This collapse depends on `agent_kind` existing. Do ②
**before** ③ (or fold the gate replacement into ③'s design).

**Verify.** Planner submission tests: an executor/verifier name passes the gate,
a planner/advisor/explorer name is rejected.

---

## ③ Decouple terminal routing + retag `agent_kind`  (design — DRAFT)

> **Decision needed (read first).** The request is "remove `agent_kind`." The
> honest answer from the code: the **routing coupling** can be removed cleanly,
> but the **tag cannot** — three non-routing consumers depend on it (telemetry
> `metadata["role"]`, mock-runner dispatch, planner gate). So ③ is two separable
> changes: **A — decouple the router** (the real, clean win), and **B — what
> replaces the tag** (the only actual fork). This draft recommends **A + rename
> the enum `AgentKind` → `AgentRole`** (keep it typed, strip its routing role),
> and rejects full deletion. You can override B before we implement.

### Consumer map (verified 2026-05-30)

| consumer | use | change |
|---|---|---|
| `task_center/_core/terminal_tool_routing.py:127-150` (`_allowed_terminals`) | depth-aware terminal filtering via `if agent_kind == PLANNER … elif EXECUTOR …` | **A** — replaced by per-folder routing |
| `engine/agent/factory.py:378` + `tools/subagent/run_subagent/run_subagent.py:198` + mock `runner.py:311,1762,2012`, `scenario_loop_runner.py:167,213` | `metadata["role"] = agent_kind.value` (telemetry tag) | **B** — read `.role` instead |
| mock `runner.py:253-282` (invocation event + `_run_<role>`), `scenario_adapter.py:285` (`TurnScript` select) | behavioral dispatch on `agent_kind.value == "planner"/...` | **B** — planner/evaluator on `.role`; executor/verifier on `.name` (they share `role == generator`) |
| `tools/submission/planner/_schemas.py:102` | generator gate `agent_kind in {EXECUTOR, VERIFIER}` (post-②) | **B** — `role == AgentRole.GENERATOR` |

The enum itself carries **no routing logic** — it is a plain `StrEnum`. All
routing coupling lives in the `terminal_tool_routing.py` ladder. A and B are
therefore independent and can land in either order.

---

### Change A — decouple the router (the substantive win)

**Scope is small: only 2 of 6 profiles have routing rules** (planner, executor).
Each rule is a pure function `(depth: int, has_workflow: bool) -> frozenset[str] | None`:

- **planner:** `depth>1 → {submit_plan_closes_goal}`; else `{closes, defers}`.
- **executor:** `has_workflow=False → None`; `depth>1 → {success, blocker}`;
  else `{handoff, success, blocker}`.
- **verifier / evaluator / advisor / explorer:** no file → `None` (no filtering).

**Mechanism (recommended): explicit frontmatter path, mirroring `skill:`.**
The user asked for "script-based, in the agent's own folder." The loader already
has this exact idiom: the `skill:` field is a *relative path declared in
frontmatter, resolved against the profile's folder* (`loader.py:70-78`). Reuse
that pattern instead of inventing a magic `<stem>_routing.py` suffix convention —
explicit, greppable, and consistent with how profiles already reference adjacent
files.

Five design decisions:

1. **Declaration — explicit, not convention.** A profile that needs filtering
   adds `terminal_routing: planner_routing.py` to its frontmatter (relative path,
   resolved like `skill:`). No field → no router → never filtered. Only
   `planner.md` and `executor.md` declare it; the other four omit it.
   *Rejected:* magic `<stem>_routing.py` auto-discovery — implicit, surprising,
   and inconsistent with the explicit `skill:` precedent.

2. **Module contract — one pure function.**
   `select_terminals(*, is_nested: bool, has_workflow: bool) -> frozenset[str] | None`.
   Returns the *allowed* terminal superset (the router intersects it with the
   profile's declared `terminals`, exactly as today), or `None` for "no
   filtering." It takes only two derived booleans — never `ContextScope`, `deps`,
   or the definition — so it is unit-testable with zero fixtures.

3. **Where the path is stored — a real field, like `skill`.** Add
   `terminal_routing: Path | None = None` to `AgentDefinition`. Because it is a
   declared field it passes `extra="forbid"`, serializes for audit, and the
   loader resolves it with the same three lines as `skill`. `Path` needs no
   `arbitrary_types_allowed`.

4. **Where the code is imported — loader, at load (fail-fast).** The loader
   imports the module once at startup and attaches the callable. A broken routing
   module (missing file, missing/!callable `select_terminals`) then fails
   *startup*, consistent with how the loader already hard-fails on a missing
   `skill:` file or absent `agent_kind:`. The callable rides on a `PrivateAttr`
   (non-serializable, survives `model_copy`), exposed via a `terminal_router`
   property. *Alternative considered:* let the `task_center` router lazy-import
   from `definition.terminal_routing` on first launch — purer layering (the
   agents package never executes profile code) but defers failure to the first
   nested launch in production. Fail-fast wins.

5. **Router — thin dispatch, depth seam intact.** `_allowed_terminals` collapses
   to "call `definition.terminal_router` if present, else `None`"; it still calls
   `_nested_workflow_depth_gt_1(ctx)` to compute `is_nested`, so the existing
   monkeypatch test seam is untouched.

**Proportionality note / alternative.** Two functions do not, by themselves,
justify importing code from a data directory (and `profile/` becomes mixed
`.md` + `.py`). The lighter alternative is a **declarative routing block in
frontmatter** (depth/workflow conditions → terminal lists) interpreted by the
router — no code import, no new loader capability, `profile/` stays pure data.
It is less "script-based" than requested and needs a tiny interpreter for the
two-axis (`is_nested` × `has_workflow`) branching. Offered as the cheaper option
if executing profile-folder code is judged too heavy for two rules.

**Test seam.** `test_terminal_tool_router.py` monkeypatches
`_nested_workflow_depth_gt_1` by module path. The router still calls that helper
and passes its boolean result into the per-folder function (as `is_nested`), so
the seam is unchanged. Per-folder functions receive `is_nested` / `has_workflow`
as arguments — they never touch `ContextScope` or compute depth — which keeps
them trivially unit-testable in isolation.

#### Concrete sketch

`backend/src/agents/profile/main/planner_routing.py`:
```python
"""Launch-time terminal routing for the planner profile."""
from __future__ import annotations


def select_terminals(*, is_nested: bool, has_workflow: bool) -> frozenset[str]:
    # A nested planner (caller attempt is itself inside a workflow) may only
    # close its goal; a top-level planner may also defer.
    if is_nested:
        return frozenset({"submit_plan_closes_goal"})
    return frozenset({"submit_plan_closes_goal", "submit_plan_defers_goal"})
```

`backend/src/agents/profile/main/executor_routing.py`:
```python
"""Launch-time terminal routing for the executor profile."""
from __future__ import annotations


def select_terminals(*, is_nested: bool, has_workflow: bool) -> frozenset[str] | None:
    # Outside a workflow: keep the full frontmatter terminal set (no filtering).
    if not has_workflow:
        return None
    # Nested executors cannot hand off; only succeed or block.
    if is_nested:
        return frozenset({"submit_execution_success", "submit_execution_blocker"})
    return frozenset(
        {
            "submit_execution_handoff",
            "submit_execution_success",
            "submit_execution_blocker",
        }
    )
```

`planner.md` / `executor.md` frontmatter gains one line (resolved like `skill:`):
```yaml
terminal_routing: planner_routing.py
```
The other four profiles omit the key, so the loader attaches no router and they
are never filtered (today's `return None` for non-planner/executor).

`terminal_tool_routing.py` — the enum ladder collapses to a thin dispatch:
```python
@staticmethod
def _allowed_terminals(definition, ctx) -> frozenset[str] | None:
    router = definition.terminal_router
    if router is None:
        return None
    return router(
        is_nested=_nested_workflow_depth_gt_1(ctx),
        has_workflow=ctx.scope.workflow_id is not None,
    )
```
The `AgentKind` import, the `{PLANNER, EXECUTOR}` membership test, and the four
hardcoded terminal sets all leave this file.

`agents/definition/model.py` — a serializable `Path` field (mirrors `skill`)
plus the resolved callable on a private attr (no `arbitrary_types_allowed`,
survives `model_copy`):
```python
from pydantic import PrivateAttr

# --- terminal routing (Round ③) ---
# Absolute path to the profile's terminal-routing module, resolved by the
# loader from the relative ``terminal_routing:`` frontmatter field. ``None``
# when no routing is declared (the profile is never terminal-filtered).
terminal_routing: Path | None = None

_terminal_router: Callable[..., frozenset[str] | None] | None = PrivateAttr(default=None)

@property
def terminal_router(self) -> Callable[..., frozenset[str] | None] | None:
    return self._terminal_router
```

`agents/definition/loader.py` — resolve the path exactly like `skill:` (before
`model_validate`), then import once and attach the callable (after):
```python
routing_value = data.get("terminal_routing")
if routing_value:
    routing_path = (path.parent / str(routing_value)).resolve()
    if not routing_path.is_file():
        raise FileNotFoundError(
            f"Agent profile {path} declares terminal_routing: {routing_value!r}, "
            f"but {routing_path} does not exist."
        )
    data["terminal_routing"] = routing_path

definition = AgentDefinition.model_validate(data)

if definition.terminal_routing is not None:
    spec = importlib.util.spec_from_file_location(
        f"agents._routing.{path.stem}", definition.terminal_routing
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    select = getattr(mod, "select_terminals", None)
    if not callable(select):
        raise SkillLintError(  # or a dedicated RoutingModuleError
            f"{definition.terminal_routing} must export a callable "
            "'select_terminals(*, is_nested, has_workflow)'."
        )
    definition._terminal_router = select
```

**Threshold note.** Passing the `is_nested` boolean (not raw `depth: int`) fixes
the ">1" threshold in the router, matching today's behavior exactly and keeping
the monkeypatch seam. If a future profile needs its own threshold, switch the
contract to pass `depth: int` and move the test seam from
`_nested_workflow_depth_gt_1` to `_depth`. Not needed for the two current rules.

**Testing.** Three cheap layers, no fixture stacks:
- *Per-folder functions* — call `select_terminals(is_nested=…, has_workflow=…)`
  across the 4 input combinations and assert the returned set. Pure, no scope or
  stores. This is the main win: today the same coverage needs a constructed
  workflow hierarchy.
- *Router dispatch* — attach a stub callable to a definition, monkeypatch
  `_nested_workflow_depth_gt_1`, assert the stub's result is intersected with the
  declared terminals; and that a definition with no router returns unfiltered.
- *Loader* — a profile declaring `terminal_routing:` gets a callable attached;
  a missing file raises `FileNotFoundError`; a module without a callable
  `select_terminals` raises at load.

---

### Change B — retag `agent_kind` → `AgentRole` with a 5-member taxonomy  (recommended)

`agent_kind` survives as pure identity once routing is gone. Rename to signal
that, and **collapse the six members to five** — role becomes a true category,
coarser than `name`:

| `AgentRole` | profiles (by `name`) | folder(s) | was |
|---|---|---|---|
| `PLANNER` | `planner` | `main/` | planner |
| `GENERATOR` | `executor`, `verifier` | `main/` | executor + verifier |
| `EVALUATOR` | `evaluator` | `main/` | evaluator |
| `HELPER` | `advisor` | `helper/` | advisor |
| `SUBAGENT` | `explorer` | `subagent/` | explorer |

Changes:
- `AgentKind` enum → `AgentRole`, members `planner / generator / evaluator /
  helper / subagent`.
- `AgentDefinition.agent_kind: AgentKind` → `role: AgentRole`. Frontmatter
  `agent_kind:` → `role:` in all 6 MDs (executor.md + generator_verifier.md both
  → `role: generator`; advisor → `helper`; explorer → `subagent`); loader
  required-field check updated.
- **Planner gate** (`_schemas.py`) simplifies: `role in {EXECUTOR, VERIFIER}` →
  `role == AgentRole.GENERATOR`.
- **Telemetry** `metadata["role"]` becomes coarser — both executor and verifier
  report `"generator"`. The fine instance is still in `metadata["agent_name"]`.

**The one place role no longer suffices: mock-runner dispatch.** `runner.py:253-282`
picks `_run_executor` vs `_run_verifier` and `EXECUTOR_INVOKED` vs
`VERIFIER_INVOKED` — these are *behaviorally distinct* but now share
`role == generator`. Split that branch to dispatch on **`agent_def.name`**
(`"executor"` / `"verifier"`), keeping planner/evaluator on `role`. `name` is
already in scope at the dispatch (`runner.py:224` etc.).

**Already name-keyed → unaffected by the collapse** (verified): `ROLE_DIRECTIVES`
(name-keyed despite its name — keys `executor`/`verifier`/`explorer` stay),
`task_guidance_dispatch.py` (exact agent name), scenario `agent_name:` values,
and the `*_INVOKED` event assertions.

**Why keep it typed (reject full deletion / `str`).** The collapse makes
`name != role` real (`executor`/`verifier` → `generator`), so the consumers
*cannot* all fold to `agent_def.name` — full deletion is now off the table on its
own merits. A free-form `str` is also rejected: it reintroduces typo-silent
failure in the (now single-value) planner gate and the mock dispatch. A typed
`AgentRole` keeps exhaustiveness and a closed set while shedding the routing job.

---

### Migration order (within ③)

1. **A first** — add `terminal_router` field + loader discovery + the two
   `routing.py` files; collapse `_allowed_terminals`. `agent_kind` still exists
   but the router no longer reads it. Verify `test_terminal_tool_router.py` +
   `test_submission_terminal_routing.py` green.
2. **B second** — rename enum + field + frontmatter + the four consumers.
   Verify `test_agents/`, mock-runner suites, planner-gate tests green.

### Risks / open items

- **Mock-runner dispatch is the largest surface** (~7 sites across 3 files).
  Under B it is *not* a pure rename: the executor/verifier branch must move from
  `role` to `name` (both are `generator` now), while planner/evaluator stay on
  `role`. Do it carefully, not mechanically.
- Decide the per-folder mechanism (sibling module vs declarative block) before
  starting A.
- Confirm `terminal_router` can be attached without re-enabling
  `arbitrary_types_allowed` (use `PrivateAttr` or post-validate attach).
- **New redundancy surfaced by the taxonomy (candidate ④, not in ③ scope):**
  `agent_type ∈ {agent, subagent}` becomes derivable from `role` — `explorer` is
  the only `agent_type: subagent` profile and its role is now `subagent`, so
  `agent_type == subagent ⟺ role == subagent`. `agent_type` still has its own
  consumers (`run_subagent.py:197`, `factory.py:377` set `metadata["agent_type"]`),
  so leave it for now and revisit as a separate merge.
- `ROLE_DIRECTIVES` is misnamed (it is name-keyed, not role-keyed). Out of ③
  scope; note for a future rename to avoid confusion with the new `role` field.

---

## Sequencing

1. **① — DONE.**
2. **② — DONE.**
3. **③ — drafted above.** Resolve the two decisions (tag = rename vs delete;
   routing = sibling-module vs declarative), then implement A → B.
