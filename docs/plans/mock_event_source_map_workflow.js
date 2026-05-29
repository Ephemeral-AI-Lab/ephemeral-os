export const meta = {
  name: 'mock-event-source-migration-map',
  description: 'Read-only map of the MockSquadRunner→ScenarioEventSource migration: heavy-probe adaptation strategy, executor-action catalogue, assertion→graph_summary rewrites, full tests/mock categorization, and the Phase-3 deletion checklist.',
  whenToUse: 'Before executing Phase 2/3 of the mock_event_source migration.',
  phases: [
    { title: 'ProbeAdaptation', detail: 'per heavy probe/script module: call_tool/sandbox_api/publish usage + queue-bridge feasibility' },
    { title: 'ActionsAndAssertions', detail: 'executor-action catalogue + per-test assertion→graph_summary rewrites' },
    { title: 'InventoryAndDeletion', detail: 'tests/mock categorization + Phase-3 deletion checklist' },
  ],
}

const MOCK = '/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock'
const TCR = '/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner'

// Read-only guardrail prepended to every agent prompt.
const RO = [
  'READ-ONLY task. Do NOT edit, write, or run any code. Use Read/Grep/Glob only.',
  'Context: the codebase is migrating mock agents off the imperative MockSquadRunner',
  `(${MOCK}/runner.py, 2043 LoC) onto the REAL engine query loop via an injected`,
  'ScenarioEventSource. The seam + 3 simple probes (preflight/sandbox_integrity/final_probe)',
  `are already ported in ${MOCK}/probes.py + scenario_adapter.py + event_source.py + scenario_loop_runner.py`,
  '(all verified green on docker). The remaining heavy probes + the PreparedToolScriptEngine',
  'scripts still run only through the old runner and must be ported in Phase 2.',
  'KEY CONSTRAINT (the "two-level coroutine bridge"): the ScenarioEventSource drives a per-turn',
  'TurnScript async-generator that must `yield Turn(calls=(ToolCall,))` at TOP LEVEL — Python',
  'forbids hiding an async-gen yield inside a helper. The 3 ported probes were rewritten AS',
  'async generators (`result = yield ToolCall(name, args)` replacing `await call_tool(...)`).',
  'The heavy probe modules instead accept `call_tool` as an INJECTED async callback',
  '(the CallTool protocol in tool_scripts.py) and call `await call_tool(tool_obj, raw_input,',
  'metadata, emit, allow_error=...)` deep in their bodies. The proposed alternative to rewriting',
  'them is a BRIDGING call_tool shim (asyncio.Queue + per-call Future) so the probe body stays',
  'byte-identical while each `await call_tool(...)` routes a ToolCall through the loop.',
].join(' ')

const PROBE_SPEC_TEMPLATE = [
  'Produce a precise PORT SPEC as markdown with these exact subsections:',
  '### <module> — entry functions',
  'List every public entry function (name + signature) and how runner.py dispatches it',
  '(grep runner.py for the import + call site; note smoke/index/mode params).',
  '### call_tool sites',
  'A table of EVERY `await call_tool(...)` (or via PreparedToolScriptEngine step): tool name,',
  'key args, allow_error?, and whether it is a BACKGROUND call (background_task_id/',
  'sandbox_invocation_id passed, or input has background=True). Count them.',
  '### out-of-band work (NOT through call_tool)',
  'Every direct `sandbox_api.*` call, and every `publish(...)` / `publish_mock_record(...)` /',
  '`record_tool_check(...)` / `caller(...)` use. These do not touch the loop and re-home to a',
  'ProbeContext-style helper.',
  '### loop-interaction verdict (DECISIVE)',
  'Answer YES/NO: does this module interact with the engine ONLY via the injected call_tool',
  '(everything else being out-of-band publish/record/sandbox_api)? If NO, quote the exact site',
  'that needs something else.',
  '### recommended adaptation',
  'One of: "QUEUE-BRIDGE (zero body change)" | "REWRITE-AS-GENERATOR" | "HYBRID" — with a 2-3',
  'sentence rationale. Flag any concurrency/cancellation/background hazards for the bridge',
  '(e.g. the background_shell probe cancels tasks, asserts on partial writes, races).',
  '### executor action strings it backs',
  'Which scenario `executor_actions` strings route to this module (cross-ref runner.py:_run_executor 372-823).',
].join('\n')

const HEAVY = [
  { f: `${MOCK}/complex_project_build_probe.py`, label: 'complex_project_build' },
  { f: `${MOCK}/complex_project_build_shell_edit_lsp_probe.py`, label: 'cpb_shell_edit_lsp' },
  { f: `${MOCK}/complex_project_build_grep_glob_probe.py`, label: 'cpb_grep_glob' },
  { f: `${MOCK}/high_concurrency_probe.py`, label: 'high_concurrency' },
  { f: `${MOCK}/heavy_io_zoned_probe.py`, label: 'heavy_io_zoned' },
  { f: `${MOCK}/background_shell_probe.py`, label: 'background_shell' },
  { f: `${MOCK}/ephemeral_workspace_probe.py`, label: 'ephemeral_workspace' },
  { f: `${MOCK}/plugin_workspace_probe.py`, label: 'plugin_workspace' },
  { f: `${MOCK}/tool_scripts.py`, label: 'tool_scripts(PreparedToolScriptEngine + simple scripts)' },
  { f: `${MOCK}/full_stack_tool_scripts.py`, label: 'full_stack_tool_scripts' },
  { f: `${MOCK}/capacity_actions`, label: 'capacity_actions(dir)' },
  { f: `${MOCK}/runner.py lines 1083-1257`, label: 'auto_squash_commit_resume_probe + batch/conflict helpers (in runner.py)' },
]

phase('ProbeAdaptation')
const probeSpecs = await parallel(
  HEAVY.map((m) => () =>
    agent(
      `${RO}\n\nAnalyze: ${m.f}\nFocus module: ${m.label}\n\n${PROBE_SPEC_TEMPLATE}`,
      { label: `probe:${m.label}`, phase: 'ProbeAdaptation' }
    )
  )
)

phase('ActionsAndAssertions')

const actionCatalogue = agent(
  `${RO}\n\nProduce the COMPLETE executor-action catalogue as markdown.\n` +
    `1. Grep every scenario under ${TCR}/scenarios/ for \`def executor_actions\` and list EVERY distinct action value any scenario yields (literal strings AND f-string/prefixed forms like execute_package:<id>, request_recursive_goal:<id>, fail:<reason>, request_recursive_matrix:<id>).\n` +
    `2. For each action, map to its dispatch branch in ${MOCK}/runner.py:_run_executor (lines 372-823): which probe/script function it calls, and whether it currently raises NotImplementedError in ${MOCK}/scenario_adapter.py:_executor_script.\n` +
    `3. Note which actions submit a TERMINAL other than submit_execution_success (fail→submit_execution_blocker, recursive→submit_execution_handoff) — these need their own ask_advisor-gated terminal turn in the adapter, not the success terminal.\n` +
    `Output a single table: action | terminal | backing probe/script fn | adapter status (ported/NotImplemented). Then a bullet list of any actions that are NOT plain executor_actions (e.g. planner/verifier/evaluator response variants that matter).`,
  { label: 'executor-action-catalogue', phase: 'ActionsAndAssertions' }
)

// The event-asserting test + scenario files (from grep of min_event_counts/expected_event_sequence/lifecycle EventType).
const ASSERT_FILES = [
  `${TCR}/tests/mock/task_center/test_correctness.py`,
  `${TCR}/tests/mock/task_center/test_focused_scenarios.py`,
  `${TCR}/tests/mock/task_center/test_full_case_user_input.py`,
  `${TCR}/tests/mock/task_center/test_initial_messages_capture.py`,
  `${TCR}/tests/mock/task_center/test_deferred_parent_planner_terminal_routing.py`,
  `${TCR}/tests/mock/sandbox/capacity/test_full_system_capacity_matrix.py`,
  `${TCR}/tests/mock/sandbox/full_stack/test_full_stack_adversarial.py`,
  `${TCR}/tests/mock/sandbox/layer_stack_occ_overlay/test_focused_sandbox_scenarios.py`,
  `${TCR}/tests/mock/sandbox/layer_stack_occ_overlay/test_high_concurrency_layerstack_overlay_occ.py`,
  `${TCR}/tests/mock/sandbox/project_build/test_project_build_shell_edit_lsp_three_parallel_agents.py`,
]
const assertRewrites = await parallel(
  ASSERT_FILES.map((f) => () =>
    agent(
      `${RO}\n\nRead ${f} and the shared helpers it uses ` +
        `(${TCR}/tests/mock/_focused_scenario_contracts.py has FocusedScenarioCase + assert_focused_scenario_report + _assert_ordered_subsequence + _assert_event_counts; report.graph_summary shape is built by ${TCR}/core/runner.py:_graph_summary ~90-142).\n\n` +
        'For EACH assertion in this file that depends on lifecycle EventTypes (min_event_counts, expected_event_sequence, absent_events, seen_event_types, count_events(PLANNER_INVOKED/EXECUTOR_INVOKED/...)), write the PRECISE graph_summary-based replacement, using the §4.1 mapping: ' +
        'INVOKED counts→count role tasks in attempt["tasks"]/task_ids; EXECUTOR/VERIFIER SUCCESS/FAILURE→per-task status; EVALUATOR_*→attempt status+fail_reason; COMPLETES vs DEFERS→iteration/attempt["deferred_goal_for_next_iteration"]; RECURSIVE_GOAL_*→child goal origin_kind=="task"+requested_by_task_id+status. ' +
        'Quote the current assertion (with line numbers) and give the exact replacement code. ' +
        'Call out the test_correctness.py "no ask_advisor in transcript" assertion if present — it is now INVERTED (real ask_advisor turns DO appear). ' +
        'Also note whether this test sets EOS_MOCK_EVENT_SOURCE_RUNNER and which single scenario it runs (its under-flag verification target). ' +
        'Output: a per-assertion before/after table + a "shared helpers needed" list (count_role_tasks/attempt_outcome/recursive_goals).',
      { label: `assert:${f.split('/').pop()}`, phase: 'ActionsAndAssertions' }
    )
  )
)

phase('InventoryAndDeletion')

const INVENTORY_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['files'],
  properties: {
    files: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['path', 'category', 'runs_scenario', 'references_lifecycle_events', 'notes'],
        properties: {
          path: { type: 'string', description: 'path relative to tests/mock/' },
          category: {
            type: 'string',
            enum: ['event_dependent', 'graph_summary_or_store_state', 'pure_sandbox_runner_agnostic', 'imports_only', 'other'],
            description: 'event_dependent=asserts on lifecycle EventTypes/min_event_counts/expected_event_sequence; pure_sandbox_runner_agnostic=tests sandbox internals via fixtures, not the squad/scenario runner',
          },
          runs_scenario: { type: 'boolean', description: 'does it run a Scenario through the runner (run_scenario_on_sweevo_image / build_scenario_config / MockSquadRunner)?' },
          references_lifecycle_events: { type: 'boolean', description: 'imports/uses any of the 14 lifecycle EventTypes or expected_event_sequence/seen_event_types' },
          notes: { type: 'string', description: 'one line: what would break under the migration, if anything' },
        },
      },
    },
  },
}

const INVENTORY_DIRS = [
  'contracts',
  'task_center',
  'sandbox/background_tool',
  'sandbox/ephemeral_workspace + sandbox/plugin + sandbox/capacity + sandbox/full_stack + environments',
  'sandbox/layer_stack_occ_overlay + sandbox/project_build',
  'sandbox/isolated_workspace (all subdirs — expect most are runner-agnostic)',
]
const inventory = await parallel(
  INVENTORY_DIRS.map((d) => () =>
    agent(
      `${RO}\n\nInventory the test files under ${TCR}/tests/mock/${d}.\n` +
        'For EACH .py test file (skip __init__.py and pure _helpers/_invariants includes unless they drive scenarios), classify it. ' +
        'A file is event_dependent if it imports any of these EventTypes or asserts on them: PLANNER_INVOKED, PLANNER_COMPLETES_GOAL_PLAN, PLANNER_DEFERS_GOAL_PLAN, PLANNER_REPLAN, EXECUTOR_INVOKED/SUCCESS/FAILURE, VERIFIER_INVOKED/SUCCESS/FAILURE, EVALUATOR_INVOKED/SUCCESS/FAILURE, RECURSIVE_GOAL_REQUESTED/COMPLETED, FULL_STACK_SCRIPT_COMPLETED — or uses min_event_counts/expected_event_sequence/seen_event_types/FocusedScenarioCase. ' +
        'It runs_scenario if it calls run_scenario_on_sweevo_image / build_scenario_config / instantiates MockSquadRunner / imports a Scenario subclass and drives it. ' +
        'pure_sandbox_runner_agnostic = it exercises sandbox/IWS/overlay internals through fixtures (_iws_rpc, daemon, sandbox_api) WITHOUT running a squad scenario — these should be unaffected by the runner migration. ' +
        'Return structured data per the schema. Be exhaustive — list every test file in the directory.',
      { label: `inventory:${d.split(' ')[0]}`, phase: 'InventoryAndDeletion', schema: INVENTORY_SCHEMA }
    )
  )
)

const deletionChecklist = agent(
  `${RO}\n\nProduce the PRECISE Phase-3 deletion checklist as markdown, with exact file paths + line ranges + symbol names. Cover:\n` +
    `1. ${MOCK}/runner.py: every method/attribute to delete (the _run_planner/_run_executor/_run_verifier/_run_evaluator, _call_tool, _approve_terminal, all _run_*_probe, _record_tool_check, _script_engine, the _*_EVENT_BY_TOOL maps at 109-122, every _publish(EventType.<lifecycle>) call site) vs. what to KEEP/move into ScenarioLoopRunner (_inspect_prompt 1748-1840, _record_initial_messages 1842-1863, _current_attempt_and_iteration 1865-1879, _invocation_payload, the MOCK_* publishers). Give the line ranges.\n` +
    `2. ${MOCK}/_advisor_approval.py: confirm it can be deleted; grep ALL importers (src + tests) and list them.\n` +
    `3. ${TCR}/audit/events.py: the 14 lifecycle EventType members to remove (61-76) vs the MOCK_*/SANDBOX_* to KEEP. Confirm via grep that nothing OUTSIDE the migration-touched files still references the 14 after assertions migrate.\n` +
    `4. ${TCR}/scenarios/base.py: removing Scenario.expected_event_sequence (51,74) — list every scenario file that declares it (they all need the line removed) + the EventType import if it becomes unused.\n` +
    `5. ${TCR}/hooks/builtins.py: the exact VERIFIER_INVOKED/VERIFIER_SUCCESS emit sites (lines ~28-31,135,162) to drop, and whether dropping them breaks any hook test.\n` +
    `6. ${TCR}/core/runner.py RunReport: the seen_event_types field + _focused_scenario_contracts.py _assert_ordered_subsequence/_assert_event_counts machinery — what to remove and who consumes it.\n` +
    `7. ${TCR}/tests/mock/contracts/test_advisor_gate_negative_path.py: how it currently asserts a blocked terminal, and what MutableMockState.consume_advisor_verdict() needs (the adapter already reads it via getattr in _advisor_script).\n` +
    `Order the checklist so EventType-enum removal is strictly LAST (everything referencing it must migrate first).`,
  { label: 'deletion-checklist', phase: 'InventoryAndDeletion' }
)

const [catalogue, deletion] = await Promise.all([actionCatalogue, deletionChecklist])

return {
  probeSpecs: HEAVY.map((m, i) => ({ module: m.label, spec: probeSpecs[i] })),
  executorActionCatalogue: catalogue,
  assertionRewrites: ASSERT_FILES.map((f, i) => ({ file: f, rewrite: assertRewrites[i] })),
  testInventory: INVENTORY_DIRS.map((d, i) => ({ dir: d, files: inventory[i]?.files ?? null })),
  deletionChecklist: deletion,
}
