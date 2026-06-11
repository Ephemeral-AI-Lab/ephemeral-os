export const DESCRIPTION = `Submit the final outcome of this main run. Terminal: a successful call ends the run.

## How This Tool Works
- A successful call ends the run; a failed call does not - fix the problem and submit again.
- Must be called alone: batching it with any other tool call rejects the whole batch undispatched.
- \`summary\` is the one-paragraph result the caller reads first; \`payload\` optionally carries the structured result and rides the run outcome as \`submission\`.

## Before Using This Tool
- Only submit once the user's goal is complete and verified; this is the last action of the run.
- Advisory-gated: review with \`ask_advisor\` first, passing \`tool_name\` "submit_main_outcome" and the exact payload you intend to submit, and address its feedback. An unreviewed submission can be denied.
- A successful submission disposes this run's still-running background sessions. If you are waiting on a subagent's result, wait for its \`session_settled\` notification before submitting.

## Writing the Summary
- Lead with the outcome: what was accomplished and how it was verified.
- Report honestly: if something was skipped, failed, or only partially done, say so plainly.
`;
