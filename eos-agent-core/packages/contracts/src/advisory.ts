import { z } from "zod";

import { JsonObjectSchema } from "./json.js";

export const AdvisoryVerdictSchema = z.object({
  verdict: z.enum(["pass", "fail"]),
  tool_name: z.string().min(1),
  payload: JsonObjectSchema,
  reason: z.string().min(1),
});
export type AdvisoryVerdict = z.infer<typeof AdvisoryVerdictSchema>;
