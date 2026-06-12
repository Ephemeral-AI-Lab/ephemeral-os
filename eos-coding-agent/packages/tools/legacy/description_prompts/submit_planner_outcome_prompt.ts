export const DESCRIPTION = `Submit the final outcome of this planner run: optional leg-goal declarations and the work items for the current leg. Terminal: a successful call ends the run.

## How This Tool Works
- A successful call ends the run; a failed call does not - fix the reported problem and submit again. Shape, structure (unique ids, no in-payload cycles), and materialization errors all return as correctable results.
- Must be called alone: batching it with any other tool call rejects the whole batch undispatched.
- Dynamic pursuits may omit both \`leg_goal\` and \`next_leg_goal\` to accept the current leg goal.
- Dynamic pursuits may submit \`leg_goal\` to refocus this leg. If \`next_leg_goal\` is omitted in that same payload, any standing successor is cleared.
- Dynamic pursuits may submit successor-only \`next_leg_goal\` without \`leg_goal\`; it is promoted to the next leg when this one closes successfully.
- Predefined pursuits must omit both \`leg_goal\` and \`next_leg_goal\`; the caller-provided leg list owns the sequence.
- Each work item names a worker profile (\`agent_name\`), a one-line \`title\`, a full \`spec\`, and \`depends_on\` ids. Dependencies may target this payload's work items or visible prior work items in the same non-superseded leg-goal version.

## Before Using This Tool
- Only submit once the plan is coherent, complete, and safe to hand off; this is the last action of the run.
- Advisory-gated: review with \`ask_advisor\` first, passing \`tool_name\` "submit_planner_outcome" and the exact payload you intend to submit, and address its feedback. An unreviewed submission can be denied.
- A successful submission disposes this run's still-running background sessions.

## Writing the Summary
- Lead with what this attempt's plan achieves within the current leg goal and the approach it takes.
- Flag the open risks and assumptions whoever executes the plan must know about.
`;
