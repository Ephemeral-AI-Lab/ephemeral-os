export const meta = {
  name: 'rust-parity-recovery',
  description: 'Fill audit gaps: 7 missing investigations + 10 missing verifications, then synthesize REPORT.md',
  phases: [
    { title: 'Investigate-missing', detail: '7 areas with no .md (advisor, background_supervisor, request_completion, attempt_harness, context_engine, subagent, tools_framework)' },
    { title: 'Verify-existing', detail: '10 areas with .md but no independent verification' },
    { title: 'Synthesize', detail: 'Cross-domain REPORT.md from all 25 areas' },
  ],
}

// Schemaless by design: agents WRITE their .md / .verify.md files (the durable
// deliverable) and return a short text confirmation. No StructuredOutput → the
// failure mode that killed 12 areas in the first run cannot recur.

const AREA_DIR = 'docs/reviews/rust_parity/areas'

// --- 7 areas needing full investigate + verify (no .md exists) ---------------
const FULL = [
  {
    key: 'advisor', domain: 'agent-core',
    title: 'Advisor (ask_advisor pass-verdict gate before terminal submission)',
    docs: ['docs/architecture/tools/ask-helper.html', 'docs/architecture/workflow/terminal-tools.html'],
    python: ['backend/src/tools/ (IN-TREE: advisor / ask_advisor helper)', 'backend/src/engine/ advisor gate integration (IN-TREE)'],
    rust: ['agent-core/crates/eos-tools/src/model_tools/advisor.rs', 'agent-core/crates/eos-engine/src/notifications.rs (AdvisorPort / AdvisorApproval)', 'agent-core/crates/eos-engine/src/tool_call/ or query/loop_.rs (the gate)'],
    invariants: [
      'The root agent AND workflow agents must call ask_advisor to choose the payload for their terminal tool.',
      'They must receive a verdict of PASS from the advisor BEFORE terminal tool submission is allowed.',
      'A non-pass advisor verdict BLOCKS terminal submission (the gate is enforced, not advisory).',
      'Which roles are subject to the advisor gate is correct (root + workflow agents; confirm subagents are excluded or not).',
    ],
    owner_notes: 'Owns the advisor-pass-before-terminal cross-cutting invariant (spans submission + loop). Find the exact gate that blocks submission without a PASS.',
  },
  {
    key: 'background_supervisor', domain: 'agent-core',
    title: 'Background supervisor (exec/subagent/workflow bg, exec status from daemon, terminal-block)',
    docs: ['docs/architecture/tools/background.html', 'docs/architecture/agent_loops/background-operations.html'],
    python: ['backend/src/engine/ background supervisor (IN-TREE)', 'backend/src/sandbox/api/ exec status (IN-TREE)'],
    rust: ['agent-core/crates/eos-engine/src/background/ (supervisor.rs, dispatch.rs, policy.rs, mod.rs)'],
    invariants: [
      'The background supervisor handles exec_command, subagent, AND workflow as background tasks.',
      'For exec_command status it PULLS from the sandbox daemon (not a provider-level persistent shell session).',
      'An agent CANNOT submit its terminal tool while any background task is still running (hard gate).',
      'Background completion surfaces back to the agent (notification / result injection).',
      'Background execution is an engine dispatch mode (policy decides what is backgroundable).',
    ],
    owner_notes: 'Owns the "no terminal submission while background task running" cross-cutting invariant. Find the exact gate + how exec status is polled from the daemon.',
  },
  {
    key: 'request_completion', domain: 'agent-core',
    title: 'User request -> completion (sandbox_id binding, root task, submit_root_outcome)',
    docs: ['docs/architecture/tools/submission.html', 'docs/architecture/workflow/index.html', 'docs/architecture/agent_loops/main-loop.html'],
    python: ['backend/src/runtime/entry.py (IN-TREE)', 'backend/src/task/ (IN-TREE)', 'submit_root_outcome path (IN-TREE)'],
    rust: ['agent-core/crates/eos-runtime/src/ (entry.rs, root_agent.rs, agent_loop.rs, agent_runner.rs, app_state.rs, tool_context.rs)', 'agent-core/crates/eos-tools/src/model_tools/submission.rs'],
    invariants: [
      'A user request is BOUND to a sandbox_id.',
      'The request mints a root Task(role=root, workflow_id=None) and runs the root agent directly through the entry path.',
      'The result the user receives comes from submit_root_outcome().',
      'The root agent MAY call delegate_workflow() for sophisticated execution, but the final user-facing result STILL comes from submit_root_outcome() (delegated workflow outcome does not directly become the user result).',
      'The request finishes through submit_root_outcome (a terminal submission).',
    ],
  },
  {
    key: 'attempt_harness', domain: 'agent-core',
    title: 'Attempt harness (planner DAG, generator/reducer, PLAN->RUN->CLOSED, reducer exit gate)',
    docs: ['docs/architecture/workflow/attempt-harness.html', 'docs/architecture/workflow/agent-roles.html'],
    python: ['backend/src/workflow/attempt/ (IN-TREE)'],
    rust: ['agent-core/crates/eos-workflow/src/attempt/ (orchestrator.rs, launch.rs, plan_dag.rs, run_stage.rs, orchestrator_registry.rs, mod.rs)'],
    invariants: [
      'Each Attempt owns ONE planner-authored DAG of generator + reducer Task rows whose edges are `needs`.',
      'Attempt stages are PLAN -> RUN -> CLOSED.',
      'The reducer is the EXIT GATE: the attempt closes through the reducer.',
      'Generators + reducers are launched based on the planned tasks (respecting `needs` dependency edges).',
      'AttemptOrchestrator is per-Attempt machinery, not a global orchestration layer.',
    ],
  },
  {
    key: 'context_engine', domain: 'agent-core',
    title: 'Context engine (role packets from store state, workflow-only)',
    docs: ['docs/architecture/workflow/context-engine.html'],
    python: ['backend/src/workflow/context_engine/ (IN-TREE)'],
    rust: ['agent-core/crates/eos-workflow/src/context/ (engine.rs, composer.rs, scope.rs, section.rs, xml.rs, mod.rs)'],
    invariants: [
      'ContextEngine builds role packets from STORE STATE for WORKFLOW agents only (not the root agent, not subagents).',
      'Lifecycle policy lives in workflow handlers/managers, NOT hidden inside context construction.',
      'Packet composition varies by role/scope (planner vs generator vs reducer see different sections).',
      'XML rendering of context sections is preserved.',
    ],
  },
  {
    key: 'subagent', domain: 'agent-core',
    title: 'Subagent (launched as background task)',
    docs: ['docs/architecture/tools/subagent.html', 'docs/architecture/agent_loops/background-operations.html'],
    python: ['backend/src/tools/ (IN-TREE: subagent tool)', 'backend/src/engine/ background (IN-TREE)'],
    rust: ['agent-core/crates/eos-tools/src/model_tools/subagent.rs', 'agent-core/crates/eos-engine/src/background/ (supervisor.rs, dispatch.rs, mod.rs)'],
    invariants: [
      'Subagents are launched as BACKGROUND tasks (not inline blocking calls).',
      'The subagent result surfaces back to the launching agent.',
      'Subagent lifecycle is tracked by the background supervisor.',
    ],
  },
  {
    key: 'tools_framework', domain: 'agent-core',
    title: 'Tools framework + hooks + skills + registry/spec',
    docs: ['docs/architecture/tools/framework.html', 'docs/architecture/tools/hooks.html', 'docs/architecture/tools/skills.html', 'docs/architecture/tools/index.html'],
    python: ['backend/src/tools/_framework/ (IN-TREE)', 'backend/src/skills/ (IN-TREE)'],
    rust: ['agent-core/crates/eos-tools/src/ (dispatch.rs, execution.rs, executor.rs, hooks.rs, registry.rs, spec.rs, intent.rs, metadata.rs, meta.rs, name.rs, result.rs)', 'agent-core/crates/eos-skills/src/', 'agent-core/crates/eos-tools/src/model_tools/skills.rs'],
    invariants: [
      'Tool registry + spec generation parity (see default_tool_specs snapshot under eos-tools/src/model_tools/snapshots).',
      'Tool dispatch + execution pipeline (intent labeling, pre/post hooks) parity.',
      'Hooks framework (Pre/Post tool-use equivalents) parity.',
      'Skills loading / exposure parity.',
    ],
  },
]

// --- 10 areas with a complete .md but no independent verification ------------
const VERIFY_ONLY = [
  ['layerstack', 'sandbox', 'LayerStack (layers on workspace base, snapshot view, lease semantics)'],
  ['occ', 'sandbox', 'OCC gate (gitignore/outside-workspace direct merge; git-tracked through gate)'],
  ['sandbox_tools', 'sandbox', 'Sandbox tools (command_exec, write_stdin, write, edit, multi-edit, grep, glob)'],
  ['daemon_protocol', 'sandbox', 'Daemon protocol & dispatch (wire protocol, envelopes, CAS, command session, in-flight)'],
  ['plugins', 'sandbox', 'Plugins (install, PPC, refresh, registry, OCC callbacks, projection)'],
  ['provider_network', 'sandbox', 'Provider / provisioning / network namespace (docker/daytona, ns-holder, setns)'],
  ['perf', 'sandbox', 'Performance properties (O(1) lowerdir CoW, O(n*delta) upperdir, fast mount)'],
  ['workflow_lifecycle', 'agent-core', 'Workflow lifecycle (workflow->iteration->attempt creation rules, delegate_workflow)'],
  ['deferred_goal_depth', 'agent-core', 'Deferred goal handoff + nested depth 2 + planner@depth-2 cannot defer'],
  ['model_provider_prompt', 'agent-core', 'Model provider + SSE + prompt/context assembly'],
]

const ALL_KEYS = [
  'overlay', 'layerstack', 'squash', 'occ', 'ephemeral_workspace', 'isolated_workspace',
  'sandbox_tools', 'daemon_protocol', 'plugins', 'provider_network', 'perf',
  'query_engine', 'budget_notifications', 'terminal_tools', 'workflow_lifecycle',
  'attempt_harness', 'deferred_goal_depth', 'context_engine', 'advisor', 'subagent',
  'background_supervisor', 'request_completion', 'model_provider_prompt', 'tools_framework',
  'persistence_state',
]

const bullets = (a) => a.map((x) => `  - ${x}`).join('\n')
const numbered = (a) => a.map((x, i) => `  ${i + 1}. ${x}`).join('\n')

const BOUNDARY = `## Architecture note
The \`sandbox/\` workspace is an INDEPENDENT module reached from agent-core ONLY through the \`eos-protocol\` contract crate, so a single Python in-process flow may be SPLIT across that boundary (host/client side in agent-core: eos-sandbox-host/eos-sandbox-api; execution side in sandbox: eos-daemon/eos-runner/eos-overlay/eos-occ/eos-layerstack/eos-isolated/eos-plugin). Trace each invariant ACROSS the boundary; do not call a dynamic "missing" just because it lives on the other side. Note: \`backend/src\` is the about-to-be-deleted Python ground truth; \`// PORT\` comments have already been removed, so map Rust->Python by behavior + grep, not by PORT markers.`

function investigatePrompt(a) {
  const reportPath = `${AREA_DIR}/${a.key}.md`
  const gitNote = a.git ? '\n  NOTE: pre-cutover Python sandbox internals were deleted and MATERIALIZED at /tmp/oldpy/backend/src/sandbox/... — read them there.' : ''
  return `Audit the Rust re-implementation of the EphemeralOS framework against its behavioral ground truth, for ONE area:

# AREA: ${a.title}  (domain: ${a.domain})

THE WORRY: the Rust port may SILENTLY MISS key dynamics, drop details, or introduce bugs. A shallow "looks fine" is worse than useless. Every claim needs bilateral file:line evidence quoting REAL code/constants.

Source precedence: Python source = behavioral GROUND TRUTH; docs/architecture = corroboration; the invariant checklist = what to confirm (specifics may be fuzzy — find the real constant). A three-way disagreement is itself a finding.

## Ground truth
Docs:
${bullets(a.docs)}
Python:
${bullets(a.python)}${gitNote}

${BOUNDARY}

## Rust under audit
${bullets(a.rust)}

## Invariant checklist (confirm EACH with bilateral evidence)
${numbered(a.invariants)}
Extract EXACT CONSTANTS/operators (>= vs >) and compare literal values on both sides.
${a.owner_notes ? `\nOWNER NOTE: ${a.owner_notes}` : ''}

## Method
Read ground truth (docs + Python), understand the intended dynamic incl. edge cases/ordering/errors. Read the Rust, map each invariant to its anchor (grep). Status per invariant: match / partial / missing / bug / divergent / unverifiable — a "match" REQUIRES the Rust file:line. Hunt bugs/dropped details beyond the checklist; distinguish intentional migration changes from real gaps.

## Output (NO structured tool — just write the file)
Write a thorough markdown report to: ${reportPath}
Sections: "## Ground truth" (anchors) · "## Rust mapping" · "## Invariant table" (invariant | status | severity | python file:line | rust file:line | note) · "## Disparities" (evidence + why it matters + suggested fix, each id'd D1.. with severity) · "## Extra findings" · "## Open questions".
Then reply with one line: "DONE ${a.key}: <N> invariants, <M> disparities (highest severity: X)".`
}

function verifyPromptFull(a) {
  const mdPath = `${AREA_DIR}/${a.key}.md`
  const verifyPath = `${AREA_DIR}/${a.key}.verify.md`
  return verifyBody(a.key, a.title, a.domain, mdPath, verifyPath, numbered(a.invariants), a.git)
}
function verifyPromptLight(key, domain, title) {
  const mdPath = `${AREA_DIR}/${key}.md`
  const verifyPath = `${AREA_DIR}/${key}.verify.md`
  const isSandbox = domain === 'sandbox'
  return verifyBody(key, title, domain, mdPath, verifyPath, `  (The completed investigation at ${mdPath} lists the invariant checklist + the doc/Python/Rust anchors. Use it as a MAP, but open the cited files and re-derive yourself.)`, isSandbox)
}
function verifyBody(key, title, domain, mdPath, verifyPath, checklist, git) {
  const gitNote = git ? ' (deleted Python materialized at /tmp/oldpy/backend/src/sandbox/...)' : ''
  return `You are the INDEPENDENT VERIFIER for area "${title}" (${domain}). An investigation was written to ${mdPath}. TRUST NOTHING — re-derive the truth by opening the files yourself.

Catch TWO failure modes, FIRST is primary:
(a) FALSE MATCH — investigator said "match" but Rust misses/breaks the dynamic. Hunt these.
(b) FALSE ALARM — a flagged disparity that is actually implemented (perhaps across the eos-protocol boundary).

Source precedence: Python source = ground truth${gitNote}; docs/architecture = corroboration. \`// PORT\` comments are gone — map Rust->Python by behavior + grep. The sandbox workspace is reached via eos-protocol; trace invariants across that boundary.

## Read first
- The investigation: ${mdPath}
## Invariant checklist — independently confirm EACH
${checklist}
Extract + compare EXACT constants/operators on both sides.

For each invariant assign: confirmed_match / confirmed_disparity / investigator_overstated / investigator_missed (claimed match but actually broken → FLAG LOUDLY) / unproven (say what blocked you). Bilateral file:line for every verdict. Adjudicate each investigator disparity (confirmed/refuted/adjusted) and add any NEW findings.

## Output (NO structured tool — just write the file)
Write to: ${verifyPath}
Sections: "## Invariant verdict table" (invariant | independent_status | severity | decisive bilateral anchor) · "## Disparity adjudication" · "## New findings" · "## Overall verdict".
Then reply one line: "DONE ${key}: <X> confirmed_match, <Y> confirmed_disparity, <Z> unproven; <any investigator_missed?>".`
}

// ---------------------------------------------------------------------------
// Second-pass gap fill: only the areas still single-pass after run 1.
const GAP_FULL = new Set(['attempt_harness', 'background_supervisor'])
const GAP_VERIFY = new Set(['plugins', 'workflow_lifecycle', 'model_provider_prompt'])

phase('Investigate-missing')
const fullPromise = pipeline(
  FULL.filter((a) => GAP_FULL.has(a.key)),
  (a) => agent(investigatePrompt(a), { label: a.key, phase: 'Investigate-missing' }),
  (_r, a) => agent(verifyPromptFull(a), { label: `verify:${a.key}`, phase: 'Verify-existing' }),
)

phase('Verify-existing')
const verifyPromise = parallel(
  VERIFY_ONLY.filter(([key]) => GAP_VERIFY.has(key)).map(([key, domain, title]) => () =>
    agent(verifyPromptLight(key, domain, title), { label: `verify:${key}`, phase: 'Verify-existing' })),
)

await Promise.all([fullPromise, verifyPromise])

phase('Synthesize')
const reportPath = 'docs/reviews/rust_parity/REPORT.md'
const synthPrompt = `You are the SYNTHESIS author for a Rust-vs-Python/docs parity audit of EphemeralOS (two Rust workspaces: agent-core/ and sandbox/ reached via eos-protocol; ground truth = docs/architecture + Python in backend/src + materialized deleted Python at /tmp/oldpy; the audit gates deletion of backend/src).

All 25 areas were audited under ${AREA_DIR}/ as <key>.md (investigation) and <key>.verify.md (independent verification). READ THEM ALL — both files per area. Area keys:
${ALL_KEYS.map((k) => `  - ${k}`).join('\n')}

Produce ONE authoritative report at ${reportPath}:
1. Executive summary — how faithfully does the Rust port reproduce the framework's KEY DYNAMICS? Headline risks (most likely real bugs / missed dynamics).
2. CROSS-DOMAIN DISPARITY TABLE ranked by severity: | severity | domain | area | invariant/dynamic | python anchor | rust status + anchor | verifier verdict | suggested fix |. When the verifier's independent_status disagrees with the investigator, PREFER the verifier and mark the disagreement. Pull EVERY high/medium disparity from the per-area files.
3. Per-domain detail (## Sandbox, ## Agent-core): confirmed disparities, bugs, missing dynamics — each with file:line on both sides.
4. Cross-cutting invariants — reconcile across the areas that touch them: advisor-pass-before-terminal (advisor), no-terminal-while-background-running (background_supervisor), workflow depth<=2 + planner@depth-2-cannot-defer (deferred_goal_depth), isolated-workspace-never-OCC-merged (isolated_workspace), terminal-tool-called-alone (terminal_tools), ephemeral upperdir->OCC merge & discard (ephemeral_workspace + occ). State the single source of truth + final status for each.
5. Coverage matrix — every area x final verdict, flag any 'unproven' as a manual-follow-up gap. Also flag any checklist-vs-code disagreements (e.g. OCC "outside-workspace direct merge" is not actually an OCC route).
6. Prioritized recommended fixes + suggested tests.

Anchor every claim in file:line. This report is the deliverable. After writing it, reply with a <=12-line executive summary as plain text.`

let synthOut = null
for (let attempt = 1; attempt <= 2 && !synthOut; attempt++) {
  try {
    synthOut = await agent(synthPrompt, { label: `synthesize#${attempt}`, phase: 'Synthesize' })
  } catch (e) {
    log(`synthesis attempt ${attempt} failed: ${String(e).slice(0, 120)}`)
  }
}

return { reportPath, synthesized: !!synthOut, summary: synthOut || 'synthesis failed twice — run synthesis manually from the area files' }
