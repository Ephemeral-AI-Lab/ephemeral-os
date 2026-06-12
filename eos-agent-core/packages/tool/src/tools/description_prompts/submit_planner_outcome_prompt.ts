export const DESCRIPTION = `Submit the final outcome of this planner run: the leg's focus declaration and the work items for it. Terminal: a successful call ends the run.

## How This Tool Works
- A successful call ends the run; a failed call does not - fix the reported problem and submit again. Shape, structure (unique ids, declared \`needs\`, no cycles), and materialization errors all return as correctable results.
- Must be called alone: batching it with any other tool call rejects the whole batch undispatched.
- \`leg_goal\` and \`next_leg_goal\` declare and reset as ONE atomic pair. The leg's first submission must declare \`leg_goal\`; later submissions may omit both to keep the standing declaration, or re-declare to refocus - which resets BOTH fields and supersedes the prior attempts.
- \`next_leg_goal\` is only valid beside \`leg_goal\`: it names the remainder of the current goal, promoted to the next leg when this one closes successfully.
- Each work item names a worker profile (\`agent_name\`), a one-line \`description\`, a full \`work_item_spec\`, and its \`needs\` (ids of work items in this same submission it depends on).

## Before Using This Tool
- Only submit once the plan is coherent, complete, and safe to hand off; this is the last action of the run.
- Advisory-gated: review with \`ask_advisor\` first, passing \`tool_name\` "submit_planner_outcome" and the exact payload you intend to submit, and address its feedback. An unreviewed submission can be denied.
- A successful submission disposes this run's still-running background sessions.

## Writing the Summary
- Lead with what this attempt's plan achieves within the declared focus and the approach it takes.
- Flag the open risks and assumptions whoever executes the plan must know about.
`;
