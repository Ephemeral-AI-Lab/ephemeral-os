"""LSP plugin in-sandbox runtime entry point."""

from __future__ import annotations

from typing import Any

from sandbox.plugin.runtime import register_plugin_op

from plugins.catalog.lsp.runtime.apply import apply_workspace_edit
from plugins.catalog.lsp.runtime.session_manager import get_session


async def warm_plugin_runtime(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """Start the Pyright sidecar during plugin ensure so first tool calls are warm."""
    del args
    session = await get_session(ctx)
    await session.start()
    return {
        "success": True,
        "manifest_key": session.manifest_key,
    }


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


@register_plugin_op("lsp", "apply_workspace_edit")
async def apply_workspace_edit_op(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    edit = args.get("edit") if isinstance(args.get("edit"), dict) else args
    return await apply_workspace_edit(edit, ctx)


@register_plugin_op("lsp", "rename")
async def rename(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    operation = getattr(ctx.overlay, "workspace_operation", None)
    if callable(operation):
        async with operation(reason="lsp:rename:enter"):
            session = await get_session(ctx)
            edit = await session.rename(args)
            result = await apply_workspace_edit(edit, ctx, ensure_current=False)
    else:
        session = await get_session(ctx)
        edit = await session.rename(args)
        result = await apply_workspace_edit(edit, ctx, ensure_current=False)
    return {"edit": edit, "apply": result}


@register_plugin_op("lsp", "format")
async def format_document(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    operation = getattr(ctx.overlay, "workspace_operation", None)
    if callable(operation):
        async with operation(reason="lsp:format:enter"):
            session = await get_session(ctx)
            edit = await session.format_document(args)
            result = await apply_workspace_edit(edit, ctx, ensure_current=False)
    else:
        session = await get_session(ctx)
        edit = await session.format_document(args)
        result = await apply_workspace_edit(edit, ctx, ensure_current=False)
    return {"edit": edit, "apply": result}


@register_plugin_op("lsp", "code_actions")
async def code_actions(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    session = await get_session(ctx)
    return await session.code_actions(args)


@register_plugin_op("lsp", "apply_code_action")
async def apply_code_action(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    action = args.get("action") if isinstance(args.get("action"), dict) else args
    edit = action.get("edit") if isinstance(action.get("edit"), dict) else {}
    result = await apply_workspace_edit(edit, ctx)
    return {"action": action, "apply": result}
