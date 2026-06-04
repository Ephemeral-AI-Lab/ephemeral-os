# Tools-framework Rust parity remediation plan (PLAN ONLY)

> Phase 2 (correctness / data-safety), area **tools_framework**.
> Report rows: `REPORT.md` Phase 2 lines 494–495 (D7 HIGH, NF1 MED); invariant
> table rows 121 (D7) + 123 (NF1); `areas/tools_framework.md` §D7; `areas/tools_framework.verify.md` NF1.
> Ground truth: Python `backend/src/tools/skills/_factory.py` (`make_load_skill_reference_from_context`/`_for_skill`),
> `backend/src/tools/skills/load_skill_reference.py`, `backend/src/tools/_framework/execution/tool_call.py`.
> Rust under audit: `agent-core/crates/eos-tools`, `agent-core/crates/eos-engine`.
> Anchors are current `main`; agent-core files are edited concurrently, so line numbers are approximate (`~`) — function/type names are authoritative.

## 0. The two issues in one paragraph

The Phase-2 `tools_framework` lane has exactly two open rows. **D7 (HIGH, authorization):**
Python scopes `load_skill_reference` to the *spawning agent's own* skill folder — an
agent can read only its own skill's `references/*.md`, and a not-found error lists
only that one skill. The Rust port dropped the scope entirely: `LoadSkillReference`
reads the **process-global** skill registry, lets any agent serve any skill's
references, and on a miss leaks the names of **all** bundled skills. **NF1 (MEDIUM,
investigator_missed):** Python parses tool input *then* runs pre-hooks on the
validated model; Rust runs pre-hooks on the **raw JSON** then parses inside the
executor body. Hooks see pre-default input, and a hook-deny now precedes a
parse-error (precedence flip). The fixes are independent and land in separate
commits with a failing→passing reproduction each.

---

# Part A — D7 (HIGH): per-agent skill scoping

## A1. Root cause: the executor reads the whole registry, no allowlist

Python builds a **per-agent tool instance** with a captured `available` allowlist:

- `make_load_skill_reference_from_context` (`_factory.py:68-88`) reads
  `ctx.metadata["agent_name"]`, looks up `AgentDefinition.skill`, and derives the
  slug `defn.skill.parent.name` (the skill *folder* name).
- `make_load_skill_reference_for_skill` (`_factory.py:40-65`) builds `available`
  from **only** that slug: `for slug in [skill_slug]: registry.get(slug) → available[skill.name] = {...}`.
- The tool body (`load_skill_reference.py:52-61`) rejects any `skill_name not in available`
  and the error lists only `available.keys()` — the agent's own skill.
- An agent with no `skill:` declared gets `skill_slug=None` → empty `available` →
  a no-op tool that errors on every call (`_factory.py:46-49,82-88`).

Rust `LoadSkillReference` (`model_tools/skills.rs:47-64`) has **no allowlist**. It
builds `available` from `ctx.skill_registry.list_skills()` (the whole registry) and
looks up `ctx.skill_registry.get(name)` unscoped. The registry is a single
process-global `Arc<SkillRegistry>` cloned into every `ExecutionMetadata`
regardless of `agent_name` (`metadata.rs:75-76`; built once at `app_state.rs ~430`).
The module doc-comment (`skills.rs:1-3`) even *claims* "per-agent `SkillRegistry`"
— but nothing scopes it.

**Consequence:** any agent holding `load_skill_reference` reads any other skill's
reference docs (a real authorization-boundary regression), and the not-found error
enumerates every bundled skill.

## A2. Design decision: scope via the existing `CallerScope` seam (no new port, no metadata field)

The per-caller registry seam **already exists**: `build_default_registry(&CallerScope)`
(`model_tools/mod.rs:61`) is called *per agent spawn* at `agent_loop.rs:149`, and
`CallerScope` (`mod.rs:28-32`) already carries `dispatchable_subagents`, which
patches the `run_subagent` schema per caller (`subagent.rs:172,180`). The report's
open-question #2 names this exact seam: "skill scoping was omittable from an
existing mechanism, not architecturally blocked." We thread the skill scope through
the **same** seam, mirroring `subagent::register(registry, caller)`.

| Option | New field | New port | Engine touch | Verdict |
|---|---|---|---|---|
| **`CallerScope.skill_slug` → executor allowlist** ✅ | one `Option<String>` on `CallerScope` | none | populate at the existing `agent_loop.rs:141` `CallerScope { … }` | **recommended** — reuses the per-caller seam, near-verbatim port of `_for_skill` |
| New `ExecutionMetadata.skill_scope` field | one field on the per-call bag | none | every metadata builder | over-threaded — scope is per-spawn (registry), not per-call |
| New `SkillScopePort` trait | — | +1 sealed trait | wire a port impl | over-built — no downstream state to inject; the registry is already in `ctx` |

**Recommendation:** extend `CallerScope` with `skill_slug: Option<String>` and bake
the allowlist into a stateful `LoadSkillReference { allowed_slugs: Vec<SkillName> }`,
exactly as Python bakes `available` into the closure at factory time. The executor
keeps using `ctx.skill_registry` for the slug→skill→`references` lookup (same
registry it holds today), so the only behavior change is the **allowlist gate**.

### Slug-vs-name fidelity (the one correctness nuance)

Both registries key skills by frontmatter `name`, falling back to the folder name
(`eos-skills/bundled.rs:40-43` `parse_skill_metadata(dir_name(skill_dir), …)`;
Python `_parse_skill_metadata(skill_dir.name, …)`). Python's scope derivation uses
the **folder name** (`defn.skill.parent.name`) and looks it up by that slug. A
faithful port therefore derives the slug from `agent.skill`'s parent-folder name and
resolves it through `registry.get(slug)` inside the executor — reproducing Python's
exact mapping (slug → `skill` → `available[skill.name]`), including the existing
edge case where a folder name that differs from the frontmatter `name` yields a
no-op tool. The tool's `skill_name` input continues to be matched against
`available` (the resolved `skill.name`), unchanged.

## A3. The flow after the change

```
agent_loop.rs:141  CallerScope {
                      dispatchable_subagents: …,                 (unchanged)
                      skill_slug: agent.skill                    ← NEW
                        .as_deref()
                        .and_then(|p| p.parent()?.file_name())
                        .map(|s| s.to_string_lossy().into_owned()),
                    }
        ▼  build_default_registry(&caller_scope)
skills::register(&mut registry, caller)                          ← takes caller (like subagent::register)
        ▼  LoadSkillReference { allowed_slugs: caller.skill_slug → SkillName, 0-or-1 }
LoadSkillReference::execute(input, ctx)
        │  available = allowed_slugs.iter()
        │     .filter_map(|slug| ctx.skill_registry.get(slug))   ← scoped, not list_skills()
        │     .map(|s| s.name).collect()
        │  if parsed.skill_name ∉ available → error{ available }  ← lists only the agent's skill
        │  else serve from that skill's references                (unchanged)
        ▼
```

`app_state.rs ~430` keeps `CallerScope::default()` → `skill_slug: None` → empty
allowlist → no-op tool (matches Python's no-skill-declared agent).

## A4. The changes (diff table)

| # | File (crate) | Δ | What changes | Why |
|---|---|---|---|---|
| 1 | `eos-tools/model_tools/mod.rs` | ✚ | Add `pub skill_slug: Option<String>` to `CallerScope` (`~28-32`); pass `caller` into `skills::register` at `build_default_registry` (`~69`) | The per-caller scope carrier + wiring, mirroring `subagent::register(&mut registry, caller)`. |
| 2 | `eos-tools/model_tools/skills.rs` | ✎ | `LoadSkillReference` becomes `{ allowed_slugs: Vec<SkillName> }`; `register(registry, caller)` resolves `caller.skill_slug` → `SkillName` (0-or-1). In `execute`, build `available` from `allowed_slugs.filter_map(|s| ctx.skill_registry.get(s)).map(skill.name)` instead of `list_skills()`; gate `skill_name ∈ available` before serving | The allowlist gate — the verbatim port of `_factory.py:40-65` + `load_skill_reference.py:52-61`. |
| 3 | `eos-engine/agent_loop.rs` | ✎ | At `CallerScope { … }` (`~141`), set `skill_slug` from `agent.skill`'s parent-folder name (read before `agent` is moved into `build_query_context` at `~152`) | The per-spawn wiring — the analog of Python `make_load_skill_reference_from_context` reading `AgentDefinition.skill`. |
| 4 | `eos-tools/model_tools/skills.rs` `#[cfg(test)]` | ✚ | Add scoping tests (see A6); the existing module note (`skills.rs:106-111`) about `#[non_exhaustive] SkillDefinition` means tests inject a small in-crate `SkillRegistry` built via `register` + a test `SkillDefinition` constructor, or assert at the engine layer if the type cannot be built downstream | Prove A read-isolation + the scoped not-found list. |
| 5 | `eos-tools/model_tools/skills.rs` | ✎ | Update the module doc-comment (`~1-3`) to state the scope is now real (per-agent allowlist), not aspirational | The comment currently lies; align it with behavior. |

**Net:** 0 new files, 0 new ports, 0 new `ExecutionMetadata` fields; one
`Option<String>` on `CallerScope`; `LoadSkillReference` gains a `Vec<SkillName>`
field. `subagent.rs` is the template for every signature change.

## A5. What stays as-is (do not change)

- `ExecutionMetadata.skill_registry` stays the process-global `Arc<SkillRegistry>`
  (the *content* source is shared; only the *allowlist* is per-agent — exactly Python).
- `eos-skills` entirely (`SkillRegistry`, `bundled`, `loader`, `definition`).
- The `load_skill_reference` Input/Output DTOs, description, spec, and the
  reference-not-found branch (`references.get` + `available_references`).
- `dispatchable_subagents` and the `run_subagent` enum patch.

## A6. Verification (success criteria) — D7

1. **Read-isolation:** an agent scoped to skill `a` calling `load_skill_reference{skill_name:"b", …}`
   gets an `is_error` result whose `available` lists **only `a`** (not `b`, not all skills).
2. **Own-skill still works:** the same agent reading `a`'s real reference returns the content.
3. **No-skill agent:** `CallerScope::default()` → every call errors with an **empty** `available`.
4. **Not-found no longer leaks:** the miss branch never calls `list_skills()` (grep: no
   `list_skills` in `skills.rs` execute path).
5. `cargo test -p eos-tools` + `cargo clippy -p eos-tools --all-targets` clean; the
   `specs_snapshot` test is unaffected (scope is execution-time, not schema-time).

---

# Part B — NF1 (MEDIUM): inner pipeline order inverted

## B1. Root cause

Python `execute_tool_once` (`tool_call.py`): **parse first** (`:157`
`parse_tool_input`), **then** `run_pre_hooks(parsed.args)` (`:163`) on the validated
model, then `execute_tool_body(parsed_input)` (`:187`). Hooks observe the
validated, default-applied input (`parsed_input.model_dump(mode="json")`).

Rust `execute_tool_once` (`execution.rs:30-57`): `run_pre_hooks(tool, raw_input, ctx)`
(`:45`) runs on the **raw `JsonObject`**, then `tool.executor().execute(raw_input, ctx)`
(`:50`) parses internally via each executor's `parse_input` (`execution.rs:151`).
Two observable differences:

- **Hooks see raw (pre-default) input.** A pre-hook reading a field that has a serde
  default sees it *absent* in Rust, *present* in Python.
- **Precedence flip.** For an input that is *both* malformed *and* hook-denied, Rust
  returns the **hook deny**; Python returns the **parse error**.

Today this is **behaviorally inert**: every wired pre-hook
(`destructive_shell`, `block_in_isolated_mode`, `require_no_inflight_background_tasks`,
`advisor_approval`, `disallow_nested_planner_deferral`) reads **required** fields
(`cmd`, `tool_name`) with no serde defaults, so raw == validated for them; and no
wired hook inspects a defaulted field.

## B2. Design fork — DECISION REQUIRED

| Option | Cost | Faithfulness | Risk |
|---|---|---|---|
| **B-doc: document the seam + add a defaulted-field hook test** ✅ (recommended) | ~15 LOC + 1 test + doc | Behavior-equivalent for all wired hooks; codifies the boundary | minimal — pins the inert deviation so a *future* defaulted-field hook is caught |
| B-reorder: parse-before-hooks generically | new executor seam | byte-faithful order | the pipeline is generic over `JsonObject`; a true reorder needs a per-tool "normalize input → default-applied JSON" step (round-trip through the DTO) before hooks — a real re-architecture for an inert difference |

**Recommendation: B-doc.** The Rust pipeline is deliberately generic over
`&JsonObject` (one `execute_tool_once` for 24 tools; each executor owns its DTO).
A faithful reorder would require materializing the validated model *before* the
generic hook loop — i.e. a new `normalize_input` seam on `RegisteredTool` that
round-trips raw→DTO→JSON to apply defaults — which is disproportionate to an
effect that is inert across every wired hook. The report itself offers the doc path
as the accepted alternative ("...or document the seam + add a defaulted-field hook
test"). If the user wants byte-exact precedence parity regardless, take B-reorder.

## B3. The changes (B-doc)

| # | File (crate) | Δ | What changes | Why |
|---|---|---|---|---|
| 1 | `eos-tools/execution.rs` | ✎ | Extend the module/`execute_tool_once` doc (`~1-9,21-29,44`) to state explicitly: pre-hooks run on **raw** input before parse; hooks observe pre-default fields; a hook-deny precedes a parse-error. Justify (all wired hooks read required fields) | Make the intentional seam discoverable, not a silent divergence. |
| 2 | `eos-tools/execution.rs` `#[cfg(test)]` | ✚ | Add a test: a tool with a defaulted DTO field + a pre-hook that inspects that field — assert the hook sees the **raw** (absent/pre-default) value, locking the documented contract | Turns the inert deviation into a guarded, intentional one. |

## B4. Verification (success criteria) — NF1

1. New test demonstrates a pre-hook observing raw (pre-default) input — green and
   asserting the documented behavior.
2. Doc comment names the seam (order + precedence) at `execute_tool_once`.
3. `cargo test -p eos-tools` + clippy clean.
4. (If B-reorder is chosen instead) a malformed-and-denied input returns the
   **parse error**, and a hook sees the default-applied value.

---

## C. Coordination / sequencing

- **Independent of each other and of other Phase-2 lanes.** D7 = `eos-tools`
  (skills.rs + mod.rs) + one engine line (`agent_loop.rs`). NF1 = `eos-tools`
  (execution.rs) only. No edge to `sandbox_tools`/`occ`/`model_provider_prompt`/
  `workflow_lifecycle`/`deferred_goal_depth`.
- Land in two commits (D7 first — it carries the severity). After each is green,
  flip the matching `REPORT.md`/`REPORT.html` Phase-2 row `☐ → ☑` and update the
  `areas/tools_framework.md` D7 / `.verify.md` NF1 status lines.

## D. Open questions

1. **D7 gating of *who gets* the tool:** Python also gates *access* to
   `load_skill_reference` via `allowed_tools` (`factory.py:86-98`); Rust registers
   it unconditionally in `build_default_registry`. This plan fixes the
   *reference-scoping* leak (the HIGH row). Whether `registry.restrict` strips the
   tool per-agent for skill-less agents is a **separate** parity item
   (`areas/tools_framework.md` open-question #2) — out of scope here; the no-op
   tool (empty allowlist) is the safe interim behavior and matches Python's
   no-skill case.
2. **NF1 fork:** confirm B-doc (recommended) vs B-reorder before implementing Part B.

## E. Implementation status (this session)

**Landed (D7 + NF1, B-doc path):**
- `CallerScope` gains `skill_slug: Option<String>` (`model_tools/mod.rs`); `skills::register` now takes `(registry, config, caller)` and forwards the caller scope.
- `LoadSkillReference` holds a `Vec<SkillName>` allowlist; `execute` builds `available` from the scoped skill(s) and **gates `skill_name ∈ available` before serving** — a faithful port of `load_skill_reference.py:52-82`. The unscoped `list_skills()` read is gone (`model_tools/skills.rs`).
- `agent_loop.rs` populates `skill_slug` from `agent.skill`'s parent-folder name.
- D7 read-isolation tests added (`scoped_agent_cannot_read_other_skill`, `…reads_own_reference`, `skill_less_agent_has_empty_allowlist`, `register_scopes_to_caller_skill_slug`).
- NF1: the pre-hook input-order seam is documented in `execution.rs` (module doc + step-2 comment) and locked by `pre_hook_denies_before_parse`.

**Integration note — concurrent `ToolConfigSet` migration.** A parallel agent is mid-migration externalizing tool config/prose to `.eos-agents/tools/*.md` (`config.rs` + `ToolConfigSet`), which deleted `meta.rs` and the inline `descriptions/*.md`, and changed `register_tool` to a 6-arg form taking `&ToolConfig` and `build_default_registry(config, caller)`. My D7 edits were brought in line with this new plumbing (description sourced from `config.get(LoadSkillReference).description`; the inline const removed). The `.eos-agents/tools/load_skill_reference.md` body is byte-identical to the prior inline const, so the served description is unchanged.

**Verification status — D7 + NF1 GREEN at the `eos-tools` unit level.** Once the migration's sibling sweep landed, `cargo build -p eos-tools --tests` compiles. Results:
- D7: `scoped_agent_cannot_read_other_skill`, `scoped_agent_reads_own_reference`, `skill_less_agent_has_empty_allowlist`, `register_scopes_to_caller_skill_slug` — **4/4 pass.**
- NF1: `pre_hook_denies_before_parse` — **passes.**
- `cargo clippy -p eos-tools --all-targets` — **no warnings attributed to `skills.rs`/`execution.rs`** (test module carries the standard `#![allow(clippy::unwrap_used)]`).

**Two remaining non-green items, both owned by the concurrent migration (not these changes):**
1. `model_tools::tests::specs_snapshot` fails on **`run_subagent`** (trailing `\n` dropped) because the migration now sources descriptions from `.eos-agents/tools/*.md` and `parse_tool_config` end-trims them. `load_skill_reference` is **not** in the diff. The migration author owns blessing/fixing this snapshot.
2. `eos-engine` (`agent_loop.rs:157`) and `eos-runtime` (`app_state.rs:430`) callers of `build_default_registry` still pass one arg; the new signature needs a `&ToolConfigSet` the migration has not yet threaded into those crates. This blocks compile-verifying the `agent_loop.rs` `skill_slug` wiring; the composition wiring is the migration author's architectural decision, not part of this fix.

`REPORT.md` Phase-2 rows 494/495 stay `☐` until the whole workspace is green (snapshot blessed + cross-crate `build_default_registry` wiring landed); the D7/NF1 behavior itself is already proven by the passing unit tests above.
