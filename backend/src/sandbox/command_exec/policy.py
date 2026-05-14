"""Policy values for guarded command execution."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CommandExecPolicy:
    """Tenant/test-injectable command execution policy."""

    restricted_env_keys: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "LD_PRELOAD",
                "LD_LIBRARY_PATH",
                "LD_AUDIT",
                "DYLD_INSERT_LIBRARIES",
                "DYLD_LIBRARY_PATH",
                "PATH",
                "PYTHONPATH",
                "BASH_ENV",
                "ENV",
            }
        )
    )
    workspace_env_keys: frozenset[str] = field(
        default_factory=lambda: frozenset({"WORKSPACE_DIR", "PWD", "OLDPWD"})
    )
    forbidden_overlay_path_chars: tuple[str, ...] = (
        ",",
        ":",
        "\\",
        "\n",
        "\r",
        "\t",
        "\0",
    )
    command_env_defaults: Mapping[str, str] = field(
        default_factory=lambda: {"GIT_OPTIONAL_LOCKS": "0"}
    )

    def command_environment(self, extra: Mapping[str, str]) -> dict[str, str]:
        safe_extra = {
            k: v for k, v in extra.items() if k not in self.restricted_env_keys
        }
        return {
            **os.environ,
            **safe_extra,
            **{str(k): str(v) for k, v in self.command_env_defaults.items()},
        }

    def validate_overlay_path_text(self, text: str) -> None:
        for bad in self.forbidden_overlay_path_chars:
            if bad in text:
                label = repr(bad)
                raise ValueError(f"overlay mount path cannot contain {label}: {text!r}")

    def to_payload(self) -> dict[str, object]:
        return {
            "restricted_env_keys": sorted(self.restricted_env_keys),
            "workspace_env_keys": sorted(self.workspace_env_keys),
            "forbidden_overlay_path_chars": list(self.forbidden_overlay_path_chars),
            "command_env_defaults": dict(self.command_env_defaults),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> CommandExecPolicy:
        def _strings(key: str, default: tuple[str, ...]) -> frozenset[str]:
            raw = payload.get(key)
            if not isinstance(raw, list):
                return frozenset(default)
            return frozenset(str(item) for item in raw)

        defaults = DEFAULT_COMMAND_EXEC_POLICY
        env_defaults_raw = payload.get("command_env_defaults")
        env_defaults = (
            {str(k): str(v) for k, v in env_defaults_raw.items()}
            if isinstance(env_defaults_raw, Mapping)
            else dict(defaults.command_env_defaults)
        )
        forbidden_raw = payload.get("forbidden_overlay_path_chars")
        forbidden = (
            tuple(str(item) for item in forbidden_raw)
            if isinstance(forbidden_raw, list)
            else defaults.forbidden_overlay_path_chars
        )
        return cls(
            restricted_env_keys=_strings(
                "restricted_env_keys",
                tuple(defaults.restricted_env_keys),
            ),
            workspace_env_keys=_strings(
                "workspace_env_keys",
                tuple(defaults.workspace_env_keys),
            ),
            forbidden_overlay_path_chars=forbidden,
            command_env_defaults=env_defaults,
        )


DEFAULT_COMMAND_EXEC_POLICY = CommandExecPolicy()

__all__ = [
    "CommandExecPolicy",
    "DEFAULT_COMMAND_EXEC_POLICY",
]
