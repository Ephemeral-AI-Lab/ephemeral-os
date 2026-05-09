"""Unit tests for sandbox.plugin.runtime.registry."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from sandbox.plugin.runtime import registry as registry_mod
from sandbox.plugin.runtime.registry import (
    PluginOpConflictError,
    PluginOpRegistrationError,
    flush_plugin_registrations,
    pending_plugin_registrations,
    register_plugin_op,
)


@pytest.fixture(autouse=True)
def _clear_pending() -> Iterator[None]:
    registry_mod._PENDING.clear()
    yield
    registry_mod._PENDING.clear()


def _exec_in_plugin_namespace(plugin_name: str, code: str) -> dict[str, object]:
    """Execute *code* with __name__ set to a plugin runtime module name.

    register_plugin_op uses inspect.stack to read the caller frame's
    __name__; exec() lets us simulate any module name without writing temp
    files to disk.
    """
    namespace: dict[str, object] = {
        "__name__": f"plugins.catalog.{plugin_name}.runtime.synthetic_module",
        "register_plugin_op": register_plugin_op,
    }
    exec(code, namespace)
    return namespace


def test_register_and_flush_happy_path() -> None:
    namespace = _exec_in_plugin_namespace(
        "demo",
        """
async def hover_handler(args):
    return {"ok": True, "args": args}

decorated = register_plugin_op("demo", "hover")(hover_handler)
        """.strip(),
    )

    pending = pending_plugin_registrations("demo")
    assert len(pending) == 1
    assert pending[0].plugin_name == "demo"
    assert pending[0].op_name == "hover"
    assert pending[0].handler is namespace["hover_handler"]

    registered: dict[str, object] = {}

    def fake_dispatcher(op: str, handler: object) -> None:
        registered[op] = handler

    keys = flush_plugin_registrations("demo", fake_dispatcher)
    assert keys == ["plugin.demo.hover"]
    assert registered == {"plugin.demo.hover": namespace["hover_handler"]}


def test_namespace_mismatch_rejected_loudly() -> None:
    with pytest.raises(
        PluginOpRegistrationError, match="only modules under"
    ):
        # Called from this test module — __name__ is the test, not a plugin.
        register_plugin_op("demo", "hover")


def test_identical_re_registration_is_silent_noop() -> None:
    namespace = _exec_in_plugin_namespace(
        "demo",
        """
async def handler(args):
    return {}

register_plugin_op("demo", "hover")(handler)
register_plugin_op("demo", "hover")(handler)
        """.strip(),
    )
    assert len(pending_plugin_registrations("demo")) == 1
    assert namespace["handler"] is pending_plugin_registrations("demo")[0].handler


def test_conflicting_handler_under_same_op_raises() -> None:
    with pytest.raises(PluginOpConflictError, match="already has a different"):
        _exec_in_plugin_namespace(
            "demo",
            """
async def first(args):
    return {}

async def second(args):
    return {}

register_plugin_op("demo", "hover")(first)
register_plugin_op("demo", "hover")(second)
            """.strip(),
        )


def test_flush_only_targets_named_plugin() -> None:
    _exec_in_plugin_namespace(
        "alpha",
        """
async def alpha_handler(args):
    return {}

register_plugin_op("alpha", "ping")(alpha_handler)
        """.strip(),
    )
    _exec_in_plugin_namespace(
        "beta",
        """
async def beta_handler(args):
    return {}

register_plugin_op("beta", "ping")(beta_handler)
        """.strip(),
    )

    seen: list[str] = []
    flush_plugin_registrations("alpha", lambda op, _h: seen.append(op))
    assert seen == ["plugin.alpha.ping"]
    # beta still pending
    assert any(
        entry.plugin_name == "beta"
        for entry in pending_plugin_registrations()
    )


def test_register_requires_non_empty_names() -> None:
    with pytest.raises(PluginOpRegistrationError, match="non-empty"):
        register_plugin_op("", "hover")
    with pytest.raises(PluginOpRegistrationError, match="non-empty"):
        register_plugin_op("demo", "")
