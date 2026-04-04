"""Swarm backend abstraction for teammate execution."""

from ephemeralos.swarm.mailbox import (
    MailboxMessage,
    TeammateMailbox,
    create_idle_notification,
    create_shutdown_request,
    create_user_message,
    get_agent_mailbox_dir,
    get_team_dir,
)
from ephemeralos.swarm.permission_sync import (
    SwarmPermissionRequest,
    SwarmPermissionResponse,
    create_permission_request,
    handle_permission_request,
    poll_permission_response,
    send_permission_request,
    send_permission_response,
)
from ephemeralos.swarm.registry import BackendRegistry, get_backend_registry
from ephemeralos.swarm.subprocess_backend import SubprocessBackend
from ephemeralos.swarm.types import (
    BackendType,
    SpawnResult,
    TeammateExecutor,
    TeammateIdentity,
    TeammateMessage,
    TeammateSpawnConfig,
)

__all__ = [
    "BackendRegistry",
    "BackendType",
    "MailboxMessage",
    "SpawnResult",
    "SubprocessBackend",
    "SwarmPermissionRequest",
    "SwarmPermissionResponse",
    "TeammateExecutor",
    "TeammateIdentity",
    "TeammateMailbox",
    "TeammateMessage",
    "TeammateSpawnConfig",
    "create_idle_notification",
    "create_permission_request",
    "create_shutdown_request",
    "create_user_message",
    "get_agent_mailbox_dir",
    "get_backend_registry",
    "get_team_dir",
    "handle_permission_request",
    "poll_permission_response",
    "send_permission_request",
    "send_permission_response",
]
