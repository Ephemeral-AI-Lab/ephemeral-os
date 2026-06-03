# Rust parity audit — Tools framework + hooks + skills + registry/spec (agent-core)

Scope: the tool framework (`_framework/`), tool-specific hooks (`tools/_hooks/`),
the skills subsystem (`skills/`, `tools/skills/`), and the tool registry / spec
generation, mapped to the Rust `eos-tools` and `eos-skills` crates.

Source precedence: Python (`backend/src`) is behavioral ground truth; the Rust
crates are under audit. The `sandbox/` workspace is reached only through
`eos-protocol`/`eos-sandbox-api`, so state-dependent hooks (`block_in_isolated_mode`,
`require_no_inflight_background_tasks`) are traced across the host/client boundary.

## Ground truth

Docs (corroboration):
- `docs/architecture/tools/framework.html`, `tools/hooks.html`, `tools/skills.html`,
  `tools/index.html`

Python anchors:
- Tool abstraction / decorator: `backend/src/tools/_framework/core/base.py`,
  `core/decorator.py`
- Hook contract + validation: `core/hooks.py`
- Registry / factory / introspection: `core/registry.py`, `factory.py`,
  `introspection/catalog.py`, `introspection/schema_summary.py`
- Execution pipeline: `execution/tool_call.py`, `execution/hook_pipeline.py`,
  `core/validation.py`, `core/results.py`, `core/runtime.py`, `core/context.py`,
  `execution/trace.py`
- Batch-dispatch policy: `backend/src/engine/tool_call/dispatch.py`
- Wired hooks: `tools/_hooks/destructive_shell.py`, `advisor_approval.py`,
  `require_no_inflight_background_tasks.py`, `block_in_isolated_mode.py`,
  `disallow_nested_planner_deferral.py`
- Hook wiring sites: `tools/sandbox/exec_command/exec_command.py:44`,
  `tools/ask_helper/ask_advisor/ask_advisor.py:157`,
  `tools/isolated_workspace/{enter,exit}_isolated_workspace/definition.py:34`,
  `tools/submission/.../submit_{root,generator,reducer,planner}_outcome.py`
- Skills: `skills/core/{types,registry,loader}.py`, `skills/bundled/__init__.py`,
  `tools/skills/load_skill_reference.py`, `tools/skills/_factory.py`

## Rust mapping

| Concern | Python | Rust |
| --- | --- | --- |
| Tool name set | union of 6 `make_*`/registration sites + `_names.py` | `eos-tools/src/name.rs` (`ToolName::ALL`, 24) |
| Tool intent | `@tool(intent=…)` per tool | `eos-tools/src/meta.rs::tool_intent` |
| Terminal flag | `@tool(is_terminal_tool=…)` | `eos-tools/src/meta.rs::is_terminal` |
| Pre-hook chain | `@tool(pre_hooks=…)` per tool | `eos-tools/src/meta.rs::tool_hooks` |
| Registry | `core/registry.py::ToolRegistry` | `eos-tools/src/registry.rs::ToolRegistry` |
| Spec / API schema | `base.py::to_api_schema`, `registry.py::to_api_schema` | `eos-tools/src/spec.rs`, `registry.rs::specs`, snapshot `default_tool_specs.snap` |
| Execution pipeline | `execution/tool_call.py::execute_tool_once` | `eos-tools/src/execution.rs::execute_tool_once` |
| Input parse / output validate | `core/validation.py` | `execution.rs::{parse_input, validate_output}` |
| Hook framework | `core/hooks.py` + `execution/hook_pipeline.py` | `eos-tools/src/hooks.rs` (sealed enum) |
| Batch-dispatch policy | `engine/tool_call/dispatch.py` | `eos-tools/src/dispatch.rs` (pure predicates) |
| Result type | `core/results.py::ToolResult` | `eos-tools/src/result.rs::ToolResult` |
| Exec metadata | `core/runtime.py::ExecutionMetadata` | `eos-tools/src/metadata.rs::ExecutionMetadata` |
| Skills load/registry | `skills/core/*`, `skills/bundled` | `eos-skills/src/{loader,registry,bundled,definition}.rs` |
| `load_skill_reference` | `tools/skills/load_skill_reference.py` + `_factory.py` | `eos-tools/src/model_tools/skills.rs` |
| Downstream-state ports | implicit via context services | `eos-tools/src/ports.rs` (6 sealed traits) |

## Invariant table

| Invariant | Status | Severity | Python file:line | Rust file:line | Note |
| --- | --- | --- | --- | --- | --- |
| 1. Tool registry + spec generation parity | match | — | `core/registry.py:18-47`; `factory.py:103-122`; `base.py:65-73` | `registry.rs:33-72`; `model_tools/mod.rs:61-71`; `name.rs:78-103` | Builtin set = 24 names, byte-equal both sides. Registry replace-in-place, `restrict`/`remove`, insertion order all match. Spec *dialect* differs (D6). |
| 1a. `output_schema` emitted in spec | match | — | `base.py:65-73` (`output_schema` always present) | `spec.rs:28-46` (`text_spec` → `None`; `json_spec` → `Some`) | Python always emits `output_schema`; for `TextToolOutput` it is `RootModel[str]`. Rust omits `output_schema` for text tools (snapshot confirms). Behavioral effect nil (text = any string); see D6. |
| 2. Dispatch + execution pipeline (intent labeling, pre/post hooks) | partial | high | `execution/tool_call.py:139-200`; `dispatch.py:180-314` | `execution.rs:30-72`; `dispatch.rs:62-181` | Inner pipeline (parse → pre-hooks → execute → validate → stamp-terminal) matches. **Post-hook stage dropped** (D2). Batch + lifecycle predicates byte-exact. |
| 2a. Terminal stamping on success only | match | — | `tool_call.py:197-199` | `execution.rs:106-115` | `is_terminal=true` iff terminal tool AND not error. |
| 2b. `background` arg rejection | match | — | `validation.py:25-34` | `execution.rs:36-42` | Same in-band message; both reject before parse. |
| 2c. Input parse error message | match | — | `validation.py:35-47` | `execution.rs:124-134` | "Invalid input for X: … Please retry…". Internal/exotic-error split (D5). |
| 2d. Output validation (text vs JSON) | match | — | `validation.py:86-124` | `execution.rs:76-102` | Stamps `output_validation_error` metadata; JSON-decode failure → in-band error. |
| 2e. Intent labeling (read/write/lifecycle) | match | — | `@tool(intent=)` per tool; `dispatch.py:224-229` | `meta.rs:17-45`; `intent.rs:20-61` | Wire strings `read_only`/`write_allowed`/`lifecycle` match; per-tool classification verified. |
| 3. Hooks framework (Pre/Post tool-use) | partial | high | `core/hooks.py`; `hook_pipeline.py` | `hooks.rs` | Pre-hooks: 6 wired hooks ported 1:1. Post-hook machinery dropped (D2). `hook_failure` JSON shape + `hook_trace`/`effective_tool_input` match. |
| 3a. `destructive_git` + `destructive_shell` policy | match | — | `destructive_shell.py:13-193` | `hooks.rs:244-443` | Subcommand list (24), git option sets, `_GIT_CLEAN_SHORT_FLAGS="ndfxXqi"`, rm/mv target set, `apply --check`, both messages, `policy` tags — all byte-equal. `shlex` vs whitespace split (D4). |
| 3b. `advisor_approval` hook plumbing | partial | high | `advisor_approval.py:39-119` | `hooks.rs:574-597` | Plumbing/message/`policy` faithful; the 6-way conversation-scan classification is **unported** (stub port) (D1). |
| 3c. `require_no_inflight_background_tasks` | match | — | `require_no_inflight_background_tasks.py:42-129` | `hooks.rs:477-570` | `max(local, daemon)`, bailout fail-open (`daemon_unavailable_bailout`), `command_session_count_unavailable`, all `reason`/`count` tags match. |
| 3d. `block_in_isolated_mode` (fail-open) | match | — | `block_in_isolated_mode.py:32-76` | `hooks.rs:459-473` | No sandbox → pass; daemon error → fail-open pass; active → deny w/ `isolated_workspace_open`. |
| 3e. `disallow_nested_planner_deferral` | partial | low | `disallow_nested_planner_deferral.py:23-50` | `hooks.rs:602-625` | Deny-on-nested + `nested_workflow` reason match. Context-unavailable branch diverges (D3). |
| 3f. Hook chain ordering per tool | divergent | medium | submission `@tool` callsites | `meta.rs:58-88` | Planner/generator/reducer chains match. **Root gains `AdvisorApproval`** not present in Python (D1). |
| 3g. Hook-target validation | match | — | `core/hooks.py:102-117`; `decorator.py:76-80` | enforced structurally (`Hook` carries `tool`) | Python validates at decorator time; Rust ties hook→tool via the enum variant field. |
| 4. Skills loading / exposure parity | partial | high | `skills/bundled/__init__.py`; `tools/skills/load_skill_reference.py:52-80`; `_factory.py:40-88` | `eos-skills/src/{bundled,loader,registry}.rs`; `model_tools/skills.rs` | Loader/frontmatter/registry parity is exact. **Per-agent skill scoping dropped** (D7). |
| 4a. Skill loader (dir walk, refs, fallback) | match | — | `bundled/__init__.py:16-67` | `bundled.rs:27-119` | sorted dir walk, `references/*.md` by stem, frontmatter `name`/`description`, full-content fallback, 200-char truncation, `"Bundled skill: {name}"`. |
| 4b. Skill registry (register/get/list) | match | — | `core/registry.py:8-24` | `registry.rs:19-42` | Last-wins by name; `list_skills` name-sorted (BTreeMap). |
| 4c. `load_skill_registry` cwd ignored | match | — | `core/loader.py:11-17` | `loader.rs:26-51` | `cwd` dropped (Python `del cwd`); missing root → empty; non-dir root → error (Rust adds typed `RootNotDir`). |

## Disparities

### D1 — AdvisorApproval is a deny-all stub + root-gating divergence (high)

Two coupled gaps that together make the Rust root agent unable to complete under
default wiring.

**(a) The classification is unported.** Python `advisor_approval.py:66-89`
scans `context.conversation_messages` for the latest advisor result/`ask_advisor`
pair and classifies into six reasons — `missing`, `advisor_failed` (`is_error`),
`structural` (verdict not in `{approve,reject}`), `rejected`, `unpaired`
(no originating block), `wrong_tool` (`originating.input["tool_name"] != target`).
The Rust hook (`hooks.rs:574-597`) delegates this entirely to
`AdvisorPort::approval_status`. The only production implementor is the engine
stub `AdvisorService` (`eos-engine/src/notifications.rs:226-231`):

```rust
async fn approval_status(&self, _target_tool: &str) -> Result<AdvisorApproval, ToolError> {
    Ok(AdvisorApproval { approved: false, reason: Some("missing".to_owned()) })
}
```

It always denies with `"missing"`, ignoring the target tool and the conversation.
Grep confirms no `conversation_messages` / `helper_role` / verdict-scanning logic
exists anywhere in agent-core (`app_state.rs:605` is a test-only impl). The
`ask_advisor` runner (`AdvisorPort::review`) is likewise a stub
(`notifications.rs:216-224`), so an agent can never *obtain* an approving verdict.

**(b) Root is advisor-gated in Rust but not Python.** Python
`submit_root_outcome.py:42` wires only `RequireNoInflightBackgroundTasks` — no
advisor gate (the docstring in `advisor_approval.py:9-11` says helper/subagent
terminals omit it; root is also omitted). Rust `meta.rs:72-75` adds
`AdvisorApproval` to `SubmitRootOutcome` (the code comment flags this as a
deliberate EOS decision).

**Net behavioral consequence:** under the default `AdvisorService`, the Rust root
agent's `submit_root_outcome` is permanently denied with `"missing"` — it cannot
terminate. `eos-runtime/src/tests.rs:202-215` confirms the production stub denies
the root gate and an approving `AdvisorPort` is the prerequisite for root to
complete. Python root has no advisor gate and completes freely. **Why it matters:**
the default-wired Rust runtime cannot complete a root request; the gate is both an
unported behavior (the classification) and an added behavior (root). **Fix:** port
the conversation-scan classification into a real `AdvisorPort` impl in
`eos-engine`/`eos-runtime`, and reconcile the root-gating divergence with Python
(either drop the root hook or document it as an intentional product change with a
working advisor runner behind it).

### D2 — Post-hook execution stage dropped (high)

Python's pipeline runs a full **post-hook** stage after the body
(`hook_pipeline.py:110-188`, called from `tool_call.py:192`): each `post_hook`
may replace the `ToolResult`, the replacement is re-validated against
`output_model` (`hook_pipeline.py:160-179`), and failures produce the same
`hook_failure` shape as pre-hooks. The Rust pipeline omits this entirely
(`execution.rs:7-9`: "The unexercised post-hook stage is dropped"). The Rust
`Hook` enum has no post variant and `execute_tool_once` has no post-hook loop.

The migration rationale is correct *today*: grep shows **no** wired `post_hooks`
content — the only `post_hooks` reference outside the framework is
`tools/subagent/_factory.py:71,82`, which merely *copies and re-validates*
`run_subagent.post_hooks` (always `()`, a documented no-op shim). So the dropped
stage changes no current behavior. **Why it matters:** this is a silently removed
extension point. Any future Python post-hook (result rewriting, post-validation
denial) has no Rust seam and would be a hard re-architecture, not a wiring change.
The framework also loses the post-hook output re-validation path. **Fix:** acceptable
as a documented migration simplification; record it as a known capability gap so a
future post-hook need is caught early rather than discovered as "missing feature."

### D3 — `disallow_nested_planner_deferral` context-unavailable branch diverges (low)

Python (`disallow_nested_planner_deferral.py:38-41`): when a nonblank deferred
goal is set but the submission context cannot be resolved
(`AttemptSubmissionContextError`), the hook **fails** with that error message and
`policy=nested_planner_deferral`. Rust (`hooks.rs:614-616`): when
`workflow_id`/`workflow_control` are absent, the hook **passes**:

```rust
let (Some(workflow_id), Some(control)) = (&ctx.workflow_id, &ctx.workflow_control) else {
    return Ok(HookOutcome::pass());
};
```

The Rust comment ("passes when nesting cannot be determined; the orchestrator
still enforces on apply") suggests the apply-time check is the real gate, so this
is fail-open vs Python's fail-closed-with-message in the missing-context case.
**Why it matters:** a planner deferral submitted without a resolvable workflow
context is silently allowed past the hook in Rust where Python would reject it at
the hook; the downstream `PlanSubmissionPort::apply_plan` is expected to re-check.
**Fix:** confirm the orchestrator's apply-time nested-deferral rejection exists in
`eos-workflow`; if so this is a deliberate boundary shift, otherwise tighten the
hook to deny on a present-deferred-goal + unresolved-context.

### D4 — git-arg splitting uses whitespace split, not shlex (low)

Python `_split_git_args` (`destructive_shell.py:130-134`) prefers `shlex.split`
and falls back to `str.split` only on `ValueError`. Rust `split_git_args`
(`hooks.rs:336-338`) always uses `split_whitespace()` (the fallback path only).
For quoted git arguments (`git -c "user.name=a b" commit`) the token boundaries
differ, which can change which token is read as the subcommand. **Why it matters:**
a crafted quoted command could evade or trip the git-mutation classifier
differently than Python. The Rust message and Python docstring both explicitly state
the prehook is *not* the authoritative isolation boundary (the sandbox write/commit
audit is), so impact is bounded. **Fix:** use a shell-lexer crate for parity, or
accept as documented best-effort divergence.

### D5 — input-parse internal-vs-validation error split collapsed (low)

Python `parse_tool_input` (`validation.py:35-62`) distinguishes a pydantic
`ValidationError` ("Invalid input for X: … Please retry…") from any other
exception ("Internal validation error for X: {type}: {msg}", logged, no "retry"
advice — to keep triage from confusing internal faults with bad args). Rust
`parse_input` (`execution.rs:124-134`) renders only the single "Invalid input …
Please retry" message for every serde failure. **Why it matters:** an internal
deserialization fault in Rust is surfaced to the model as a retryable
bad-arguments error, losing the Python triage signal. Low impact (the typed DTOs
make exotic failures rare). **Fix:** optional — split serde data-errors from
non-object/structural errors if the triage signal is valued.

### D6 — spec JSON-Schema dialect differs from Python (low)

The Rust `default_tool_specs.snap` is self-consistent (24 tools, `name` /
`description` / `input_schema` / `output_schema`), but the schemas are
`schemars` draft-07: they carry `$schema: ".../draft-07/schema#"`, `title`,
`format: "uint32"`, and float `minimum` (`1.0`). Python `to_api_schema`
(`base.py:65-73`) emits pydantic v2 schemas (2020-12 dialect, no `$schema`/`format:
uint32`, integer bounds). Additionally Python always emits `output_schema` (even
`RootModel[str]` for text tools) whereas Rust omits it for `OutputShape::Text`
(`spec.rs:28-30`). **Why it matters:** the wire schemas presented to the model are
not byte-identical across the port; for strict schema validators or schema-diff
tooling this is a visible difference. The *contract* (required fields, property
names, enums incl. the `run_subagent` `agent_name` enum patch in
`spec.rs:54-74`) is behaviorally equivalent. **Fix:** none required unless byte
parity of the emitted schema is a goal; record as an intentional tooling
difference.

### D7 — per-agent skill scoping dropped: any agent reads any skill's references (high)

Python scopes `load_skill_reference` to the spawning agent's *own* skill folder.
`make_load_skill_reference_from_context` (`tools/skills/_factory.py:68-88`) reads
`ctx.metadata["agent_name"]`, looks up `AgentDefinition.skill`, and passes
`allowed_slugs=[skill_slug]` into `make_load_skill_reference_for_skill`
(`_factory.py:40-65`), which builds the `available` allowlist from *only* that
slug. The tool body (`load_skill_reference.py:52-61`) rejects any
`skill_name not in available` and the error path lists only `available.keys()`.
This is the load-bearing "an agent reads only its own skill's references"
invariant (`factory.py:86-98` docstring).

Rust `LoadSkillReference` (`model_tools/skills.rs:47-64`) has **no allowlist**: it
calls `ctx.skill_registry.get()` over the whole registry and, on miss, lists
`ctx.skill_registry.list_skills()` — *all* bundled skills. The registry is a
single process-global (`eos-runtime/src/app_state.rs:426-477` builds one
`skill_registry`; `tool_context.rs:79` clones the same `Arc` into every tool
context regardless of `agent_name`). There is no per-agent scoping anywhere.

**Why it matters:** in Rust any agent that has `load_skill_reference` can read any
other skill's reference documents, and the not-found error leaks the names of all
bundled skills. The Python authorization boundary (scope to the agent's declared
skill) is silently absent. **Fix:** build the per-agent `available`/allowlist from
the bound agent's `AgentDefinition.skill` (thread the scoped slug into
`ExecutionMetadata` or build a per-agent scoped view), mirroring
`make_load_skill_reference_from_context`.

### D8 — pre-hook `hook_trace`/`effective_tool_input` not stamped on success (low)

Distinct from D2 (the post-hook *seam*); this is the pre-hook *trace on the
successful path*. Python accumulates a `status:"pass"` trace entry per passing
pre-hook (`hook_pipeline.py:101-107`), and on the success path
`tool_call.py:196` → `finalize_result` (`hook_pipeline.py:190-193`) →
`_metadata_with_hook_details` (`hook_pipeline.py:253-260`) stamps `hook_trace`
(when non-empty) plus `effective_tool_input` onto the successful `ToolResult`
metadata. So any tool with pre-hooks that *pass* (e.g. `exec_command` clearing
both destructive checks, any `submit_*` clearing the no-inflight gate) carries a
`hook_trace` audit breadcrumb on its successful result.

Rust accumulates `hook_trace` in the loop (`execution.rs:47-62`) but only
consumes it in `hook_failure_result` on a deny; the all-pass path
(`execution.rs:64-71`) discards it — the successful `ToolResult` gets no
`hook_trace` and no `effective_tool_input`. **Why it matters:** observability
loss on successful gated calls; the per-hook pass breadcrumb is gone from audit
metadata. Blast radius is bounded: Rust `HookOutcome::Pass` cannot replace input,
so `effective_tool_input` would equal raw input regardless (Python's
`_trace_input_from_result` readback at `tool_call.py:131-136` is functionally
equivalent), and tools with no pre-hooks match (Python's trace is empty too). The
real gap is the `hook_trace` stamp. **Fix:** on the success path, merge the
accumulated `hook_trace` (and, if a future hook can replace input,
`effective_tool_input`) into the returned `ToolResult.metadata`.

## Extra findings

- **`ExecutionMetadata` mapping shim intentionally dropped (match-by-design).**
  Python `runtime.py` is a `Mapping`-emulating bag (`get`/`__getitem__`/`__iter__`/
  `extras`/`_TYPED_FIELDS`) for incremental migration; Rust `metadata.rs:1-17`
  drops it for a typed struct with injected port traits. Documented, and the
  dropped `tool_registry` field (skills introspecting siblings) is unused by the
  ported tools. No behavioral loss for the audited surface.
- **`conversation_messages` removed from tool context (related to D1).**
  Python threads `conversation_messages` into hooks (`tool_call.py:100-101`;
  consumed by `advisor_approval.py:56`). Rust `ExecutionMetadata` drops it
  (`metadata.rs:14-17` lists it as "engine plumbing"), which is *why* the advisor
  classification had to move behind `AdvisorPort` — and why it is currently a stub
  (D1). Consistent with the boundary design but the consumer is unimplemented.
- **`task_type` discriminator dropped.** Python `BaseTool.task_type`
  (`base.py:39`, default `"agent"`) is a monitoring/UI/audit field set by the
  decorator. No equivalent on Rust `RegisteredTool` (`executor.rs:38-53`). It is
  not read by any audited dispatch/exec logic, so low/no impact, but note it for
  audit/UI parity work.
- **`short_description` dropped.** Python `BaseTool.short_description`
  (`base.py:29`) is carried per tool; Rust `ToolSpec`/`RegisteredTool` has no
  short-description field. Not consumed by the model-facing schema in either
  (`to_api_schema` ignores it), so cosmetic.
- **Per-response tool trace (`trace.py`) lives in engine, not eos-tools.**
  Python `record_tool_trace` (counters for `read_file`/`exec_command`/
  `read_file_note`, `_TOOL_TRACE_LIMIT=64`) runs after a successful call
  (`tool_call.py:112-119`). It is engine-side bookkeeping (out of this crate's
  scope); confirm it is ported in the engine area, not assumed here.
- **Lifecycle-batch telemetry counters not in `dispatch.rs`.** Python
  `dispatch.py:49-69,280-308` maintains `_LIFECYCLE_BATCH_REJECTION_COUNTERS` and
  emits `emit_lifecycle_batch_rejected` audit events. The Rust `dispatch.rs`
  predicates return only the decision (rejected/dispatched); the counter/audit
  emission is the engine consumer's job (the Rust module docstring says the loop
  lives in `eos-engine`). Verify the engine emits these — the pure predicate is
  correct but the telemetry side-effect must exist somewhere.
- **`RestrictedRunSubagentTool` → spec enum patch (match).** Python narrows
  `run_subagent`'s `agent_name` schema to the caller's dispatchable list
  (`subagent/_factory.py:19-50`). Rust reproduces this via
  `spec.rs::text_spec_with_agent_enum` + `CallerScope.dispatchable_subagents`
  (`model_tools/mod.rs:29-32`), asserted in the snapshot test
  (`mod.rs:135-140`). Good parity on a subtle per-caller schema mutation.

## Open questions

1. **D1 reconciliation:** is the root-`AdvisorApproval` gate an accepted product
   change, and is a real `AdvisorPort` (with the 6-way classification + an
   `ask_advisor` runner) planned for `eos-runtime`/`eos-engine` before this ships?
   As wired today the default Rust root agent cannot terminate.
2. **D7:** is per-agent skill scoping intended to be enforced at the tool layer
   (as Python does) or deferred to a per-agent registry built elsewhere? No such
   per-agent registry construction exists in `eos-runtime` today. Note the
   per-caller seam already exists — `build_default_registry(&CallerScope)`
   (`mod.rs:61`) carries `dispatchable_subagents` but no skill slug — so skill
   scoping was omittable from an existing mechanism, not architecturally blocked.
   Separately, Python gates *who gets* `load_skill_reference` via `allowed_tools`
   (`factory.py:86-98`); Rust registers it unconditionally in
   `build_default_registry`. Whether `registry.restrict` strips it per-agent for
   skill-less agents is unverified.
3. **D3:** does `eos-workflow`'s `apply_plan` reject a nested-workflow deferred
   goal, making the hook's fail-open in the unresolved-context case safe?
4. **D2:** is the post-hook stage permanently out of scope, or a deferred seam?
   If permanent, the `tools/subagent/_factory.py` post-hook copy/validation shim
   in Python is also dead and can be noted for the Python side.
5. **Telemetry:** are the lifecycle-batch rejection counters and
   `emit_lifecycle_batch_rejected` audit events ported to the engine consumer of
   `dispatch.rs`?
