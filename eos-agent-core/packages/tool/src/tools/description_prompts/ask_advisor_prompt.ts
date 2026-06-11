export const DESCRIPTION = `Ask the advisor to review the terminal submission you intend to make, before you make it.

## How This Tool Works
- Pass \`tool_name\` (the terminal tool you intend to call next) and \`payload\` (the EXACT input you intend to submit).
- The advisor receives this run's full transcript plus the target tool's advisory instructions, and reviews them against your intended payload.
- Blocks until the advisor finishes; the tool result is the advisor's own submission.
- The advisor run is scoped to this call and is cancelled if the call is interrupted.

## When to Use This Tool
- Before calling an advisory-gated terminal tool such as \`submit_main_outcome\`; an unreviewed submission can be denied.
- Only advisory-gated tools can be reviewed: asking about a tool that has no advisor prompt is an error.

## Before Using This Tool
- Finalize the payload first: a review of anything other than what you actually submit is worthless.
- If the advisor asks for changes, revise the payload, then re-review the revised payload before submitting.
`;
