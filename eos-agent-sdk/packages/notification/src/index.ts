// The notification mechanism, layered below the engine: the inbox the loop
// drains and the host-facing Notifier capability. Notification *rules* are
// not an SDK concept — hosts compile theirs into turnBoundary hook entries.
export { NotificationInbox, systemNotificationMessage } from "./inbox.js";
export type { Notifier } from "./notifier.js";
