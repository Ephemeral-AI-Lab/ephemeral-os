# Independent verification — Tools framework + hooks + skills + registry/spec (agent-core)

Verifier note: every verdict below was re-derived by opening both sides. Python
(`backend/src`) is ground truth. The primary mandate was hunting FALSE MATCHES
(claimed match, Rust actually differs); one was found and is flagged loudly
(NF1, parse/pre-hook ordering).

## Invariant verdict table

| Invariant | Independent status | Severity | Decisive bilateral anchor |
| --- | --- | --- | --- |
| 1. Tool registry + spec generation parity | confirmed_match | — | Py `registry.py:18-47` (dict assign / insertion order; `remove_tools`/`restrict_to_tools` preserve order) vs Rust `registry.rs:33-66` (`register` replace-in-place; `retain`-based `remove`/`restrict`). 24-name set: Rust `name.rs:78-103` (`ToolName::ALL` len 24); snapshot has 24 `"name":` entries. |
| 1a. `output_schema` emitted in spec | investigator_overstated | low | Py `base.py:72` emits `output_schema` **unconditionally** (incl. text tools via `output_schema()`); Rust `spec.rs:28-30` `text_spec` passes `None`. Snapshot: text tools have no `output_schema` key, JSON tools do. Real wire difference; behaviorally inert (text = any string). Investigation called this "match"; it is a (low) disparity already captured under D6 — the row label overstates. |
| 2. Dispatch + execution pipeline (intent labeling, pre/post hooks) | confirmed_disparity | high | Post-hook stage dropped (D2, confirmed). **Pipeline ORDER inverted (NF1, investigator_missed):** Py parses then runs hooks on the parsed model (`tool_call.py:157,163,187`); Rust runs hooks on raw JSON then parses inside the executor (`execution.rs:48,65`; `submission.rs:106,351`; `skills.rs:42`). Batch + lifecycle predicates byte-exact (confirmed). |
| 2a. Terminal stamping on success only | confirmed_match | — | Py `tool_call.py:197-198` (`is_terminal_tool and not final.is_error`) vs Rust `execution.rs:106-115` (`tool.is_terminal && !result.is_error`). |
| 2b. `background` arg rejection | confirmed_match | — | Py `validation.py:25-34` (reject iff `background ∉ model_fields`) vs Rust `execution.rs:36-42` (reject unconditionally). Behaviorally equal: no in-scope DTO declares a `background` field (grep of `model_tools/*.rs` found none). Same message. |
| 2c. Input parse error message | confirmed_match | — | Py `validation.py:35-47` vs Rust `execution.rs:124-134`: "Invalid input for X: … Please retry the tool call with valid arguments." Internal-vs-validation split collapsed in Rust (D5, confirmed low). |
| 2d. Output validation (text vs JSON) | confirmed_match | — | Py `validation.py:86-124` vs Rust `execution.rs:76-102`: text passes; JSON-decode/shape failure → in-band error stamping `output_validation_error`. |
| 2e. Intent labeling (read/write/lifecycle) | confirmed_match | — | Per-tool `@tool(intent=)` + `dispatch.py:224-229` vs Rust `meta.rs:17-45` + `dispatch.rs:43-53`. Wire strings and per-tool classification align (e.g. `enter/exit_isolated_workspace`, `delegate/cancel_workflow` = lifecycle). |
| 3. Hooks framework (Pre/Post tool-use) | confirmed_disparity | high | 6 pre-hooks ported 1:1 (`hooks.rs` enum); `hook_failure` JSON shape byte-equal (`hook_pipeline.py:301-322` vs `hooks.rs:193-238`). Post-hook machinery dropped (D2). |
| 3a. `destructive_git` + `destructive_shell` policy | confirmed_match | — | Py `destructive_shell.py:13-193` vs Rust `hooks.rs:244-443`: subcommand set (24), option sets, `_GIT_CLEAN_SHORT_FLAGS="ndfxXqi"`, regex patterns, `apply --check` read-only, both messages, `policy` tags — all byte-equal. `shlex` vs whitespace split (D4, confirmed low). |
| 3b. `advisor_approval` hook plumbing | confirmed_disparity | high | Py `advisor_approval.py:66-89` 6-way conversation scan vs Rust `hooks.rs:574-597` delegating to `AdvisorPort` whose only impl (`notifications.rs:226-231`) always denies `"missing"` (D1). |
| 3c. `require_no_inflight_background_tasks` | confirmed_match | — | Py `require_no_inflight_background_tasks.py:53-128` vs Rust `hooks.rs:501-570`: local-first short-circuit, sandbox-absent pass, daemon count, bailout fail-open with `daemon_unavailable_bailout`, `command_session_count_unavailable`, `ephemeral_jobs_in_flight`+`count`, both messages — all match. (Investigation's "max(local, daemon)" echoes the Py docstring; the *code* is sequential short-circuit on both sides — equivalent result.) |
| 3d. `block_in_isolated_mode` (fail-open) | confirmed_match | — | Py `block_in_isolated_mode.py:42-76` vs Rust `hooks.rs:459-473`: no sandbox → pass; daemon error → fail-open pass; active → deny `isolated_workspace_open`; same message. |
| 3e. `disallow_nested_planner_deferral` | confirmed_disparity | low | Py `disallow_nested_planner_deferral.py:38-41` fails-closed on `AttemptSubmissionContextError`; Rust `hooks.rs:614-616` passes when `workflow_id`/`workflow_control` absent (D3). Deny-on-nested + `nested_workflow` reason match. |
| 3f. Hook chain ordering per tool | confirmed_disparity | medium | Planner/generator/reducer chains match (verified at the four `@tool` callsites). Root: Py `submit_root_outcome.py:42` = `RequireNoInflight` only; Rust `meta.rs:72-75` adds `AdvisorApproval` (D1b). |
| 3g. Hook-target validation | confirmed_match | — | Py validates at decorator time (`hooks.py`/`decorator.py`); Rust ties hook→tool structurally via the `Hook` enum `tool` field (`hooks.rs:122-134`). Equivalent guarantee. |
| 4. Skills loading / exposure parity | confirmed_disparity | high | Loader/frontmatter/registry exact (4a-4c below). Per-agent `load_skill_reference` scoping dropped (D7). |
| 4a. Skill loader (dir walk, refs, fallback) | confirmed_match | — | Py `bundled/__init__.py:16-67` vs Rust `bundled.rs:27-119`: sorted dir walk, `references/*.md` by stem, frontmatter `name`/`description`, full-content fallback scan, `DESCRIPTION_MAX_CHARS=200`, `"Bundled skill: {name}"`. |
| 4b. Skill registry (register/get/list) | confirmed_match | — | Py `core/registry.py:8-24` (dict last-wins; `list_skills` sorted) vs Rust `registry.rs:19-42` (BTreeMap last-wins; values() sorted). |
| 4c. `load_skill_registry` cwd ignored | confirmed_match | — | Py `core/loader.py:11-17` (`del cwd`; bundled iterate) vs Rust `loader.rs:26-51` (cwd param dropped; missing root → empty; non-dir → `RootNotDir`). |

## Disparity adjudication

- **D1 — AdvisorApproval deny-all stub + root-gating divergence (high): CONFIRMED.**
  (a) `AdvisorService::approval_status` (`eos-engine/src/notifications.rs:226-231`)
  returns `approved:false, reason:"missing"` ignoring tool+conversation; `review`
  is a stub string (`:216-224`). Grep: exactly one `impl AdvisorPort` in the whole
  Rust tree; no `conversation_messages`/`helper_role`/verdict scan exists
  (`submit_advisor_feedback` *writes* `helper_role`/`verdict` metadata at
  `submission.rs:529-530` but nothing reads it). (b) Python root wires only
  `RequireNoInflightBackgroundTasks("submit_root_outcome")` (`submit_root_outcome.py:42`);
  Rust adds `AdvisorApproval` (`meta.rs:72-75`, comment flags it as an EOS decision).
  Net: `eos-runtime/src/tests.rs:208-229` (`root_terminal_blocked_without_advisor_approval`)
  asserts `TaskStatus::Failed` under the production stub — the default-wired Rust root
  cannot terminate. The runtime defaults advisor to `AdvisorService`
  (`app_state.rs:482-484`). Faithful and decisive.

- **D2 — Post-hook stage dropped (high): CONFIRMED (current behavior unchanged).**
  Python runs `run_post_hooks` (`hook_pipeline.py:110-188`, invoked `tool_call.py:192`)
  with result-replacement + re-validation; Rust `Hook` enum is pre-only and
  `execute_tool_once` has no post loop (`execution.rs:7-9,64-71`). No tool wires a
  real `post_hook` today (the only ref is the `tools/subagent/_factory.py` no-op
  copy/validate shim), so no current behavior changes. Correctly characterized as a
  removed extension-point/capability gap, not a live break.

- **D3 — nested-planner-deferral context-unavailable branch (low): CONFIRMED.**
  Python fails-closed (`disallow_nested_planner_deferral.py:40-41` returns
  `HookResult.fail(str(exc), policy=nested_planner_deferral)`); Rust fails-open
  (`hooks.rs:614-616` returns `pass()` when `workflow_id`/`workflow_control` absent).
  Safe iff `PlanSubmissionPort::apply_plan` re-rejects nested deferrals — unverified
  here (out of area; the port is `None` in `eos-runtime` Phase-6, `tool_context.rs:81`).
  Severity low confirmed.

- **D4 — git-arg whitespace split vs shlex (low): CONFIRMED.**
  Python `_split_git_args` (`destructive_shell.py:130-134`) tries `shlex.split`,
  falls back to `str.split`; Rust `split_git_args` (`hooks.rs:336-338`) always
  `split_whitespace`. Best-effort prehook (not the authoritative boundary, stated in
  both messages). Low confirmed.

- **D5 — input-parse internal-vs-validation split collapsed (low): CONFIRMED.**
  Python distinguishes `ValidationError` ("…Please retry") from other exceptions
  ("Internal validation error… {type}", no retry, logged) at `validation.py:35-62`;
  Rust renders only the retryable message for any serde failure
  (`execution.rs:124-134`). Low confirmed.

- **D6 — spec JSON-Schema dialect differs (low): CONFIRMED.**
  Snapshot carries `$schema:".../draft-07/schema#"`, `title`, `format:"uint32"`
  (schemars) vs Python pydantic-v2 2020-12. Plus the text-tool `output_schema`
  omission (the 1a substance). Contract (required fields, enums incl. the
  `run_subagent` agent_name enum patch, `spec.rs:54-74`) is equivalent. Low confirmed.

- **D7 — per-agent skill scoping dropped (high): CONFIRMED.**
  Python `make_load_skill_reference_from_context` (`_factory.py:68-88`) reads
  `agent_name` → `AgentDefinition.skill` → `allowed_slugs=[slug]` →
  `make_load_skill_reference_for_skill` builds `available` from only that slug
  (`_factory.py:40-65`); the body rejects `skill_name not in available` and lists
  only `available.keys()` (`load_skill_reference.py:52-61`). Rust `LoadSkillReference`
  (`model_tools/skills.rs:47-64`) has no allowlist: it queries the whole
  `ctx.skill_registry` and on miss lists all skills. The registry is a single
  process-global Arc cloned into every tool context regardless of `agent_name`
  (`tool_context.rs:79`, with `agent_name` captured at `:65` but never used to scope
  skills; built from `CallerScope::default()` at `app_state.rs:436`). Any agent can
  read any skill's references and the not-found error leaks all bundled skill names.
  Faithful and decisive.

- **D8 — pre-hook `hook_trace`/`effective_tool_input` not stamped on success (low): CONFIRMED.**
  Python success path (`tool_call.py:196` → `finalize_result` →
  `_metadata_with_hook_details`, `hook_pipeline.py:190-193,253-260`) stamps
  `hook_trace` (when non-empty) + `effective_tool_input`. Rust accumulates
  `hook_trace` (`execution.rs:47-62`) but the all-pass path (`:64-71`) discards it;
  only `hook_failure_result` consumes it. Observability loss on successfully-gated
  calls. Note: NF1 (below) widens this — because Rust hooks run on raw JSON,
  `effective_tool_input` parity is moot, but `hook_trace` loss stands. Low confirmed.

## New findings

- **NF1 (investigator_missed, FLAG LOUDLY) — inner pipeline ORDER is inverted;
  invariant 2 is NOT a match on ordering. Severity medium.**
  Investigation row 2 states the inner pipeline is "parse → pre-hooks → execute →
  validate" and "matches." It does not. Python: `parse_tool_input` first
  (`tool_call.py:157`), then `run_pre_hooks(parsed.args)` on the validated BaseModel
  (`:163`), then `execute_tool_body(... parsed_input)` (`:187`). Rust: pre-hooks run
  on `raw_input` (`execution.rs:48-49`), then `executor().execute(raw_input)`
  (`:65`), and `parse_input` happens *inside* each executor afterward (verified
  `submission.rs:106` SubmitRoot, `:208/:261/:351` gen/red/planner, `skills.rs:42`).
  So Rust = **pre-hooks(raw) → parse**; Python = **parse → pre-hooks(parsed)**.
  Two observable consequences, both grounded in code:
  1. Error precedence flips. A malformed gated `submit_*` call: Python returns
     "Invalid input for X… Please retry" (parse error) before any hook; Rust runs
     hooks first and — under the always-denying `AdvisorService` (D1) — surfaces a
     `AdvisorApproval`/`RequireNoInflight` BLOCKED hook message instead. Reproducible;
     compounds with D1.
  2. Rust hooks read raw JSON, not the defaulted/validated model. Any DTO field with
     a non-None default applied by validation would be visible to a Python hook but
     not the Rust hook. The currently-wired field-reading hooks
     (`DisallowNestedPlannerDeferral`/`is_bailout_submission` reading
     `deferred_goal_for_next_iteration`/`status`; destructive shell reading
     `command`/`cmd`) happen not to depend on a validation default today, so no live
     break is confirmed — but the seam is fragile and the "matches" claim is wrong.
  Decisive anchors: `tool_call.py:157,163,187` vs `execution.rs:48,65` +
  `submission.rs:106`.

## Overall verdict

The investigation is high quality and its three load-bearing disparities (D1
advisor stub + root gate, D2 post-hook drop, D7 skill scoping) are all CONFIRMED
with decisive bilateral anchors; D3/D4/D5/D6/D8 are confirmed at the stated low
severities. The registry/spec semantics, 24-name set, batch/lifecycle dispatch
messages (byte-equal), and the entire skills loader/registry/cwd surface are
genuine matches.

Two corrections to the investigation:
1. **One false match (investigator_missed): NF1** — the inner-pipeline ordering is
   inverted (hooks-on-raw-then-parse vs parse-then-hooks-on-model), so invariant 2's
   "inner pipeline … matches" claim is wrong. Medium severity (flips error
   precedence, compounding D1; hooks see raw not validated input).
2. **One overstated row: 1a** — Python emits `output_schema` unconditionally, Rust
   omits it for text tools; the row should be a low disparity (already captured by
   D6), not "match."

No FALSE ALARMS found: every flagged disparity is real. The cross-`eos-protocol`
state hooks (3c/3d) were independently re-derived from Python source and are
faithful ports, not phantom gaps.

DONE tools_framework: 12 confirmed_match, 7 confirmed_disparity (D1,D2,D3,D4,D5,D6,D7,D8 across rows 2/3/3b/3e/3f/4 + 1a overstated), 0 unproven; YES investigator_missed — NF1: inner-pipeline parse/pre-hook ordering is inverted (Rust hooks run on raw JSON before parse; Python parses then runs hooks on the validated model), so invariant-2's "inner pipeline matches" is a false match (medium).
