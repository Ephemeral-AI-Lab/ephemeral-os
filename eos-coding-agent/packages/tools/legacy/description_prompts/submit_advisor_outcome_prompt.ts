export const DESCRIPTION = `Submit the final outcome of this advisor run. Terminal: a successful call ends the run.

## How This Tool Works
- A successful call ends the run; a failed call does not - fix the problem and submit again.
- Must be called alone: batching it with any other tool call rejects the whole batch undispatched.
- Your submission is returned verbatim to the run that asked for the review, as its \`ask_advisor\` result.

## Writing the Verdict
- Lead \`summary\` with a clear verdict - approve, or what must change and why - followed by the reasoning that supports it.
- Be specific enough that the asker can act without guessing: name the exact field, claim, or gap at issue.
- \`payload\` optionally carries structured findings and rides the run outcome as \`submission\`.
`;
