import type {
  AgentRunId,
  LegGoalMode,
  PursuitEntityRunStatus,
  PursuitId,
} from "../../contracts/pursuit.js";

export interface PursuitState {
  readonly id: PursuitId;
  readonly parentRunId: AgentRunId | null;
  readonly pursuitGoal: string;
  readonly legGoalMode: LegGoalMode;
  readonly legGoals: readonly string[];
  readonly status: PursuitEntityRunStatus;
  readonly closedAt: string | null;
}
