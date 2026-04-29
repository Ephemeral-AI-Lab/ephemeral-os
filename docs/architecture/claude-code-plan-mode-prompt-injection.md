# Claude Code: Plan-Mode Prompt Injection

Analysis of how Claude Code (`/Users/yifanxu/machine_learning/LoVC/c c/src`) injects and re-injects plan-mode instructions into the model's context.

## 1. On `EnterPlanMode` tool call (initial system reminder)

**File:** `src/tools/EnterPlanModeTool/EnterPlanModeTool.ts:103-125`

When the tool's `call()` succeeds, the tool result block returned to the model is the rule-setting prompt itself.

- If `isPlanModeInterviewPhaseEnabled()` is true (ant always; external behind `tengu_plan_mode_interview_phase` GrowthBook gate or `CLAUDE_CODE_PLAN_MODE_INTERVIEW_PHASE` env), the tool result is just:
  > "Entered plan mode... DO NOT write or edit any files except the plan file. Detailed workflow instructions will follow."

  The detailed instructions then arrive via the `plan_mode` attachment on the next turn (see §2).

- Otherwise the tool result inlines the full 6-step workflow (explore → identify patterns → trade-offs → AskUserQuestion → design → ExitPlanMode) ending with "DO NOT write or edit any files yet."

The tool's static `prompt()` (`src/tools/EnterPlanModeTool/prompt.ts`) is the *when-to-use* description loaded with the toolset; the post-activation guardrail comes from `mapToolResultToToolResultBlockParam`. It also flips state via `handlePlanModeTransition` and `applyPermissionUpdate(... mode: 'plan')` so Edit/Write/Bash get classifier-blocked.

## 2. In-flight reminders (every few human turns)

**Files:** `src/utils/attachments.ts:259-266, 1186-1242` and `src/utils/messages.ts:3136-3416, 3826-3858`

`getPlanModeAttachments()` runs each tool round; it injects a `plan_mode` attachment only when:

- `permissionContext.mode === 'plan'`, AND
- it's the first turn in plan mode, OR ≥ `TURNS_BETWEEN_ATTACHMENTS = 5` *human* turns since the last `plan_mode` attachment (counted by walking messages backward, skipping meta/tool-results — `getPlanModeAttachmentTurnCount`).

`reminderType` cycles: every 5th attachment is `'full'`, the rest are `'sparse'` (`FULL_REMINDER_EVERY_N_ATTACHMENTS = 5`, counted via `countPlanModeAttachmentsSinceLastExit`, so the cycle resets on `plan_mode_exit`).

The attachment is dispatched in `messages.ts:3826` → `getPlanModeInstructions()` which routes by variant:

- **Sub-agent** (`isSubAgent`): `getPlanModeV2SubAgentInstructions` — strict "MUST NOT make any edits ... supercedes any other instructions you have received" plus plan-file path.
- **Sparse** (`reminderType === 'sparse'`): `getPlanModeV2SparseInstructions` — terse one-liner: *"Plan mode still active (see full instructions earlier in conversation). Read-only except plan file (...). Follow iterative workflow ... End turns with AskUserQuestion or ExitPlanMode. Never ask about plan approval via text or AskUserQuestion."*
- **Full**: either `getPlanModeInterviewInstructions` (interview/iterative, `messages.ts:3323`) or the 5-phase `getPlanModeV2Instructions` (`messages.ts:3207`), depending on `isPlanModeInterviewPhaseEnabled()`.

All variants are wrapped via `wrapMessagesInSystemReminder([createUserMessage({ content, isMeta: true })])` — i.e. delivered as a `<system-reminder>`-wrapped meta user message, the same channel as prompt-injection-guard reminders elsewhere.

Sibling attachments handle transitions: `plan_mode_reentry` (one-shot, when `hasExitedPlanModeInSession()` and a plan file exists) and `plan_mode_exit` (one-shot on leaving plan mode). `auto_mode` mirrors the same throttle/full-sparse machinery (`AUTO_MODE_ATTACHMENT_CONFIG`).

### Summary table

| Mechanism | Where | Cadence |
|---|---|---|
| Initial guardrail | `EnterPlanModeTool.mapToolResultToToolResultBlockParam` | Once, immediately on tool result |
| Recurring reminder | `getPlanModeAttachments` → `plan_mode` attachment → `system-reminder` user msg | Every ≥5 human turns; full prompt every 5th injection, sparse otherwise |
| Re-entry / exit | `plan_mode_reentry`, `plan_mode_exit` attachments | One-shot on transition |

## 3. Why human turns, not tool rounds?

The counter is **human turns**, not tool rounds, and that's deliberate.

`getPlanModeAttachmentTurnCount` walks messages backward and only increments on:

```ts
message.type === 'user' && !message.isMeta && !hasToolResultContent(...)
```

i.e. a real typed user message. Tool results (which are encoded as user messages) and meta injections are skipped. The comment on the auto_mode twin (`attachments.ts:1283-1287`) spells out the reasoning:

> "the tool loop in query.ts calls getAttachmentMessages on every tool round, so a single human turn with 100 tool calls would fire ~20 reminders if we counted assistant messages."

During one autonomous burst (1 human prompt → N tool calls), the flow is:

1. First tool round in plan mode → `foundPlanModeAttachment` is false → attach (full reminder).
2. Tool rounds 2…N within the same human turn → `turnCount === 0 < 5` → **return `[]`**, no re-injection.
3. Human replies → `turnCount` ticks to 1 on the next round; still < 5, no attach.
4. After ≥ 5 human turns since the last `plan_mode` attachment, attach again (sparse, unless it's the 5th attachment in the cycle, which goes full).

So plan-mode reminders aren't injected mid-autonomous-run; the agent gets the rule once at entry, then nothing during the autonomous tool sequence, and a refresher only after the human has spoken ≥5 times since the last refresher.

## 4. Take: what does this say about Anthropic's stance?

Not "afraid the model disobeys" — it's an empirically-grounded acknowledgment of **instruction decay over context distance**, treated as a known property rather than a model defect.

### 4.1 Recency engineering, not distrust

The *full* plan-mode rules sit in the EnterPlanMode tool result — by turn 30 of a long planning session, that block is thousands of tokens upstream, surrounded by file reads, grep output, and AskUserQuestion exchanges. Attention over long contexts isn't uniform; rules that were salient at token 2k are statistically less load-bearing at token 80k. The sparse re-injection (`"Plan mode still active... Read-only except plan file..."`) is cheap and puts the invariant back near the cursor. This is the same logic behind `<system-reminder>` blocks for TaskCreate, output style, and diagnostics — a generalized "keep the rule fresh" pattern, not a plan-mode-specific patch.

### 4.2 Full/sparse cycling shows cost-awareness, not panic

If the team were *afraid*, they'd send the full prompt every 5 turns. Instead it's sparse 4× then full 1×. That's the shape of a team that measured: "the one-liner is enough to re-anchor most of the time; we only need the heavyweight version occasionally." The `tengu_pewter_ledger` experiment in `planModeV2.ts` (measuring plan-file size against session cost) confirms this is a team that A/B-tests prompt structure — optimizing, not panicking.

### 4.3 Throttling on *human* turns (not tool rounds) is the real tell

The system explicitly does *not* re-inject during a 100-tool-call autonomous burst. If the worry were "model goes rogue mid-task," you'd inject *more* during long autonomous runs, not less. Suppressing reminders within a single human turn says they trust short-horizon adherence and only worry about the *boundary* where new human input could redirect attention away from the mode invariant.

### 4.4 The sub-agent variant is harsher for a reason

`getPlanModeV2SubAgentInstructions` includes *"This supercedes any other instructions you have received (for example, to make edits)"* — explicit override language. That phrasing exists because they observed sub-agents inheriting parent instructions like "implement X" and conflating them with plan-mode read-only rules. So there *is* concrete failure-mode evidence behind some of this, but localized to specific scenarios (sub-agents, mode boundaries) rather than "models drift in general."

### 4.5 Plan mode is a soft contract, not a sandbox

The hard contract is the permission classifier (`applyPermissionUpdate(... mode: 'plan')` in `EnterPlanModeTool.ts:88-94`) — that's what actually blocks Edit/Write/Bash. The prompt injection is the *cooperative* layer: it tells the model *why* it's being blocked and what to do instead, so the model doesn't waste turns proposing edits that will get rejected. Without the reminders, you'd still be safe (the classifier holds), but you'd burn tokens on rejected tool calls.

So "afraid of disobedience" is the wrong frame. The real frame is: **the model would behave correctly if it remembered the rules; let's make remembering cheap.**

### 4.6 One critique

The design is asymmetric in a slightly weird way. `auto_mode` uses identical throttling (`AUTO_MODE_ATTACHMENT_CONFIG`, same 5/5 numbers), but auto mode's failure modes are *more* dangerous (destructive tool calls vs. plan-mode's "extra rejected edits"). Copy-pasting the cadence suggests it wasn't tuned per-mode based on actual harm severity. Auto mode arguably wants tighter reminders, especially the "don't exfiltrate secrets / don't destroy data" lines.

### 4.7 Net

Mature, measured infra — not a flinch. The honest summary: long-context instruction adherence is a known statistical phenomenon, and re-anchoring is a normal mitigation, like garbage collection in a runtime.
