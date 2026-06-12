import { describe, expect, it } from "vitest";

import { Conversation, type ToolResultBlock } from "../src/conversation.js";
import {
  assistantMessage,
  must,
  textBlock,
  toolResultBlock,
  toolUseBlock,
  userText,
} from "./support.js";

function freshConversation(): Conversation {
  return new Conversation([userText("hi")]);
}

describe("Conversation divergence policy", () => {
  it("seeds initial messages into both lists", () => {
    const initial = [userText("hi"), assistantMessage(textBlock("hello"))];
    const conversation = new Conversation(initial);
    expect(conversation.llmMessages()).toEqual(initial);
    expect(conversation.displayedMessages().map((entry) => entry.message)).toEqual(
      initial,
    );
  });

  it("writes steered user and completed assistant messages to both lists", () => {
    const conversation = freshConversation();
    conversation.appendAssistant(assistantMessage(textBlock("sure")));
    conversation.appendUser(userText("steered"));
    const expected = [
      userText("hi"),
      assistantMessage(textBlock("sure")),
      userText("steered"),
    ];
    expect(conversation.llmMessages()).toEqual(expected);
    expect(conversation.displayedMessages().map((entry) => entry.message)).toEqual(
      expected,
    );
    expect(
      conversation.displayedMessages().every((entry) => entry.partial === undefined),
      "no displayed entry is flagged partial",
    ).toBe(true);
  });

  it("wraps one batch's tool results into a single user message in both lists", () => {
    const conversation = freshConversation();
    conversation.appendAssistant(
      assistantMessage(toolUseBlock("tu_1", "a"), toolUseBlock("tu_2", "b")),
    );
    const blocks: ToolResultBlock[] = [
      toolResultBlock("tu_1", "one"),
      toolResultBlock("tu_2", "two", true),
    ];
    conversation.appendToolResults(blocks);
    const wrapped = { role: "user", content: blocks };
    expect(must(conversation.llmMessages().at(-1))).toEqual(wrapped);
    expect(must(conversation.displayedMessages().at(-1)).message).toEqual(wrapped);
  });

  it("keeps partial assistant output displayed-only, flagged with its reason", () => {
    const conversation = freshConversation();
    conversation.appendPartialAssistant(
      assistantMessage(textBlock("half a thou")),
      "interrupted",
    );
    conversation.appendPartialAssistant(
      assistantMessage(textBlock("other half")),
      "provider_error",
    );
    expect(conversation.llmMessages()).toEqual([userText("hi")]);
    const displayed = conversation.displayedMessages();
    expect(displayed).toHaveLength(3);
    expect(must(displayed.at(1)).partial).toBe("interrupted");
    expect(must(displayed.at(2)).partial).toBe("provider_error");
  });

  it("stamps displayed entries with monotonic seq and ISO created_at", () => {
    const conversation = freshConversation();
    conversation.appendAssistant(assistantMessage(textBlock("a")));
    conversation.appendPartialAssistant(
      assistantMessage(textBlock("b")),
      "interrupted",
    );
    const displayed = conversation.displayedMessages();
    expect(displayed.map((entry) => entry.seq)).toEqual([0, 1, 2]);
    for (const entry of displayed) {
      expect(
        Number.isNaN(Date.parse(entry.created_at)),
        `entry ${String(entry.seq)} created_at parses as a date`,
      ).toBe(false);
      expect(
        entry.created_at,
        `entry ${String(entry.seq)} created_at is canonical ISO`,
      ).toBe(new Date(entry.created_at).toISOString());
    }
  });
});
