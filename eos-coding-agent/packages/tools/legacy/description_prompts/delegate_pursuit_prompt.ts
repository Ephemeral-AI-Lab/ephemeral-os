export const DESCRIPTION = `Delegate a goal to an autonomous planner/worker pursuit running as one background session of this run.

## How This Tool Works
- Returns the \`pursuit_id\` immediately; this call never waits for the pursuit to finish.
- The pursuit plans the goal in legs: a planner accepts or refocuses each leg goal, declares work items, workers execute them, and failed attempts are re-planned automatically within the attempt budget (\`max_attempts\` per leg, default 2).
- When the pursuit settles, its outcome arrives as a \`session_settled\` notification carrying a one-line summary. The notification arrives in a later turn - never write it yourself.
- At most ONE delegated pursuit can be open per run: a second call while a pursuit session is open (running or undelivered) returns an error.

## Writing the Goal
Use dynamic leg goals by default: provide only \`pursuit_goal\` when the planner should discover or refocus legs during execution.

Use predefined leg goals only when you already know the complete ordered leg list: provide \`pursuit_goal\` and \`leg_goals\`. In this mode planners cannot submit \`leg_goal\` or \`next_leg_goal\`.

The planner starts with zero context from this conversation. State the complete pursuit goal like a brief to a smart colleague:
- What done looks like, the concrete constraints, and the facts the planner cannot discover on its own.
- In dynamic mode, the pursuit may split the goal across legs itself; you do not pre-slice it.

## After Delegating
- Track it with \`list_background_sessions\`; stop it with \`cancel_background_session\` (type \`pursuit\`) - cancelling the session cancels the whole pursuit.
- A pursuit still running when this run finishes is cancelled by the disposal cascade. If you need its result, wait for the \`session_settled\` notification before your terminal submission.
`;
