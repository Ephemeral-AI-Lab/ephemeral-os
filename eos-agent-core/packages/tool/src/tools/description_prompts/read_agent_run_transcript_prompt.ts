export const DESCRIPTION = `Read a started agent run's JSONL transcript by byte offset.

Usage notes:
- Returns \`transcript\` (the chunk read), \`next_offset\` (where to resume), and \`eof\` (whether the end was reached).
- Defaults to \`offset\` 0 and \`max_bytes\` 65536 (capped at 262144); for a long transcript, keep calling with the returned \`next_offset\` until \`eof\` is true.
- To poll a running subagent for progress, pass the last \`next_offset\` you saw and read only what is new.
- Only runs started by this runtime can be read; an unknown \`run_id\` is an error.
`;
