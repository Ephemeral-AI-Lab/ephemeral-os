export const DESCRIPTION = `Cancel a running background session by its \`type\` and \`id\`, as listed by \`list_background_sessions\`.

Usage notes:
- Use the \`(type, id)\` ref exactly as returned by \`list_background_sessions\`.
- \`reason\` is optional and is recorded with the cancellation.
- Cancelling an unknown session is an error; cancelling one that already settled is a no-op that reports its settled status.
- This is the only way to stop a detached run, such as a session started by \`run_subagent\`.
`;
