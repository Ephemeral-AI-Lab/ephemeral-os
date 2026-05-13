"""Tool execution context service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from inspect import isawaitable
from pathlib import Path
from typing import Any

from tools._framework.core.runtime import ExecutionMetadata


@dataclass(init=False)
class ToolExecutionContextService:
    """Service and runtime state store injected into a tool invocation.

    Well-known runtime services and identifiers are exposed directly on this
    object through attribute delegation. Tool-specific extras are available
    through mapping-style access.
    """

    cwd: Path
    _metadata: ExecutionMetadata = field(default_factory=ExecutionMetadata, repr=False)

    def __init__(
        self,
        cwd: Path | str,
        services: ExecutionMetadata | Mapping[str, Any] | None = None,
        **service_overrides: Any,
    ) -> None:
        object.__setattr__(self, "cwd", Path(cwd))
        object.__setattr__(self, "_metadata", self._coerce_services(services))
        if service_overrides:
            self._metadata.update(service_overrides)

    @staticmethod
    def _coerce_services(
        services: ExecutionMetadata | Mapping[str, Any] | None,
    ) -> ExecutionMetadata:
        if services is None:
            return ExecutionMetadata()
        if isinstance(services, ExecutionMetadata):
            return services
        meta = ExecutionMetadata()
        for key, value in services.items():
            meta[key] = value
        return meta

    def __getattr__(self, name: str) -> Any:
        if name in ExecutionMetadata._TYPED_FIELDS:
            return getattr(self._metadata, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"cwd", "_metadata"}:
            object.__setattr__(self, name, value)
            return
        if name in ExecutionMetadata._TYPED_FIELDS:
            setattr(self._metadata, name, value)
            return
        object.__setattr__(self, name, value)

    def services_copy(self) -> ExecutionMetadata:
        return self._metadata.copy()

    def services_with_overrides(self, **overrides: Any) -> ExecutionMetadata:
        return self._metadata.with_overrides(**overrides)

    def update_services(
        self,
        other: Mapping[str, Any] | ExecutionMetadata | None = None,
        /,
        **kwargs: Any,
    ) -> None:
        self._metadata.update(other, **kwargs)

    def get(self, key: str, default: Any = None) -> Any:
        return self._metadata.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._metadata[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._metadata[key] = value

    def __contains__(self, key: object) -> bool:
        return key in self._metadata

    async def notify_system(self, text: str) -> None:
        """Emit a system notification through the injected notification service."""

        service = self.get("system_notification_service")
        if service is None:
            return
        notify = getattr(service, "notify_system", None)
        if notify is None:
            notify = getattr(service, "notify", None)
        if notify is None:
            return
        result = notify(text)
        if isawaitable(result):
            await result
