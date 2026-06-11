# EOS Agent Core Rust to TypeScript Migration - Phase 06 Question Escalation

Status: Proposed
Date: 2026-06-11
Owner: eos-agent-core
Migration direction: Rust -> TypeScript
Project path: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core`
Base spec: `phase-05-workflow-orchestration_SPEC.md` as amended by
`phase-05.1-workflow-context-redesign_SPEC.md` (Phase 06 lands after that
combined effort; §3 records its amendments to their seams)
Reference notes: `knowledge/ask-user-question-tool.md`,
`knowledge/inter-agent-messaging-protocol.md` (the Claude Code observations
this design is checked against)
Depends on: Phase 05 + 05.1 (`@eos/workflow` launcher/bindings, context
universe, disk mirror), Phase 04.9 (`NotificationInbox`, trigger rules),
Phase 04.5 (`@eos/agent-runtime` profiles, per-run inbox/supervisor, run
registry), Phase 04 (`@eos/tool`), Phase 03 (`@eos/engine`), Phase 02
(`@eos/contracts`)

## 1. Intent

Agents sometimes cannot proceed without information only their delegator
has. Phase 06 adds one escalation channel for exactly that case: a blocking
`ask_question` held by the child side of a delegation edge, and an
`answer_question` held by the agent parents. Questions flow up the
delegation tree (planner -> delegating main/worker -> ... -> main -> user),
answers flow back down as the blocked tool call's own result, and the main
agent acts as the single human-facing funnel: it absorbs the questions it
can answer and escalates the rest.

The design separates **dialogue** from **facts**. Everything that can be a
fact - progress, outcomes, dependency results - already rides the Phase
05/05.1 context store and typed notifications, reply-free. Dialogue
survives only where two-way judgment is genuinely required (clarification),
and there it is serialized to at most one pending question per edge by the
existing structural guards. No agent ever holds an address, monitors a
mailbox, or schedules its own polling; the contrast with Claude Code's
SendMessage/mailbox protocol is deliberate and recorded in §2.

Worker runs never ask: their parent planner run is settled before any
worker launches. A child workflow blocked on a question its worker-delegator
cannot answer fails upward instead - the question climbs one level per
failed attempt, landing exactly at a level that holds `ask_question`, and
the eventual answer lands durably in the retried `work_item_spec`. Answered
clarifications persist as append-only workflow rows, so retry planners
inherit them instead of re-asking.

## 2. Design Decisions

1. **Questions ride the delegation tree; the route is structural, never an
   input.** Two edges exist: user -> main, served by an injected
   `UserQuestionPort`, and delegating run -> planner, served by a
   `QuestionBinding` on the launch options beside `SubmissionBinding`
   (05.1 §2.19/§2.21). `ask_question` carries no addressee - the binding or
   port fixed at launch is the route. `answer_question` carries only
   `question_id`, resolved server-side against the pending-question
   registry, with the recorded delegating run as the only authorized
   answerer. Every schema parameter removed is a hallucination surface
   removed (contrast: Claude Code's `SendMessage` needs `to:`, a name
   registry held in model context, and routing fallbacks to make
   model-supplied addresses survivable). The registry absorbs any future
   multiplicity: relaxing the one-open-workflow guard would not change
   either schema.
2. **Tool exposure equals the existence of a live answer path.**
   `ask_question` is assembled for a run iff someone can actually answer:
   main when `userQuestionPort` is present (a headless runtime omits the
   tool - the Claude Code `isEnabled()`/`requiresUserInteraction` parity),
   and a planner whose launch carried a `QuestionBinding`. The launcher
   builds that binding per claimed plan iff the delegating run's agent kind
   holds `answer_question` (main, worker); a child of any other delegator
   never sees the tool. A blocked-forever question is unrepresentable by
   construction.
3. **Kind-validated assignment, enforced at startup.** `ask_question` is
   valid only on `main`/`planner` profiles, `answer_question` only on
   `main`/`worker`; `advisor` and `subagent` profiles get neither (they do
   bounded work and report structured failure instead).
   `createAgentRuntime` rejects a violating profile before any run starts -
   the Phase 04.5 static-validation discipline, and the Claude Code lesson
   that the strip must be central (`ALL_AGENT_DISALLOWED_TOOLS` checked
   before any carve-out), not per-profile convention.
4. **`ask_question` blocks; the tool result is the answer.** The
   question-as-background-session alternative (non-blocking, answer as a
   settled notification) was considered and rejected: it hands discretion
   to the asker - the one party that just certified it lacks information -
   invites speculative work whose side effects need unwinding, and lands
   the answer arbitrarily far from the question in the transcript. Blocking
   makes "do not proceed without the answer" structure rather than prompt:
   the suspension is intra-turn (the execute awaits a promise, exactly the
   05.1 §2.19 `binding.submit` shape), costs zero tokens and zero context
   growth, and the answer lands adjacent to its question. Background work
   never pauses - only the asker's reasoning does; settlements buffer
   losslessly and drain after the answer (§2.13). A non-blocking variant
   is deliberately out of scope until an agent kind with genuinely
   concurrent workstreams exists.
5. **At most one pending question per edge - an invariant consequence, not
   configuration.** One open workflow per run -> one live attempt -> one
   live plan -> one live planner run -> blocking ask. `question_id`
   survives anyway: audit, expired/stale-answer detection, and schema
   stability if any link of the chain ever relaxes.
6. **Answers are a closed union; decline is first-class; every path
   resolves the block.** The parent returns `answered` or
   `declined(guidance)`; the service synthesizes `expired` (configured
   timeout) and `cancelled` (workflow cancel, caller dispose,
   `attempt_failed` sibling cancellation, death synthesis marks the row).
   The asker's prompt binds it to handle `declined`/`expired` by proceeding
   on a stated assumption recorded in its output. A blocked call always
   terminates; combined with §2.2 there is no unanswerable, unkillable
   question state.
7. **Timeout is opt-in configuration, not a default clock.**
   `workflowQuestionTimeoutMs?` is absent by default. A chained escalation
   (planner asks main, main asks the user) makes any fixed workflow-edge
   default wrong: the inner timer would fire while the human deliberates,
   wasting the eventual answer. Wedge protection is ownership-based
   instead, matching 05.1 §2.21's no-in-memory-clockwork philosophy: the
   disposal cascade cancels the workflow (and resolves its pending asks)
   whenever the caller finishes, cancellation paths resolve the block, and
   a 04.9 trigger rule can nudge a parent sitting on `question_pending`.
   When the timeout is configured, expiry resolves the blocked call
   `{ kind: "expired" }` and a late `answer_question` gets
   `{ ok: false, error }` for in-run correction. The human edge never has
   a timeout: main's decline is the escape hatch.
8. **Question rows are append-only workflow facts keyed to the asking
   plan.** Inserted `Pending` at ask time, resolved exactly once through a
   terminal-guarded transaction (the §2.22 idempotency discipline), and
   re-projected into the mirror - a human tailing a workflow sees the
   pending question. Keying to the plan rides the 05.1 consistency
   machinery for free: a refocus archives superseded Q&A with its drifted
   attempt, with no archive mutation. Answered and declined clarifications
   from attempts with `is_consistent_with_iteration_focus = true` surface
   in the retry planner's `workflow_context`, so answers survive episodic
   planner runs instead of dying with a transcript.
9. **The human edge is port-mediated; there is no answer tool there.**
   Claude Code's AskUserQuestion shows the shape: the dialog resolves the
   tool call's own round-trip, and no `AnswerQuestion` tool exists
   anywhere. `UserQuestionPort.ask` resolves with the user's answer or
   decline; pending state is in-memory; the Phase 04.7 `RunLog` transcript
   is the audit record. Restart re-arming (deterministic `question_id`
   minted from the `tool_use_id`, durable row, re-present on resume) is a
   named deferred seam blocked on run resumption, which does not exist
   yet - a durable row buys nothing until a run can consume it.
10. **Workers never ask; a blocked child workflow fails upward.** The
    worker's parent planner run settles before workers launch, so no
    answer path exists (§2.2 keeps the tool away rather than letting it
    hang). The worker triages its child planner's question: answer it from
    its work-item context, decline with proceed-on-assumption guidance for
    low-stakes questions, or - for blocking ones - cancel the child
    workflow (`cancel_background_session`) and submit
    `is_pass: false` quoting the question verbatim in the fail reason. The
    submission guard already forces cancel-before-submit mechanically. The
    default retry-planner directive gains one line: a `fail_reason`
    containing an unanswered question should be escalated via
    `ask_question` before re-planning. The answer then lands durably in
    the retried `work_item_spec` and flows to the next child workflow
    through the ordinary context pipeline. Each such escalation costs one
    attempt; chronic occurrence is a work-item-spec quality signal, not
    grounds for more machinery.
11. **Question payloads are structured and self-contained; clarification
    only.** The parent never sees the asker's transcript (the
    context-discipline that prevents fan-in explosions), so the payload
    must carry everything needed to answer or escalate: `summary` (one
    line, for notification/UI previews), `question`, `why_blocking`, and
    optional 2-4 `options` with the recommended option first. Structure is
    also what lets main forward a planner's question verbatim into its own
    `ask_question` - no paraphrase loss per hop. One question per call
    (the blocking ask serializes anyway; Claude Code's 1-4 batching buys
    nothing here). The tool prompt forbids approval use: terminal-outcome
    approval stays on the advisory gate (Phase 04.8), mirroring Claude
    Code's AskUserQuestion/ExitPlanMode division of labor.
12. **`question_pending` is a publisher-side notification; the inbox is
    untouched.** The payload is self-contained (full question payload plus
    `question_id` and `workflow_id`) because the read/query tools are
    deferred (05.1 §2.18) - the parent must be able to answer from the
    notification alone. Delivery is the existing machinery end to end:
    publish wakes a parked delegator through `waitForNext`, the loop
    drains at the boundary, and the parked-but-resident delegator (it
    cannot finish past an open workflow) answers on its next turn. The
    workflow session's `describe()` additionally surfaces a pending
    question (`waiting on question <id>: <summary>`) so
    `list_background_sessions` explains a long park.
13. **The loop's notification drain aggregates into one message.** The
    `@eos/engine` drain site currently appends one user message per
    drained note; after a long block those arrive as a message flood
    behind the answer. The drain now appends a single user message whose
    content concatenates the drained notes' blocks. Nothing else changes:
    per-key supersession already lives in `publish`, tag bookkeeping
    already fires once per drain, and answer-first resume ordering is
    already structural - the conversation's only writers are the loop's
    three append sites, so a mid-execution publish can never interleave
    between `tool_use` and `tool_result`.
14. **`ask_question` is batch-solo.** The batch executor awaits the whole
    batch before appending any result, so a blocked ask sharing a batch
    would hold finished siblings' results hostage. The definition sets the
    existing `isBatchExecutionForbidden` flag (whole-batch rejection with
    solo recovery, Phase 04). `answer_question` stays batch-safe.

## 3. Phase 05/05.1 Amendments

Recorded deltas against the combined Phase 05 + 05.1 surface; everything
not listed is implemented as written there.

| Base item | Amendment | Decision |
| --- | --- | --- |
| 05.1 §2.19/§2.21 `AgentLaunchPort.launch(agentName, initialMessages, { submission, signal }?)` | options gains `question?: QuestionBinding`, built per claimed plan iff the delegating run's kind holds `answer_question` | §2.1, §2.2 |
| 05.1 §7 `WorkflowContextSnapshot` plan object | gains `questions: []` (id, payload fields, status, response) | §2.8 |
| 05.1 §9 path universe, plan folder | gains `question_<id>/question.md` + `answer.md`; drifted attempts carry their questions into `archived/` by construction | §2.8 |
| 05.1 §2.13 default composition policy, retry planner | context includes answered/declined clarifications from attempts with `is_consistent_with_iteration_focus = true`; the directive gains the unanswered-question escalation line | §2.8, §2.10 |
| 05.1 §2.20/§2.22 cancel cascades and death/compose synthesis | `cancelPlan` resolves its pending questions `Cancelled`; death synthesis against a `Running` plan marks its pending questions `Expired`; both resolve the blocked ask | §2.6 |
| 05.1 §2.18 the workflow tool family is `delegate_workflow` alone | unchanged; the question pair is its own family (`tools/question/`), bound per run, not a workflow-named tool surface | §2.1 |
| 04.5 profile static validation | gains the §2.3 kind table for the question tools | §2.3 |
| 03/04.5 engine loop drain (one message per drained note) | one aggregated user message per drain | §2.13 |

## 4. Scope

In scope:

- `@eos/contracts`: `QuestionId`, the §6 question payload / response /
  outcome schemas, `AnswerQuestionInput`, the `QuestionBinding` and
  `UserQuestionPort` types, the `WorkflowContextSnapshot` plan delta,
- `@eos/db`: the append-only `questions` table and its row queries,
- `@eos/workflow`: the `question/` entity module (state, context renders,
  terminal-guarded resolution transition), the service-side pending
  registry + `ask`/`answer` methods + question router + optional timeout,
  the `QuestionBinding` construction on the launcher, the §7 context
  universe and snapshot deltas, the §2.13 default-policy delta, mirror
  coverage of question files,
- `@eos/tool`: `tools/question/ask-question.ts` and
  `tools/question/answer-question.ts` over bound functions, `ask_question`
  batch-solo, the workflow session `describe()` pending-question line,
- `@eos/engine`: the drain-site aggregation (§2.13),
- `@eos/agent-runtime`: `userQuestionPort?`, `workflowQuestionTimeoutMs?`,
  the §2.3 profile kind validation, threading `question?` through the
  launch-port adapter into per-run tool assembly, wiring the question
  router to per-run inboxes.

Out of scope, recorded as decisions rather than omissions:

- human-edge restart re-arming (deferred seam: run resumption plus
  `tool_use_id`-deterministic `question_id` and a durable row at the human
  edge; §2.9),
- a non-blocking / background-session question variant (§2.4),
- parent-initiated mid-flight steering of a running workflow - a changed
  goal is cancel + re-delegate by design; the answer payload is the only
  downward information channel and it is child-initiated,
- multi-question payloads (§2.11), lateral agent-to-agent messaging,
  question tools on `advisor`/`subagent` kinds (§2.3),
- a packaged 04.9 reminder-rule script nudging a parent on
  `question_pending` (expressible today in `notification_rules.json`; no
  new machinery, ships as a reference script when wanted).

## 5. Question Topology and Tool Matrix

```text
user (human)
  ▲ UserQuestionPort.ask                     no tool; the host resolves the
  │ (blocking tool round-trip)               call - CC AskUserQuestion shape
main ◄────────────────────────┐
  ▲ answer_question(qid, …)   │ question_pending notification
  │                           │
  │ QuestionBinding.ask       │ (blocking)
planner₁ ─ settles before workers launch
worker₁ ◄─────────────────────┐
  ▲ no ask path: triage,      │ question_pending
  │ then fail upward (§2.10)  │
planner₂   QuestionBinding.ask (blocking)
```

| Agent kind | `ask_question` | `answer_question` | Ask exposed when | When no answer path |
| --- | --- | --- | --- | --- |
| main | conditional | yes | `userQuestionPort` injected | headless: tool omitted; main declines child questions it cannot resolve |
| planner | conditional | no | launch carried `QuestionBinding` (delegator kind is main/worker) | tool omitted |
| worker | no | yes | - | triage + fail upward (§2.10) |
| advisor / subagent | no | no | - | structured failure result |

The single-pending invariant chain (§2.5): one open workflow per run ->
one live attempt -> one live plan -> one live planner run -> blocking ask.
Every link is an existing Phase 05/05.1 structural guard.

## 6. Contracts (`@eos/contracts`)

```ts
const QuestionOptionSchema = z.object({
  label: z.string().min(1),
  description: z.string().min(1),
});

const QuestionPayloadSchema = z.object({
  summary: z.string().min(1),        // one line; notification/UI preview
  question: z.string().min(1),
  why_blocking: z.string().min(1),
  options: z.array(QuestionOptionSchema).min(2).max(4).optional(),
                                     // recommended option first by convention
});

const QuestionResponseSchema = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("answered"), answer: z.string().min(1) }),
  z.object({ kind: z.literal("declined"), guidance: z.string().min(1) }),
]);

type QuestionOutcome =
  | z.infer<typeof QuestionResponseSchema>
  | { kind: "expired" }                    // configured timeout only (§2.7)
  | { kind: "cancelled"; reason: string }; // cancel cascade / dispose (§2.6)

const AnswerQuestionInputSchema = z.object({
  question_id: QuestionIdSchema,           // minted typed id (05 §2.4)
  response: QuestionResponseSchema,
});

interface QuestionBinding {
  ask(payload: QuestionPayload): Promise<QuestionOutcome>;
}
// AgentLaunchPort.launch(agentName, initialMessages,
//                        { submission, signal, question? }?)

interface UserQuestionPort {
  ask(
    payload: QuestionPayload,
    ctx: { run_id: AgentRunId },
  ): Promise<z.infer<typeof QuestionResponseSchema>>;
}
```

`WorkflowContextSnapshot` delta - the plan object inside
`WorkflowContextAttempt` gains:

```ts
questions: Array<{
  id: string;
  summary: string;
  question: string;
  why_blocking: string;
  options: Array<{ label: string; description: string }> | null;
  status: "Pending" | "Answered" | "Declined" | "Expired" | "Cancelled";
  response: string | null;           // answer text or decline guidance
}>;
```

The `question_pending` notification payload is the full question payload
plus `question_id` and `workflow_id` - self-contained because the read
tools are deferred (§2.12).

## 7. Store, Context Universe, and Launch-Context Deltas

```text
questions   id PK, workflow_id, plan_id, payload (JSON: summary/question/
            why_blocking/options), status ('Pending'|'Answered'|'Declined'|
            'Expired'|'Cancelled'), response, asked_at, resolved_at
```

Rows are append-only; the only mutation is the single
`Pending -> terminal` resolution, behind the same terminal-guard
idempotency as the entity transitions (§2.22 discipline: competing
answer/timeout/cancel resolutions accept exactly one winner; losers no-op
or return in-run errors). Resolution re-projects the mirror.

Path universe delta (one fact, one path; status rides listings):

```text
attempt_<id>/plan_<id>/
  summary.md
  question_<id>/
    question.md      derived composition: question + why_blocking + options
    answer.md        absent while Pending; answer text or decline guidance
```

Pending shows as `question.md` with no `answer.md` - the absent-field rule
already expresses it. Drifted attempts relocate their question folders
under `archived/` by construction, since they live under the plan folder.

Launch-context delta: `buildPlannerContextInput` populates
`plan.questions` for every attempt in the snapshot. The default retry
policy (05.1 §2.13) includes answered/declined clarifications from
attempts with `is_consistent_with_iteration_focus = true` and adds one
directive line: a `fail_reason` containing an unanswered question should
be escalated via `ask_question` before re-planning. Context scripts
receive the same facts through the snapshot and remain free to place them
differently. The worker policy is unchanged - answers reach workers baked
into `work_item_spec`, not as a parallel channel.

## 8. Question Lifecycle

```text
planner run                    WorkflowService                 delegating run
ask_question(payload)
  └─ binding.ask ───────────►  mint question_id
                               INSERT questions row (Pending)
     (blocked await,           mirror re-projection
      intra-turn,              publish question_pending ────►  parked run wakes
      zero tokens)             arm timer iff configured        (waitForNext);
                                                               drains at loop
                                                               boundary; answers
                                                               or escalates via
                                                               its OWN ask first
                                                               answer_question(qid,
                                                                 response)
                               resolve: terminal-guarded  ◄────┘
                               UPDATE row; mirror; settle
  tool result = outcome  ◄──── the pending promise
```

Resolution table - every path settles both the row and the blocked call:

| Event | Row | Blocked asker | Late `answer_question` |
| --- | --- | --- | --- |
| parent answers / declines | `Answered` / `Declined` | resolves with the response | - |
| configured timeout fires | `Expired` | `{ kind: "expired" }` | `{ ok: false, error }` in-run |
| workflow cancel / caller dispose | `Cancelled` | `{ kind: "cancelled" }`; the run is also interrupted by the workflow signal | `{ ok: false, error }` |
| `attempt_failed` sibling cancel (05.1 §2.20) | `Cancelled` | same | `{ ok: false, error }` |
| asker run dies (death synthesis) | `Expired` | run already settled | `{ ok: false, error }` |
| unknown / already-terminal qid, wrong answerer run | untouched (guard) | - | `{ ok: false, error }` in-run |

Chained escalation: main receives `question_pending`, judges it, and
either answers from its own context or forwards the payload verbatim into
its own `ask_question` (the human edge). Nested blocking is safe - the
ask graph is the delegation tree, acyclic by construction. The fail-upward
path (§2.10) handles the one edge with no live answer chain:

```text
planner₂ ── ask ──► worker₁ (cannot answer, judges it blocking)
                      │ cancel_background_session(workflow)   ← submission
                      │ submit_worker_outcome(is_pass: false,    guard forces
                      ▼   fail_reason quotes the question)       this order
              attempt fails → retry → fresh planner₁ sees the fail_reason,
              holds ask_question → escalates → answer lands in the retried
              work_item_spec → the next child workflow never needs to ask
```

## 9. Tool Family (`@eos/tool`)

Two files: `packages/tool/src/tools/question/ask-question.ts` (tool
`ask_question`) and `answer-question.ts` (tool `answer_question`), over
bound functions in the house factory shape:

```ts
function questionTools(
  ask: ((payload: QuestionPayload) => Promise<QuestionOutcome>) | undefined,
  answer:
    | ((questionId: QuestionId, response: QuestionResponse,
        answerer: AgentRunId) =>
        Promise<{ ok: true } | { ok: false; error: string }>)
    | undefined,
): ToolDefinition[];
```

- An `undefined` bound function omits that tool from the returned set -
  this is the §2.2 conditional-exposure mechanism for both edges.
- `ask_question`: input `QuestionPayloadSchema`; execute awaits
  `ask(payload)` and returns the `QuestionOutcome` as content (an
  `expired`/`cancelled`/`declined` outcome is NOT `isError` - it is a
  legitimate instruction to proceed on a stated assumption). Sets
  `isBatchExecutionForbidden` (§2.14). Not advisory-required. The prompt
  covers: self-containment (the reader has none of your context), one
  recommended option first, clarification-only (approval rides the
  advisory gate), and the decline/expired protocol.
- `answer_question`: input `AnswerQuestionInputSchema`; execute awaits
  `answer(question_id, response, ctx.meta.run.run_id)`; `{ ok: false }`
  maps to an `isError` result for in-run correction - unknown id, already
  terminal, expired, or an answerer that is not the recorded delegating
  run. Batch-safe.
- The workflow background session's `describe()` gains a pending-question
  line (`waiting on question <id>: <summary>`) while one exists, so
  `list_background_sessions` explains a long park (§2.12).

## 10. Engine Deltas (`@eos/engine`)

One change: the loop's notification drain appends a single user message
per drain, concatenating the drained notes' content blocks in queue order,
instead of one message per note. `publish`-time rendering, per-key
supersession, tag bookkeeping, and steers-before-notifications priority
are unchanged. The answer-first resume ordering needs no work: the
conversation's only writers are the loop's existing append sites, so a
notification published mid-tool-execution can only land at the next loop
boundary, strictly after the tool result.

## 11. Runtime Wiring Deltas (`@eos/agent-runtime`)

- `AgentRuntimeDependencies` gains `userQuestionPort?` and
  `workflowQuestionTimeoutMs?` (both optional; both absent by default).
- Profile static validation gains the §2.3 kind table; a violating profile
  fails `createAgentRuntime`.
- Per-run tool assembly: a main run gets `ask_question` bound to the port
  (when present) and `answer_question` bound to the workflow service; a
  workflow-launched planner gets `ask_question` bound to its launch's
  `QuestionBinding` (when present); a workflow-launched worker gets
  `answer_question`. The launch-port adapter threads `options.question`
  exactly as it threads `options.submission`.
- `WorkflowService` gains: the in-memory pending registry
  (`question_id -> { resolve, timer? }`), `ask` (insert row, mirror,
  publish, arm optional timer, return the promise), `answer`
  (terminal-guarded resolution + mirror + settle), and the question
  router - an injected `publishToRun(run_id, payload)` capability the
  runtime builds over its per-run inbox registry (Phase 04.5 owns the
  run -> inbox mapping; the workflow package never sees an inbox type).
- Pending registry entries are process state like session handles: a
  process death settles nothing in memory, and recovery is the existing
  death-synthesis path marking `Pending` rows `Expired` (§2.6, §2.9).

## 12. Workspace Changes

```text
packages/contracts/src/questions.ts   §6 schemas + QuestionId
packages/workflow/src/
├─ question/
│  ├─ state.ts          row DTO, status union, pending views over the tree
│  ├─ context.ts        question.md / answer.md derived renders (§7)
│  └─ transitions.ts    createQuestion; resolveQuestion - one function,
│                       answer/decline/expire/cancel as branches behind one
│                       terminal guard; cancelPlan calls it (§2.6)
├─ launcher.ts          builds QuestionBinding per claimed plan iff the
│                       delegator kind holds answer_question (§2.2)
└─ service.ts           pending registry, ask/answer methods, question
                        router dependency, optional timeout
packages/tool/src/tools/question/
├─ ask-question.ts      tool ask_question (batch-solo)
└─ answer-question.ts   tool answer_question
packages/engine/src/agent-loop.ts     drain-site aggregation (§10)
```

`@eos/db` adds the `questions` migration and row queries consumed by
`loadWorkflowTree`; `@eos/agent-runtime` adds the §11 wiring. The
`question/` module follows the entity-module shape and the adjacency rule:
plan -> question downward only; `index.ts` re-exports nothing from it. No
new third-party dependencies; the dependency graph is unchanged.

## 13. Migration Steps and Progress

| # | Step | Verify | Status |
| --- | --- | --- | --- |
| 1 | Contracts: payload/response/outcome schemas, `QuestionId`, binding + port types, snapshot delta | §14 case 1 | Planned |
| 2 | `@eos/db` + `question/` entity module: table, terminal-guarded resolution, renders | §14 cases 2-3 on `:memory:` | Planned |
| 3 | `@eos/workflow` service: pending registry, ask/answer, router, timeout, binding on the launcher, cancel/death coverage | §14 cases 4-6, engine-free | Planned |
| 4 | Context deltas: snapshot `questions`, retry-policy clarifications + directive line, mirror coverage | §14 case 7 | Planned |
| 5 | `@eos/tool`: the question pair, batch-solo, `describe()` line; runtime kind validation + port wiring | §14 cases 8-9 | Planned |
| 6 | `@eos/engine`: drain aggregation | §14 case 10 | Planned |
| 7 | Runtime end-to-end + index row | §14 cases 11-12; `pnpm run check`; `git diff --stat -- agent-core` empty | Planned |

## 14. Verification

Same harness rules as Phase 05.1 §16: scripted `AgentLaunchPort`,
`:memory:` databases, engine-free except the end-to-end case.

| # | Case | Asserts |
| --- | --- | --- |
| 1 | Contracts | payload accepts/rejects documented shapes (options bounds 2-4, required summary/why_blocking); response union requires `answer` xor `guidance`; `AnswerQuestionInput` requires a typed `question_id`; snapshot plan `questions` shape round-trips |
| 2 | Store + resolution guard | rows append-only; exactly one of competing answer/expire/cancel resolutions wins under the terminal guard and the others no-op or error; `loadWorkflowTree` exposes question views per attempt/plan |
| 3 | Projection | `question.md` composes question + why_blocking + options; `answer.md` absent while `Pending`, present after answer/decline; listing rows carry question status; a refocus relocates the question folder under `archived/` with its attempt; mirror matches renders byte-for-byte including question files |
| 4 | Ask/answer round-trip | engine-free: `binding.ask` inserts `Pending`, publishes a self-contained `question_pending`, and blocks; `answer` resolves the promise with the response and the row terminal; wrong-answerer run, unknown id, and already-terminal id each return `{ ok: false, error }` without mutating |
| 5 | Decline, timeout, config default | declined guidance reaches the asker as a non-error result; with `workflowQuestionTimeoutMs` configured, expiry resolves `expired` and marks the row, and a late answer errors in-run; with it absent no timer is armed |
| 6 | Cancellation + death | workflow cancel, caller dispose, and `attempt_failed` sibling cancellation each resolve a pending ask `cancelled` and mark the row, with the child run interrupted via the workflow signal and no entity left `Running`; death synthesis against a `Running` plan marks its pending questions `Expired` |
| 7 | Retry context | answered/declined clarifications from consistent attempts appear in the retry planner's snapshot and the default retry policy's messages; superseded attempts' questions are omitted; the directive carries the unanswered-question escalation line |
| 8 | Exposure + kind validation | a planner launch without `QuestionBinding` and a main run without `userQuestionPort` assemble no `ask_question`; a worker assembles `answer_question` only; profiles assigning `ask_question` to a worker/advisor/subagent or `answer_question` to a planner fail `createAgentRuntime` |
| 9 | Tool behavior | `ask_question` rejects batched execution and recovers solo; `expired`/`cancelled`/`declined` outcomes are non-error results; `answer_question` error table is in-run correctable; the workflow session `describe()` names the pending question |
| 10 | Drain aggregation | N pending notes drain as one user message preserving queue order and firing every tag once; the answer (tool result) precedes the aggregated drain in the transcript; per-key supersession still collapses repeats before the drain |
| 11 | Chained escalation end-to-end | MockLlm runtime: planner asks; the parked delegating main wakes on `question_pending`, forwards the payload verbatim through `ask_question` to a scripted `UserQuestionPort`, answers the planner; transcript inspection shows the answer adjacent to the planner's `tool_use` and the buffered notifications behind it |
| 12 | Worker fail-upward protocol | a worker with an open child workflow cannot submit (guard), cancels, then submits `is_pass: false` quoting the question; the retry planner's snapshot carries the fail reason and its run holds `ask_question` |

Commands (unchanged):

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core
pnpm install
pnpm run check
```

- Rust boundary hygiene: `git diff --stat -- agent-core` stays empty.
- Docs hygiene: `git diff --check -- docs/plans/agent-core-rust-to-typescript-migration eos-agent-core`.

## 15. Coexistence and Rollback

- Coexistence: Phase 05/05.1 have no landed implementation; this spec is
  paper-only until it lands after (or with) that combined effort. The Rust
  implementation remains live and unchanged throughout.
- Rollback: delete this spec and its index row; Phase 05 + 05.1 stand as
  written - no base-spec text is edited by this phase, only amended via §3.

## 16. Acceptance Criteria

Phase 06 is accepted when:

- the question topology is exactly the §5 matrix, enforced at startup by
  kind validation and at assembly by answer-path-conditional exposure - no
  agent can hold a question tool its edge cannot serve,
- `ask_question` blocks intra-turn with the answer as its tool result, and
  every pending question resolves through one of: answer, decline,
  configured expiry, cancellation, or death synthesis - no wedged
  `Pending` row and no permanently blocked run is constructible,
- questions are append-only rows keyed to the asking plan, projected as
  `question.md`/`answer.md` in the live universe and mirror, archived with
  drifted attempts by derivation, and answered clarifications from
  consistent attempts reach retry planners through `workflow_context`,
- neither tool carries an addressee: the launch binding and the injected
  port are the only routes, and `answer_question` authorizes by recorded
  delegating run, server-side,
- the human edge is port-mediated with no answer tool, omitted entirely
  when no port is injected, and never armed with a timeout,
- the worker fail-upward protocol is expressed in the default retry
  directive and the worker prompt, with the submission guard enforcing
  cancel-before-submit,
- the notification drain emits one aggregated message per drain with
  ordering and delivery bookkeeping unchanged, and `ask_question` is
  batch-solo,
- `pnpm run check` passes with the §14 suite, `git diff --stat --
  agent-core` stays empty, and the migration `index.md` lists Phase 06
  with status and verification.
