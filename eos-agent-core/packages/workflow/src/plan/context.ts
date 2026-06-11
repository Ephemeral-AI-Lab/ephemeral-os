import type { EntityFieldFile } from "../work-item/context.js";
import type { PlanState } from "./state.js";

export function planFieldFiles(plan: PlanState): EntityFieldFile[] {
  return plan.summary === null ? [] : [{ name: "summary.md", content: plan.summary }];
}
