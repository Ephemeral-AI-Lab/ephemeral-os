export const DESCRIPTION = `Delegate a goal to an autonomous planner/worker workflow running as one background session of this run.

## How This Tool Works
- Returns the \`workflow_id\` immediately; this call never waits for the workflow to finish.
- The workflow plans the goal in iterations: a planner declares each iteration's focus and work items, workers execute them, and failed attempts are re-planned automatically within the attempt budget (\`max_attempts\` per iteration, default 2).
- When the workflow settles, its outcome arrives as a \`session_settled\` notification carrying a one-line summary. The notification arrives in a later turn - never write it yourself.
- At most ONE delegated workflow can be open per run: a second call while a workflow session is open (running or undelivered) returns an error.

## Writing the Goal
The planner starts with zero context from this conversation. State the complete goal like a brief to a smart colleague:
- What done looks like, the concrete constraints, and the facts the planner cannot discover on its own.
- The workflow may split the goal across iterations itself; you do not pre-slice it.

## After Delegating
- Track it with \`list_background_sessions\`; stop it with \`cancel_background_session\` (type \`workflow\`) - cancelling the session cancels the whole workflow.
- A workflow still running when this run finishes is cancelled by the disposal cascade. If you need its result, wait for the \`session_settled\` notification before your terminal submission.
`;
