export const DESCRIPTION = `Submit the final outcome of this subagent run. Terminal: a successful call ends the run.

## How This Tool Works
- A successful call ends the run; a failed call does not - fix the problem and submit again.
- Must be called alone: batching it with any other tool call rejects the whole batch undispatched.
- \`summary\` becomes the \`session_settled\` notification your parent reads; \`payload\` optionally carries the structured result and rides the run outcome as \`submission\`.

## Writing the Summary
- Make it a self-contained paragraph: the parent may act on it without ever reading your transcript.
- State concrete results - what was found, changed, or produced - not just "done".
- Report honestly: if something was skipped, failed, or only partially done, say so plainly.
`;
