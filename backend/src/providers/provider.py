"""API client factory."""

from __future__ import annotations

import importlib
import os
from typing import Any

from providers.types import SupportsStreamingMessages


CODING_PLAN_NAMESPACE = "providers.clients.coding_plan."


def make_api_client(
    external: SupportsStreamingMessages | None = None,
    *,
    db_kwargs: dict[str, Any] | None = None,
) -> SupportsStreamingMessages:
    """Build a streaming client from the active model registration.

    With *external*, return it unchanged. Otherwise inspect `db_kwargs`:
    `class_path` is the dispatch discriminator (plan §A5). Empty / missing
    `class_path` → today's `AnthropicClient(api_key=..., base_url=...)`.
    Non-empty `class_path` is parsed as `module.path:ClassName`; the class is
    instantiated as `cls(db_kwargs=db_kwargs)`.

    EOS_DISABLE_PLAN_MODE=1 (plan §A12) rejects any `class_path` resolving
    into `providers.clients.coding_plan.*`.
    """
    if external is not None:
        return external

    if db_kwargs is None:
        from config.model_config import get_active_model_kwargs

        db_kwargs = get_active_model_kwargs()

    class_path = (db_kwargs or {}).get("class_path", "") or ""

    if class_path:
        if (
            class_path.startswith(CODING_PLAN_NAMESPACE)
            and os.environ.get("EOS_DISABLE_PLAN_MODE") == "1"
        ):
            from config.model_config import NoActiveModelError

            raise NoActiveModelError(
                "Plan mode disabled by EOS_DISABLE_PLAN_MODE=1; "
                f"refusing to instantiate {class_path!r}"
            )

        cls = _resolve_class_path(class_path)
        return cls(db_kwargs=db_kwargs)

    # Default path: API-key Anthropic client.
    from providers.clients.anthropic_native import AnthropicClient

    api_key = db_kwargs.get("api_key")
    base_url = db_kwargs.get("base_url")
    if not api_key:
        from config.model_config import NoActiveModelError

        raise NoActiveModelError("Active model registration has no api_key")

    return AnthropicClient(api_key=api_key, base_url=base_url)


def _resolve_class_path(class_path: str) -> type[SupportsStreamingMessages]:
    """Parse `module.path:ClassName` and return the class object."""
    from config.model_config import NoActiveModelError

    module_str, sep, attr_str = class_path.partition(":")
    if not sep or not module_str or not attr_str:
        raise NoActiveModelError(
            f"unknown class_path {class_path!r}: expected 'module.path:ClassName'"
        )
    try:
        module = importlib.import_module(module_str)
    except ImportError as exc:
        raise NoActiveModelError(
            f"unknown class_path {class_path!r}: cannot import module: {exc}"
        ) from exc
    cls = getattr(module, attr_str, None)
    if cls is None:
        raise NoActiveModelError(
            f"unknown class_path {class_path!r}: attribute {attr_str!r} not found"
        )
    if not isinstance(cls, type):
        raise NoActiveModelError(
            f"unknown class_path {class_path!r}: {attr_str!r} is not a class"
        )
    return cls
