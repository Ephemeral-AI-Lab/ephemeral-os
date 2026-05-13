---
phase: agents (ad-hoc directory review)
reviewed: 2026-05-13T00:00:00Z
depth: standard
files_reviewed: 7
files_reviewed_list:
  - backend/src/agents/__init__.py
  - backend/src/agents/definition/__init__.py
  - backend/src/agents/definition/loader.py
  - backend/src/agents/definition/model.py
  - backend/src/agents/definition/registry.py
  - backend/src/agents/definition/resolved_validation.py
  - backend/src/agents/definition/tool_validation.py
findings:
  critical: 1
  warning: 4
  info: 4
  total: 9
status: issues_found
---

# agents/ — Code Review Report

**Reviewed:** 2026-05-13
**Depth:** standard
**Files Reviewed:** 7
**Status:** issues_found

## Summary

`backend/src/agents/` is the small, mostly-clean schema-and-registry layer for ephemeral-agent personalities. The model/registry/resolved-validation core is correct and well-bounded; loader and tool-validation paths carry the real defects.

Highlights:
- **One BLOCKER**: `loader.py` silently swallows pydantic `ValidationError` (and any other exception) when an agent `.md` fails to parse or validate. A YAML typo or schema mismatch in a profile silently drops that agent from the registry — `validate_agent_definitions_resolved` cannot catch this because the missing definition never enters the registry to begin with. Wiring mistakes become cryptic `agent X not found` failures far from the cause.
- The entire `tool_validation.py` module (`AgentDefinitionValidator`, `AgentValidationResult`, `_AgentValidationInput`) is reachable from production code only as a re-export; every real caller is a single test. The `warnings` field is never populated. This is the largest single dead candidate.
- Several `AgentDefinition` fields (`skills`, `permissions`, `background`) have zero production readers; `AgentNotificationRule` keeps a `@runtime_checkable` decorator with no `isinstance` callers.

The follow-up `remove unused / legacy` pass has clear hooks in the **Legacy / Dead Candidates** section below.

## Critical Issues

### CR-01: Loader silently swallows ValidationError and bare Exception, making agent-profile typos invisible

**File:** `backend/src/agents/definition/loader.py:18-34`
**Issue:** `_load_agent_files` catches `ValidationError` and `Exception` and only logs at `DEBUG`. Default app logging is INFO+, so a frontmatter YAML typo, an unknown field with `extra="forbid"`, or any other validation failure produces zero observable output. The affected agent quietly disappears from the load result, never registers, and `validate_agent_definitions_resolved` cannot flag it (it iterates registered definitions, not expected ones). Downstream this surfaces as "agent X not found" at request time, with the actual root cause unreachable from logs.

This contradicts the design intent stated in `resolved_validation.py:16` ("so wiring mistakes surface before the first request").

**Fix:** Promote the log to `ERROR` at minimum, and either re-raise `ValidationError` or surface the failure as a fail-closed startup error. The bare `except Exception` should at minimum log at `ERROR` and re-raise — there is no legitimate "skip this file" scenario for a misformatted agent profile.

```python
def _load_agent_files(paths: Iterable[Path]) -> list[AgentDefinition]:
    agents: list[AgentDefinition] = []
    for path in sorted(paths):
        try:
            fm, body = parse_markdown_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            logger.error("Could not read agent definition %s", path, exc_info=True)
            raise
        data = dict(fm)
        data.setdefault("name", path.stem)
        description = str(data.get("description") or f"Agent: {data['name']}")
        data["description"] = description.replace("\\n", "\n")
        if body:
            data["system_prompt"] = body
        try:
            agents.append(AgentDefinition.model_validate(data))
        except ValidationError:
            logger.error("Invalid agent definition in %s", path, exc_info=True)
            raise
    return agents
```

## Warnings

### WR-01: `_coerce_positive_int` / `_coerce_bool` silently coerce invalid input

**File:** `backend/src/agents/definition/model.py:142-158`
**Issue:** A frontmatter typo `tool_call_limit: "ten"` does not fail validation — it silently becomes `None` (= unlimited). Similarly, any non-string truthy/falsy value passed to `background` is coerced via `bool(v)` without complaint. This is the same class of silent-failure as CR-01: malformed config produces wrong behavior with no signal.
**Fix:** Raise `ValueError` on non-integer / non-bool string input rather than coercing to a default. Pydantic will wrap this into a meaningful `ValidationError`. Alternatively, drop the custom validator entirely and rely on Pydantic's native type coercion + bounds (`Field(gt=0)` for `tool_call_limit`).

### WR-02: `loader.py:25-26` — `data['name']` lookup after `setdefault` is fragile against explicit `name: null`

**File:** `backend/src/agents/definition/loader.py:23-25`
**Issue:** `data.setdefault("name", path.stem)` is a no-op when frontmatter contains `name: null` (which YAML parses to `None`). The subsequent `data['name']` succeeds but produces a `description` of `"Agent: None"`, and Pydantic later rejects `name=None`. Combined with CR-01's silent-swallow, this is an obscure path to a dropped agent. Even after CR-01 is fixed, the error message would point at the wrong field.
**Fix:** Treat falsy `name` as missing:
```python
if not data.get("name"):
    data["name"] = path.stem
```

### WR-03: `description.replace("\\n", "\n")` — unexplained transform

**File:** `backend/src/agents/definition/loader.py:26`
**Issue:** The code converts the literal two-character sequence `\n` into a newline. There's no comment explaining why; greppable callers of the agent description don't suggest a downstream consumer that expects this. If this exists to support a YAML convention (single-line quoted strings with `\n` escapes), that should be inline-commented. If it's vestigial from a prior format, delete it.
**Fix:** Either add a single-line comment `# Profile YAML may encode multi-line descriptions as '\n'-escaped single-line strings.` or remove the line and rely on YAML block scalars.

### WR-04: `tool_validation.AgentDefinitionValidator` re-imports `tools` lazily on every call

**File:** `backend/src/agents/definition/tool_validation.py:45-54`
**Issue:** `_resolve_all_tool_names` does `from tools import collect_tool_catalog` inside the method body. The lazy import is presumably to dodge a circular import, but on every `.validate()` call it re-traverses the catalog (Python imports are cached, but the `collect_tool_catalog(...)` call rebuilds the entry list each invocation). Combined with WR-05 below (the module has one test caller), this is dead complexity. If kept, hoist the import to module scope or cache the result. If dropped per WR-05, the issue disappears.
**Fix:** See WR-05 — most direct fix is to delete the module. If retained, hoist the import.

## Info

### IN-01: `AgentValidationResult.warnings` field is never populated

**File:** `backend/src/agents/definition/tool_validation.py:23`
**Issue:** `warnings: list[str]` exists on the result model and is exported via `agents.__init__`, but no code ever writes to it. The `.validate()` method only appends to `errors`. The field is dead.
**Fix:** Remove `warnings` from `AgentValidationResult`, or implement a code path that uses it (e.g., warn on tools listed in both `allowed_tools` and `terminals`).

### IN-02: `_AgentValidationInput` Protocol is an unused indirection

**File:** `backend/src/agents/definition/tool_validation.py:13-18`
**Issue:** The Protocol exists so that `.validate()` accepts "any object with these three fields." The only call site (test) passes an actual `AgentDefinition`. Since `AgentDefinition` has these fields, the Protocol adds nothing.
**Fix:** Annotate `validate(self, defn: AgentDefinition)` directly. Forward-import via `TYPE_CHECKING` if circular-import is a concern.

### IN-03: `@runtime_checkable` on `AgentNotificationRule` is unused

**File:** `backend/src/agents/definition/model.py:18`
**Issue:** `runtime_checkable` is only meaningful when callers do `isinstance(rule, AgentNotificationRule)`. No code in the repo does. The Protocol is used purely as a type hint inside the Pydantic field `notification_rules: list[AgentNotificationRule]`, which doesn't need runtime-checkability.

Note: the Protocol itself is still needed because the field annotation can't be `list[NotificationRule]` directly — see `notification/rules/model.py:18-22` for the Pydantic forward-ref dodge. Do NOT remove the Protocol, only the decorator.
**Fix:** Drop `@runtime_checkable` and the `runtime_checkable` import.

### IN-04: `from __future__ import annotations` masks the Protocol→Pydantic forward-ref workaround

**File:** `backend/src/agents/definition/model.py:3`
**Issue:** Combined with WR-04/IN-02 above, this file's Protocol-instead-of-real-type workaround is poorly documented locally. The reason lives in another file (`notification/rules/model.py:18-22`). Future readers editing the model are likely to "simplify" by replacing the Protocol with `list[NotificationRule]` and discover the breakage at test time.
**Fix:** Add a one-line comment near `class AgentNotificationRule` referencing the forward-ref workaround in `notification/rules/model.py`. (No behavior change.)

---

## Legacy / Dead Candidates (hooks for `remove unused / legacy` follow-up)

These are not classified BLOCKER/WARNING because removing them is a deliberate design call, not a defect fix. Each entry includes evidence from grep across `backend/src` + `backend/tests`.

### LD-01: `tool_validation.py` module — production-dead

- **File:** `backend/src/agents/definition/tool_validation.py` (entire file)
- **Evidence:** Re-exported from `agents/__init__.py`. Outside the package, the only callers are tests:
  - `backend/tests/unit_test/test_tools/test_submission_tool_registration.py:53` — single call to `AgentDefinitionValidator(None).validate(...)`.
  - No `src/` consumer imports `AgentDefinitionValidator` or `AgentValidationResult`.
- **Implication:** This is a 58-line module sustained by one test. Remove the module, remove the test (or rewrite the test to assert error behavior on the registry's actual validation path), drop the two re-exports.

### LD-02: `AgentDefinition.skills` field — production-dead

- **File:** `backend/src/agents/definition/model.py:83`
- **Evidence:** `grep -rn "\.skills\b\|skills=" backend/src` returns zero `AgentDefinition.skills` reads outside the model. Production `tools/skills/` is unrelated (it's the skills toolkit, not agent.skills).
- **Implication:** Remove the field and the `"skills"` entry in the `_split_csv` validator's field list.

### LD-03: `AgentDefinition.permissions` field — production-dead

- **File:** `backend/src/agents/definition/model.py:93`
- **Evidence:** `grep -rn "\.permissions\b\|permissions=" backend/src` returns zero hits in non-agent source. Commented as "Python-specific" with no obvious consumer.
- **Implication:** Remove the field, remove from `_split_csv` validator's field list.

### LD-04: `AgentDefinition.background` field — production-dead

- **File:** `backend/src/agents/definition/model.py:86`
- **Evidence:** `grep -rn` of `agent_def.background`, `defn.background`, `definition.background` in `backend/src` returns zero hits. The unrelated `engine.background` module is the background-task manager, not this field.
- **Implication:** Remove the field; remove `_coerce_bool` validator (it's used only for this field).

### LD-05: `@runtime_checkable` decorator on `AgentNotificationRule`

- **File:** `backend/src/agents/definition/model.py:18`
- **Evidence:** No `isinstance(..., AgentNotificationRule)` anywhere in the repo.
- **Implication:** Drop the decorator and the `runtime_checkable` import. Keep the Protocol (still needed as type hint; see IN-04).

### LD-06: `AgentValidationResult.warnings` field

- **File:** `backend/src/agents/definition/tool_validation.py:23`
- **Evidence:** No writers anywhere. (Subsumed by LD-01 if the whole module is removed.)

### LD-07: `_AgentValidationInput` Protocol

- **File:** `backend/src/agents/definition/tool_validation.py:13-18`
- **Evidence:** Only callers pass full `AgentDefinition`. (Subsumed by LD-01 if the whole module is removed.)

### Not dead — keep

The following are sometimes mistaken for dead but have real production consumers; do not remove during the follow-up pass:
- `AgentDefinition.role` — read by `tools/submission/planner/_schemas.py:83` and the live_e2e squad runner.
- `AgentDefinition.model` — read by `engine/agent/factory.py:161`.
- `AgentDefinition.tool_call_limit` — read by `engine/agent/factory.py:334`.
- `AgentDefinition.agent_type` — read by `engine/agent/factory.py:171,331` and `tools/subagent/run_subagent.py:147`.
- `AgentDefinition.system_prompt` — read by `engine/agent/factory.py:287`.
- `AgentDefinition.allowed_tools` / `terminals` — read by `engine/agent/factory.py:216`.
- `AgentDefinition.notification_rules` — read by `engine/agent/factory.py:349`.
- `AgentDefinition.notification_triggers` — declared by all main-profile MDs and resolved by `notification/` (runtime-side).
- `AgentDefinition.context_recipe` / `variants` / `AgentSelectionBlock` / `AgentVariant` — used by `task_center/agent_launch/resolver.py` and `composer.py`.
- `AgentNotificationRule` Protocol body — needed as type hint to keep Pydantic happy with the cross-module forward reference (see IN-04).

---

_Reviewed: 2026-05-13_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
