"""System notification metadata conversion helpers."""

from __future__ import annotations

from message.messages import SystemNotificationBlock


SYSTEM_NOTIFICATIONS_METADATA_KEY = "system_notifications"


def serialize_system_notifications(
    notifications: list[SystemNotificationBlock],
) -> list[dict[str, str]]:
    return [block.model_dump(mode="json") for block in notifications]


def system_notifications_from_metadata(
    metadata: dict[str, object],
) -> list[SystemNotificationBlock]:
    raw = metadata.get(SYSTEM_NOTIFICATIONS_METADATA_KEY)
    if not isinstance(raw, list):
        return []
    notifications: list[SystemNotificationBlock] = []
    for item in raw:
        if isinstance(item, SystemNotificationBlock):
            notifications.append(item)
            continue
        if isinstance(item, dict):
            try:
                notifications.append(SystemNotificationBlock.model_validate(item))
            except Exception:
                continue
    return notifications
