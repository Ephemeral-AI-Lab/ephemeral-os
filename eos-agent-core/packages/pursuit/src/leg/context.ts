import { composeAttemptOutcome } from "../attempt/context.js";
import type { EntityFieldFile } from "../work-item/context.js";
import { closingAttempt, type LegState } from "./state.js";

/**
 * Leg field files: the latest declaration pair plus `outcome.md`
 * once the leg closes `Success` or `Failed`. Cancelled legs
 * carry no business outcome (§2.6).
 */
export function legFieldFiles(leg: LegState): EntityFieldFile[] {
  const files: EntityFieldFile[] = [
    {
      name: "leg_goal.md",
      content: `${leg.legGoal}\n\nProvenance: ${leg.legGoalProvenance}`,
    },
  ];
  if (leg.nextLegGoal !== null) {
    files.push({ name: "next_leg_goal.md", content: leg.nextLegGoal });
  }
  if (leg.status === "Success" || leg.status === "Failed") {
    files.push({ name: "outcome.md", content: composeLegOutcome(leg) });
  }
  return files;
}

/** §5.2: the leg outcome IS the closing attempt's derived outcome. */
export function composeLegOutcome(leg: LegState): string {
  const attempt = closingAttempt(leg);
  return attempt ? composeAttemptOutcome(attempt) : "(no attempts)";
}
