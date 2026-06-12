import type { LegId, PursuitEntityRunStatus } from "@eos/contracts";

import type { AttemptState } from "../attempt/state.js";

export interface LegState {
  readonly id: LegId;
  readonly sequence: number;
  readonly origin: "initial" | "next_leg_goal" | "predefined";
  readonly maxAttempts: number;
  readonly status: PursuitEntityRunStatus;
  readonly legGoal: string;
  readonly legGoalVersion: number;
  readonly legGoalProvenance: string;
  readonly isLegGoalMutatable: boolean;
  readonly nextLegGoal: string | null;
  readonly attempts: readonly AttemptState[];
}

export function closingAttempt(
  leg: Pick<LegState, "attempts">,
): AttemptState | undefined {
  return leg.attempts.at(-1);
}
