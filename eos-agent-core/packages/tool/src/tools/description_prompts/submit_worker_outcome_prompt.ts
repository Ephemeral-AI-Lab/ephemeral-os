export const DESCRIPTION = `Submit the final outcome of this worker run for the assigned work item. Terminal: a successful call ends the run.

## How This Tool Works
- A successful call ends the run; a failed call does not - fix the reported problem and submit again.
- Must be called alone: batching it with any other tool call rejects the whole batch undispatched.
- \`is_pass\` decides the work item's outcome: \`true\` marks it Success, \`false\` marks it Failed and fails the attempt.
- \`outcome\` carries the full structured result; \`summary\` is the one-paragraph version the planner reads first.

## Before Using This Tool
- Only submit once the assigned work is complete and verified; this is the last action of the run.
- Advisory-gated: review with \`ask_advisor\` first, passing \`tool_name\` "submit_worker_outcome" and the exact payload you intend to submit, and address its feedback. An unreviewed submission can be denied.
- A successful submission disposes this run's still-running background sessions.

## Writing the Summary
- Report what was completed and how it was verified, plus any remaining risk, honestly.
- Never claim more than was done: a faithful partial result beats an inflated complete one. An honest \`is_pass: false\` with a precise failure account is what lets the retry planner succeed.
`;
