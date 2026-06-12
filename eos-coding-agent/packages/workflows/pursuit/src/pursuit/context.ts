import { isPursuitEntityTerminal } from "@eos/contracts";

import { composeLegOutcome } from "../leg/context.js";
import type { LegState } from "../leg/state.js";
import type { EntityFieldFile } from "../work-item/context.js";
import type { PursuitState } from "./state.js";

export function pursuitFieldFiles(
  pursuit: PursuitState,
  legs: readonly LegState[],
): EntityFieldFile[] {
  const files: EntityFieldFile[] = [
    { name: "goal.md", content: pursuit.pursuitGoal },
  ];
  if (isPursuitEntityTerminal(pursuit.status)) {
    files.push({
      name: "outcome.md",
      content: composePursuitOutcome(pursuit, legs),
    });
  }
  return files;
}

/**
 * §5.3: the pursuit outcome is the ordered ledger of every closed
 * leg's outcome. A cancelled pursuit renders a cancellation marker
 * (not a business outcome) ahead of any already closed legs.
 */
export function composePursuitOutcome(
  pursuit: PursuitState,
  legs: readonly LegState[],
): string {
  const head =
    pursuit.status === "Cancelled"
      ? "# Pursuit outcome\npursuit cancelled"
      : "# Pursuit outcome";
  const sections = legs
    .filter(
      (leg) =>
        leg.status === "Success" || leg.status === "Failed",
    )
    .map(
      (leg) =>
        `## leg_${leg.id} [${leg.status}]\n${composeLegOutcome(leg)}`,
    );
  return [head, ...sections].join("\n\n");
}
