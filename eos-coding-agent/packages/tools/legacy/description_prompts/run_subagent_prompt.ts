export const DESCRIPTION = `Start the named agent as a detached background run on the given prompt.

## How This Tool Works
- Returns the subagent's \`run_id\` immediately; this call never waits for the subagent to finish.
- The run is detached: it keeps running after your current turn ends, and nothing in this turn blocks on it.
- When the subagent settles, its outcome arrives as a \`session_settled\` notification carrying the summary it submitted. The notification arrives in a later turn - it is never something you write yourself.

## Writing the Prompt
The subagent starts with zero context from this conversation. Brief it like a smart colleague who just walked into the room:
- State the goal and why it matters, not just a bare instruction.
- Include the concrete facts it needs: paths, names, constraints, what has already been tried or ruled out.
- Say what is out of scope if another agent or your own work owns adjacent territory.
- Describe the result you expect back, including the shape and length of the summary.

## After Launching
- Track running subagents with \`list_background_sessions\`; stop one with \`cancel_background_session\` (type \`subagent\`).
- Use \`read_agent_run_transcript\` with the returned \`run_id\` only when you genuinely need progress detail before the notification lands.
- Don't race the result: after launching you know nothing about the subagent's findings. Never predict or fabricate its outcome; if asked before the notification arrives, report that it is still running.
- Subagents still running when this run finishes are cancelled by the disposal cascade. If you need a subagent's result, wait for its \`session_settled\` notification before making your terminal submission.
`;
