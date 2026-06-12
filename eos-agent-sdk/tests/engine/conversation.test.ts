import { describe, expect, it } from "vitest";

import { toolUseIdFrom } from "../../src/contracts/index.js";

import {
  Conversation,
  type ConversationRecord,
} from "../../src/engine/conversation.js";

function fixture(): { conversation: Conversation; records: ConversationRecord[] } {
  const records: ConversationRecord[] = [];
  const conversation = new Conversation(
    [{ role: "user", content: [{ type: "text", text: "go" }] }],
    (entry) => {
      records.push(entry);
    },
  );
  return { conversation, records };
}

describe("Conversation", () => {
  it("seeds llm history from the initial messages and records them as initial", () => {
    const { conversation, records } = fixture();
    expect(conversation.llmMessages()).toHaveLength(1);
    expect(records).toEqual([
      { kind: "user", origin: "initial", message: conversation.llmMessages()[0] },
    ]);
  });

  it("appends every conversation message to both history and the sink", () => {
    const { conversation, records } = fixture();
    conversation.appendAssistant({ role: "assistant", content: [{ type: "text", text: "hi" }] });
    conversation.appendUser(
      { role: "user", content: [{ type: "text", text: "more" }] },
      "steer",
    );
    conversation.appendToolResults([
      { type: "tool_result", tool_use_id: toolUseIdFrom("t1"), content: "ok", is_error: false },
    ]);
    expect(conversation.llmMessages()).toHaveLength(4);
    expect(records.map((entry) => entry.kind)).toEqual([
      "user",
      "assistant",
      "user",
      "tool_results",
    ]);
    expect(records[2]).toMatchObject({ origin: "steer" });
  });

  it("wraps one batch's results as a single user message in order", () => {
    const { conversation } = fixture();
    conversation.appendToolResults([
      { type: "tool_result", tool_use_id: toolUseIdFrom("t1"), content: "a", is_error: false },
      { type: "tool_result", tool_use_id: toolUseIdFrom("t2"), content: "b", is_error: true },
    ]);
    const last = conversation.llmMessages().at(-1);
    expect(last?.role).toBe("user");
    expect(last?.content.map((block) => block.type)).toEqual([
      "tool_result",
      "tool_result",
    ]);
  });

  it("keeps salvaged partials out of provider history but in the records", () => {
    const { conversation, records } = fixture();
    conversation.appendPartialAssistant(
      { role: "assistant", content: [{ type: "text", text: "half" }] },
      "interrupted",
    );
    expect(conversation.llmMessages(), "partials never reach the provider").toHaveLength(1);
    expect(records.at(-1)).toMatchObject({
      kind: "assistant_partial",
      reason: "interrupted",
    });
  });
});
