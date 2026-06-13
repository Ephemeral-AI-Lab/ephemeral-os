import { z } from "zod";

/**
 * The host-owned message schema used to validate context-script output.
 * The SDK exports message *types* but no runtime schema, so pursuit owns a
 * narrow text-block schema here. A text content block is brand-free, so an
 * `InitialUserMessage` validated by this schema stays assignable to the
 * SDK's `UserMessage` when handed to `Agent.start`.
 */
export const ContentBlockSchema = z.object({
  type: z.literal("text"),
  text: z.string(),
});
export type ContentBlock = z.infer<typeof ContentBlockSchema>;

export const MessageSchema = z.object({
  role: z.enum(["user", "assistant"]),
  content: z.array(ContentBlockSchema).default([]),
});
export type Message = z.infer<typeof MessageSchema>;
