"""Execution strategy registry."""

from __future__ import annotations

from collections.abc import Sequence

from sandbox.command_exec.contract import MountMode
from sandbox.command_exec.policy import (
    DEFAULT_COMMAND_EXEC_POLICY,
    CommandExecPolicy,
)
from sandbox.command_exec.strategies.base import ExecutionStrategy
from sandbox.command_exec.strategies.copy_backed import CopyBackedStrategy
from sandbox.command_exec.strategies.private_namespace import (
    PrivateNamespaceStrategy,
    detect_private_mount_namespace,
)


class StrategyRegistry:
    """Ordered strategy set for command execution fallback."""

    def __init__(self, strategies: Sequence[ExecutionStrategy]) -> None:
        self._strategies = tuple(strategies)

    @classmethod
    def bootstrap(
        cls,
        *,
        private_namespace_available: bool | None = None,
        policy: CommandExecPolicy = DEFAULT_COMMAND_EXEC_POLICY,
    ) -> StrategyRegistry:
        available = (
            detect_private_mount_namespace()
            if private_namespace_available is None
            else private_namespace_available
        )
        return cls(
            (
                PrivateNamespaceStrategy(available=available, policy=policy),
                CopyBackedStrategy(policy=policy),
            )
        )

    @property
    def strategies(self) -> tuple[ExecutionStrategy, ...]:
        return self._strategies

    def is_available(self, mode: MountMode) -> bool:
        return any(
            strategy.name == mode.value and strategy.is_available()
            for strategy in self._strategies
        )


__all__ = ["StrategyRegistry"]
