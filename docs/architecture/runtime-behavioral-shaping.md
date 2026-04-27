# Runtime Behavioral Shaping for Harness Agents

How the harness layers transcript-aware notifications and runtime guardrails to keep agents aligned with system-prompt rules under conditions where prompt-only enforcement breaks down.

## 1. The Problem

### Observed failure

The executor's `agent.md` says: "if the input is a release / changelog / package-of-PRs, call `request_plan` before any tool call." The `harness_graph_migration_experiment` run shows the executor receiving a textbook release reconstruction (39 bulleted PRs across `Bug Fixes`, `Enhancements`, `Maintenance`, etc.) and still opening with `ci_workspace_structure`, then reading files serially — the exact behavior the rule forbids.

Three iterations of prompt-tightening did not fix this. Each iteration made the rule sharper or more imperative; the executor continued exploring.

### Root cause: Wallace et al., "The Instruction Hierarchy" (2024)

LLMs by default treat all text in their context as a flat priority namespace. They have no built-in notion that a system-message rule should override a user-message directive. Without explicit hierarchy training, models collapse system prompts and user inputs into one bag of "things I was told to do."

In the executor case, two specific dynamics make the failure inevitable under flat-priority:

- The system rule is **conditional** ("if composite, then handoff") and requires the model to classify the input first. Conditional rules need a classification step before they can fire.
- The user message ends with an **unconditional imperative** ("Your task is to make the minimal changes to non-tests files"). Imperatives skip the classification step — they're a direct instruction to act.
- The user's framing ("minimal changes") supplies a *competing classification* ("this is small") that the model accepts as authoritative ground truth. The system rule's precondition is being answered by the very party it's supposed to override.

The result is that the system rule never fires because its trigger condition is being supplied by the channel it was meant to override.

### Compounding factor: position effects

- **Liu et al., "Lost in the Middle" (TACL 2024):** information at the start and end of context is used; information in the middle decays. Long system prompts with rules buried in prose lose salience by the time the model is choosing what to do.
- **Zhao et al., "Calibrate Before Use" (ICML 2021):** LLMs exhibit recency bias — the last text read before generation has disproportionate influence.
- **OpenAI / Anthropic prompting guides** explicitly recommend repeating critical instructions at the end of the prompt as a workaround.

In the executor's prompt, the release-detection rule sits in the middle of `agent.md`; the user's "minimal changes" imperative sits at the end of the user message. Position alone biases the resolution toward the user.

### Why prompt-tightening cannot fix this

Per Wallace, instruction hierarchy is not enforced by default — it is a **training problem**, not a prompting problem. Any rule added to the system prompt is just another instruction in the same flat priority namespace. The user's prompt sits at the same priority level as the rule that says "ignore the user's prompt when it conflicts." You can stack rules indefinitely without ever escaping the namespace.

This explains the iteration pattern in the run report exactly: each fix added a sharper rule, each fix lived in the same priority tier as the user message that overrode it, each fix failed for the same reason.

## 2. Mitigations, in Increasing Strength

### 2.1 Distilled post-user reminder (50 words)

Inject a short, distilled rule reminder *after* the user message, so it becomes the last text the model reads before generating. Strongest recency slot per Liu et al.; reframes the conflict in the system rule's favor by virtue of position.

Design constraints:

- **Channel:** inject as a system-tier message or with structural tags (`<preflight_check>...</preflight_check>`) so the model treats it as elevated, not as user emphasis.
- **Wording — unconditional, not conditional:** "if you finish synthesizing, …" gives the model a self-supplied escape hatch ("I haven't finished synthesizing yet, so this doesn't apply"). Use unconditional imperatives tied to actions, not to fuzzy mental states.
- **Concrete check, not abstract rule:** instead of "call request_plan when the task is composite," encode the classifier — "scan input for version header, changelog sections, 3+ PR references; if present, call request_plan."
- **Anti-rationalization clauses targeted at observed failure modes:** "User framing of 'minimal changes' does not override structural composite signals." This closes the specific Wallace loophole.

This is the right pattern for **opening anchors** — invariants that must hold from turn 0.

### 2.2 Just-in-time skill loading

Move late-relevance content out of the system prompt into a separate skill file loaded mid-flight via `read_skill(name)`. For the planner, this means extracting topology diagrams, worked examples, and "how-to-read" guidance (~180 lines of `agent.md`) into `harness_agents/planner/skills/plan_topology.md`. The system prompt keeps the palette of labels and the operating loop; the skill carries the reference material.

Why this helps:

- **Lost-in-the-Middle mitigation:** the topology content enters the attention window *at the moment it is load-bearing* (when shape is being chosen), not at turn 0 where it decays before use.
- **Salience uplift for what stays:** halving the system prompt means the rules that remain (operating loop, atomicity, format contracts) compete with less prose.
- **First-class artifact:** the skill becomes reusable — the executor's atomic-execution skill, the evaluator's verification checklist, etc. follow the same pattern.

Risk to mitigate: do not extract the *closed palette* of topology labels — the planner needs to know *which labels exist* to begin reasoning at all. Extract only the diagrams, examples, and detailed guidance.

### 2.3 Runtime guardrail on terminal tools

Block `submit_plan_handoff` at the tool-dispatch layer if `read_skill('plan_topology')` has not been called. Implementation: in `engine/core/tool_dispatch.py`, before executing the terminal, check `context.skills_read`; if the required skill is missing, return a `ToolResultBlock(is_error=True, content=...)` and continue the loop. The model sees the rejection and is forced to load the skill before retrying.

This is the **load-bearing** piece of the design. It is the only mitigation that is **runtime-enforced** rather than model-enforced — it physically cannot be talked out of by user framing. Per Wallace's prescription for high-stakes invariants: when prompt-only enforcement is unreliable, move the check **out of the prompt entirely** and into a runtime gate where the model cannot be talked out of it.

The guardrail is the floor; everything else in this document is the polish that reduces how often the floor has to engage.

### 2.4 Dynamic-trigger notification rule

A `NotificationRule` whose `trigger` and `body` inspect the full transcript via `(messages, context)`. Layered, with each rung covering a different failure mode:

```python
def _trigger(messages, context):
    # Suppression: if loaded, stop. Always.
    if _has_loaded_skill(messages, 'plan_topology'):
        return False

    # Layer 1: precision — fire right after synthesis finishes.
    if _wait_background_tasks_returned(messages):
        return True

    # Layer 2: imminence — model is reasoning about shape.
    if _recent_text_mentions_topology(messages):
        return True

    # Layer 3: liveness fallback — cadence catches edge cases.
    if context.tool_calls_used > 0 and context.tool_calls_used % 10 == 0:
        return True

    # Layer 4: escalation — guardrail already rejected us.
    if _saw_guardrail_rejection(messages):
        return True

    return False
```

The `body` escalates wording with situation severity — first firing is informational, post-rejection firing is corrective. This produces a self-correcting feedback loop without additional runtime logic.

The notification's job is **not** to enforce skill-loading; the guardrail does that. The notification's job is to drive the model to compliance *before* the guardrail has to reject — saving one tool-call round-trip per planner run. Reframing the notification as a UX optimization for the guardrail (rather than a safety mechanism) lowers the bar it has to clear and clarifies the architecture.

## 3. The Architectural Principle

Soft and hard layers do different jobs and should not be measured against the same bar.

| Layer | Mechanism | Strength | Job |
|-------|-----------|----------|-----|
| **Soft** | `NotificationRule` → `<system-reminder>` block | Advisory; model can ignore | Shape probability, optimize path to compliance |
| **Hard** | Hook guardrail in tool dispatch → `ToolResultBlock(is_error=True)` | Mandatory; model cannot bypass | Provide correctness floor |

Layered correctly: the guardrail is the only piece you would bet correctness on; the notification reduces the cost of getting there. Hard for invariants, soft for guidance.

## 4. Beyond Terminal-Tool Gating: The Design Space

The skill-read gate is one application. The same primitives — *transcript-aware soft nudges* and *runtime-enforced hard rejections* — generalize into a behavior-shaping framework. The categories below organize the design surface by where in the agent's reasoning the intervention lands.

### 4.1 Anti-rationalization gates at decision boundaries

Catch rationalization *as it happens in transcript text*, before it crystallizes into action.

- **Soft (notification):** detect drift vocabulary in recent assistant text — "while I'm here," "let me also fix," "just one more file," "this is technically a small change to" — and inject a counter-prompt: *"You used scope-creep language. Restate the single change surface in one noun phrase before the next mutation. If you can't, stop and call request_plan."*
- **Hard (guardrail):** intercept `edit_file` when the assistant message preceding it mentions multiple files or contains list vocabulary; reject with *"This pattern indicates non-atomic work. Either restate the single surface or call request_plan."*

Same idea for the executor's "minimal changes" trap — detect the user's framing language being echoed back by the model as self-justification, and counter-prompt before the first tool call.

### 4.2 Forbidden-action gates with explanatory injection

- **Hard:** intercept any `edit_file` whose path matches `**/test_*.py` or `tests/**` and reject — but in the rejection text, inject the *reason*: *"Tests are acceptance criteria, not implementation surface. Edits to tests cannot satisfy the task. If tests are wrong, call submit_task_failure with REASON='test_invariant'."* The model sees not just "no" but the framework for why no.
- **Soft companion:** if the model's text mentions tests in an editing context ("I'll update the test to expect the new behavior"), pre-fire the reminder *before* it issues the tool call. Same end state, no rejection round-trip.

### 4.3 State-derived behavioral guidance

The trigger has access to `QueryContext` — tool budget, calls used, run metadata. Use it to shape behavior at state thresholds, not just transcript content.

- **Tool budget at 50%:** *"Half budget remaining. Prefer verification over exploration. If you don't have a clear next mutation in mind, call request_plan or submit_task_failure."*
- **Tool budget at 90%:** *"You're likely to hit the budget cap before completion. Package STATE_AT_HANDOFF and call request_plan now."*
- **Repeated failures (3+ tool errors in a row):** *"You've hit three consecutive errors. Consider that your hypothesis may be wrong. Stop, restate the hypothesis, and decide whether to continue or hand off."*
- **Tool-call streaks without verification:** *"You've made N mutations without running ci_diagnostics. Verify before continuing."*

These rules are context-dependent and do not fit cleanly into prompt rules; transcript inspection makes them trivial.

### 4.4 Capability nudges (better-tool routing)

Detect patterns where the agent is using a weaker tool when a stronger one applies.

- **Used `grep` with a Python symbol pattern:** *"For symbol queries, ci_query_symbol is more accurate than grep. Re-run as ci_query_symbol if your target is a function/class/method."*
- **Read 5+ files sequentially without a glob first:** *"Sequential read_file without glob is a scout pattern, not an executor pattern. If you're mapping an area, dispatch an explorer subagent or call request_plan."*
- **Foreground shell on a long-running command:** *"shell calls expected to take >10s should run as background tasks. Re-run with run_in_background=True."*

### 4.5 Cross-turn invariant maintenance

Long traces lose context. Use periodic re-injection to refresh load-bearing constraints.

- **Every N turns, re-inject the original goal in compact form** — counters root-goal drift on long planner traces. *"REMINDER: original goal is `<root_goal>`. If your current path doesn't trace back to this, you've drifted."*
- **After every replan / recovery slice:** *"REPLAN_AFTER triggered. The prefix is locked-in evidence; do not redo verified work. Size only the tail."*
- **After scout return:** *"Scouts have returned. Synthesize their findings into the plan; do not spawn executors whose deliverable is 'understand X'."*

### 4.6 Self-correcting loops via escalating wording

Because `body` sees history, it can detect "this is the Nth time this rule fired" and escalate.

- First firing: informational — *"Reminder: ..."*
- Second firing: assertive — *"You ignored the prior reminder. ..."*
- After guardrail rejection: corrective — *"submit was rejected because ... Call X now."*

Self-correction without runtime logic — the rule shapes its own escalation.

### 4.7 Anti-stall / liveness nudges

- **Same tool called 3x in a row with similar args:** *"You're in a loop. Either change strategy or call submit_task_failure."*
- **Long thinking blocks without tool calls:** *"You've reasoned for several turns without action. Commit to a tool call or hand off."*
- **Background tasks pending at would-be terminal:** *hard reject* with *"Pending background tasks: [...]. Call wait_background_tasks before terminal."*

## 5. Decision Framework: When to Use Which Mechanism

When designing a new rule, ask:

| Question | Answer |
|----------|--------|
| Is this a correctness invariant the agent must obey? | **Hard guardrail** in tool dispatch. Soft is insufficient. |
| Is this a quality nudge that improves outcomes but isn't load-bearing? | **Soft notification.** Hard is too punishing. |
| Is this state-dependent (tool budget, error streak, transcript content)? | Must be **transcript-aware** via the rule's `trigger`. Cannot live in the system prompt. |
| Is this content needed only at a specific decision moment? | **Skill** + mid-flight load. |
| Is this an opening anchor that must hold from turn 0? | **Post-user reminder** injected before generation. |

Most rules combine primitives. The skill-read gate is *just-in-time content* + *correctness invariant* + *self-correcting via escalation*. The forbidden-test-file gate is *correctness invariant* + *explanatory injection*. The budget-warning rule is *state-dependent* + *soft nudge*. Mix the primitives by need.

## 6. The Framework, Abstracted

What the harness has (or is building) is a **behavioral gradient system**:

- Soft signals shape decision probability via the transcript.
- Hard signals enforce constraints via the dispatch layer.
- Full-history inspection lets each rule be precise about *when* and *how loud* to fire.
- The system prompt sets the priors; the runtime layer handles the exceptions, the drift, and the cases where prompt-only enforcement breaks down.

The bigger insight: **behaviors that fail in production through rationalization or drift are usually fixable by a soft+hard pair, not by rewriting the system prompt.** The system prompt teaches the model what *should* happen; the runtime layer enforces it when the model's own incentives diverge. Treat them as complementary, not competing.

## 7. Current Plumbing State

- ✅ `SystemNotificationService.notify_system` works — injects `<system-reminder>` blocks post-tool-result, flushed via `flush_system_notifications` in `engine/core/query.py`.
- ✅ `NotificationRule` + `dispatch_rules` exist in `notification/rules.py` with `make_opening_reminder` and `make_budget_warning` factories.
- ❌ `dispatch_rules` is not yet called from the query loop. Wiring needed in `_run_query_loop`.
- ❌ `AgentDefinition.notification_rules` is referenced in docstrings but does not exist on the model.
- ❌ `QueryContext.notification_state` / `notification_fired` are referenced by `budget_warning.py` but do not exist on `QueryContext`.
- ❌ No skill loader yet. `AgentDefinition.skills` is a list of strings; nothing reads it. No `read_skill` tool.
- ❌ No tool-dispatch guardrail layer for skill-read or other invariants.

Implementing the skill-read gate for the planner requires wiring all of the above. See the planner-skill task for the implementation slice.

## References

- Wallace, Xiao, Leike, Weng, Heidecke, Beutel — *The Instruction Hierarchy: Training LLMs to Prioritize Privileged Instructions*, OpenAI, 2024.
- Liu, Lin, Hewitt, Paranjape, Bevilacqua, Petroni, Liang — *Lost in the Middle: How Language Models Use Long Contexts*, TACL 2024.
- Zhao, Wallace, Feng, Klein, Singh — *Calibrate Before Use: Improving Few-Shot Performance of Language Models*, ICML 2021.
- Mu, Zhang, Markov et al. — *Can LLMs Follow Simple Rules?* (RuLES), 2023.
- Sclar, Choi, Tsvetkov, Suhr — *Quantifying Language Models' Sensitivity to Spurious Features in Prompt Design*, ICLR 2024.
