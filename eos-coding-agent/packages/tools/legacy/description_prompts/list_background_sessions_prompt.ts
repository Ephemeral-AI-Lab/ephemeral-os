export const DESCRIPTION = `List background sessions: running ones plus settled ones whose completion notice has not been delivered yet.

Usage notes:
- Takes no input.
- Each row carries \`type\`, \`id\`, \`status\`, and \`started_at\`, plus a \`summary\` and \`description\` when available.
- Use the listed \`(type, id)\` pair exactly as shown when targeting \`cancel_background_session\`.
- A settled session stays listed only until its completion notification is delivered, then drops out.
`;
