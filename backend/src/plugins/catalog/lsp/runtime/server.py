"""LSP plugin in-sandbox runtime entry point.

Five ``@register_plugin_op('lsp', '<verb>')`` handlers dispatch to a lazy
Pyright session reconciled to the active layer-stack manifest. The session
is owned by :mod:`plugins.catalog.lsp.runtime.session_manager`; this module
is just the dispatcher.
"""

from __future__ import annotations

from typing import Any

from sandbox.plugin.runtime import register_plugin_op

from plugins.catalog.lsp.runtime.session_manager import get_session


@register_plugin_op("lsp", "hover")
async def hover(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    session = await get_session(ctx)
    return await session.hover(args)


@register_plugin_op("lsp", "find_definitions")
async def find_definitions(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    session = await get_session(ctx)
    return await session.find_definitions(args)


@register_plugin_op("lsp", "find_references")
async def find_references(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    session = await get_session(ctx)
    return await session.find_references(args)


@register_plugin_op("lsp", "diagnostics")
async def diagnostics(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    session = await get_session(ctx)
    return await session.diagnostics(args)


@register_plugin_op("lsp", "query_symbols")
async def query_symbols(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    session = await get_session(ctx)
    return await session.query_symbols(args)
