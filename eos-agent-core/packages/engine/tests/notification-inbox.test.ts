import { describe, expect, it } from "vitest";

import { fromUserText } from "@eos/contracts";

import {
  NotificationInbox,
  systemNotificationMessage,
} from "../src/notification-inbox.js";

const note = (text: string) => fromUserText(text);

describe("NotificationInbox", () => {
  it("drains pending messages in publish order, then is empty", () => {
    const inbox = new NotificationInbox();
    inbox.publish(note("a"));
    inbox.publish(note("b"));
    expect(inbox.drain()).toEqual([note("a"), note("b")]);
    expect(inbox.drain()).toEqual([]);
  });

  it("replaces a pending entry with the same key in place", () => {
    const inbox = new NotificationInbox();
    inbox.publish(note("first"), { key: "k1" });
    inbox.publish(note("other"), { key: "k2" });
    inbox.publish(note("second"), { key: "k1" });
    expect(inbox.drain()).toEqual([note("second"), note("other")]);
  });

  it("fires onDrained with the drained tags in the same synchronous block", () => {
    const inbox = new NotificationInbox();
    const seen: unknown[][] = [];
    inbox.onDrained((tags) => {
      seen.push(tags);
    });
    inbox.publish(note("tagged"), { tag: { id: 1 } });
    inbox.publish(note("untagged"));
    const drained = inbox.drain();
    expect(drained).toHaveLength(2);
    expect(seen, "callback ran synchronously during drain()").toEqual([[{ id: 1 }]]);
    inbox.drain();
    expect(seen, "an empty drain fires nothing").toHaveLength(1);
  });

  it("waitForNext resolves immediately when entries are already pending", async () => {
    const inbox = new NotificationInbox();
    inbox.publish(note("ready"));
    await inbox.waitForNext(new AbortController().signal);
  });

  it("waitForNext resolves on the next publish", async () => {
    const inbox = new NotificationInbox();
    const wait = inbox.waitForNext(new AbortController().signal);
    inbox.publish(note("arrives"));
    await wait;
  });

  it("waitForNext resolves on abort so a parked loop can classify cancellation", async () => {
    const inbox = new NotificationInbox();
    const controller = new AbortController();
    const wait = inbox.waitForNext(controller.signal);
    controller.abort();
    await wait;
  });

  it("renders payloads as a <system_notification> user message", () => {
    const message = systemNotificationMessage({ type: "hook_context", text: "hi" });
    expect(message).toEqual({
      role: "user",
      content: [
        {
          type: "text",
          text: '<system_notification>{"type":"hook_context","text":"hi"}</system_notification>',
        },
      ],
    });
  });
});
