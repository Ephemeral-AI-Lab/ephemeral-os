import type {
  AttemptFailureReason,
  AttemptId,
  PursuitEntityRunStatus,
} from "../contracts/pursuit.js";

import type { PlanState } from "../plan/state.js";
import type { WorkItemState } from "../work-item/state.js";

export interface AttemptState {
  readonly id: AttemptId;
  readonly sequence: number;
  readonly status: PursuitEntityRunStatus;
  readonly failureReasons: readonly AttemptFailureReason[];
  readonly legGoalVersion: number;
  readonly isConsistentWithLegGoal: boolean;
  readonly plan: PlanState;
  readonly workItems: readonly WorkItemState[];
}
