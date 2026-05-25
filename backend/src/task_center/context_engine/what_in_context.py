"""Deterministic ``What's in context`` outline for ``<Task Guidance>``.

Walks the same :class:`ContextPacket` the :class:`XmlPromptRenderer` consumes,
so the outline mirrors the rendered ``<context>`` tree exactly. The algorithm:

1. Walk blocks in packet order; group consecutive blocks that share
   ``metadata['group_id']``.
2. For each top-level entry (a standalone block or a group), look its
   ``(tag, semantic-attrs)`` up in :data:`TAG_DICTIONARY` and emit one bullet
   ``- <tag attrs> — canonical label``. Consecutive same-descriptor siblings
   collapse to one bullet.
3. For each entry whose tag is in :data:`RECURSE_THROUGH`, recurse one level
   into the group's children (indented by two spaces). Max recursion depth is
   bounded by ``max_depth`` (default 2). ``<attempt>`` is *not* in
   ``RECURSE_THROUGH`` — its body lives in the rendered XML, not in the
   outline.

The walker reads the same metadata keys the renderer reads
(``tag``/``child_tag``/``group_tag``/``group_id``/``attrs``/``group_attrs``)
so the two outputs stay in lockstep without re-parsing rendered XML.
"""

from __future__ import annotations

from dataclasses import dataclass

from task_center.context_engine.packet import ContextBlock, ContextPacket
from task_center.context_engine.tag_dictionary import (
    RECURSE_THROUGH,
    TagDescriptor,
    match,
    render_attrs,
)

_INDENT = "  "


@dataclass(frozen=True, slots=True)
class _OutlineNode:
    """One bullet's worth of data: tag, attrs, label, and any children."""

    tag: str
    attrs: dict[str, str]
    descriptor: TagDescriptor
    children: tuple["_OutlineNode", ...] = ()


def render_what_in_context(packet: ContextPacket, max_depth: int = 2) -> str:
    """Return the bulleted ``What's in context`` outline for ``packet``."""
    top_level = _walk_top_level(list(packet.blocks))
    nodes = _collapse_consecutive(top_level)
    return _render_nodes(nodes, depth=1, max_depth=max_depth)


def _walk_top_level(blocks: list[ContextBlock]) -> list[_OutlineNode]:
    """Walk packet blocks once, returning one node per top-level entry."""
    nodes: list[_OutlineNode] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        group_id = block.metadata.get("group_id")
        if group_id:
            group: list[ContextBlock] = []
            while index < len(blocks) and blocks[index].metadata.get("group_id") == group_id:
                group.append(blocks[index])
                index += 1
            node = _node_for_group(group)
            if node is not None:
                nodes.append(node)
            continue
        node = _node_for_standalone(block)
        if node is not None:
            nodes.append(node)
        index += 1
    return nodes


def _node_for_standalone(block: ContextBlock) -> _OutlineNode | None:
    tag = _tag_for_standalone(block)
    if tag is None:
        return None
    attrs = _parse_attrs(block.metadata.get("attrs", ""))
    descriptor = match(tag, attrs)
    if descriptor is None:
        return None
    return _OutlineNode(tag=tag, attrs=attrs, descriptor=descriptor)


def _node_for_group(group: list[ContextBlock]) -> _OutlineNode | None:
    first = group[0]
    group_tag = first.metadata.get("group_tag")
    if not group_tag:
        return None
    group_attrs = _parse_attrs(first.metadata.get("group_attrs", ""))
    descriptor = match(group_tag, group_attrs)
    if descriptor is None:
        return None
    children: list[_OutlineNode] = []
    if group_tag in RECURSE_THROUGH:
        for block in group:
            child_tag = block.metadata.get("child_tag")
            if not child_tag:
                continue
            child_attrs = _parse_attrs(block.metadata.get("attrs", ""))
            child_descriptor = match(child_tag, child_attrs)
            if child_descriptor is None:
                continue
            children.append(
                _OutlineNode(
                    tag=child_tag,
                    attrs=child_attrs,
                    descriptor=child_descriptor,
                )
            )
        children = _collapse_consecutive(children)
    return _OutlineNode(
        tag=group_tag,
        attrs=group_attrs,
        descriptor=descriptor,
        children=tuple(children),
    )


def _tag_for_standalone(block: ContextBlock) -> str | None:
    """Return the rendered tag for a non-grouped block.

    Mirrors :meth:`XmlPromptRenderer._tag_for`: prefer ``metadata['tag']``,
    otherwise fall through to the kind→tag fallback used by the renderer's
    default ``_DEFAULT_TAGS``. Returns ``None`` when no tag is set and the
    kind has no fallback — the renderer would raise; the walker quietly
    skips so the outline never references content that won't ship.
    """
    explicit = block.metadata.get("tag")
    if explicit:
        return explicit
    # Lazy import to avoid an import cycle at module load.
    from task_center.context_engine.renderer import _DEFAULT_TAGS

    return _DEFAULT_TAGS.get(block.kind)


def _parse_attrs(attrs_str: str) -> dict[str, str]:
    """Parse a raw ``attrs=...`` string into a dict.

    Recipes emit attrs as a literal XML attribute fragment
    (``'iteration_no="1" status="current"'``). The walker only consumes
    semantic and identity attributes, so a simple key="value" split is
    sufficient.
    """
    if not attrs_str:
        return {}
    out: dict[str, str] = {}
    remaining = attrs_str.strip()
    while remaining:
        eq = remaining.find("=")
        if eq == -1:
            break
        key = remaining[:eq].strip()
        rest = remaining[eq + 1 :].lstrip()
        if not rest or rest[0] != '"':
            break
        end = rest.find('"', 1)
        if end == -1:
            break
        out[key] = rest[1:end]
        remaining = rest[end + 1 :].lstrip()
    return out


def _collapse_consecutive(nodes: list[_OutlineNode]) -> list[_OutlineNode]:
    """Collapse runs of nodes that share the same descriptor."""
    if not nodes:
        return nodes
    collapsed: list[_OutlineNode] = [nodes[0]]
    for node in nodes[1:]:
        prev = collapsed[-1]
        if prev.descriptor is node.descriptor and prev.children == node.children:
            continue
        collapsed.append(node)
    return collapsed


def _render_nodes(nodes: list[_OutlineNode], *, depth: int, max_depth: int) -> str:
    lines: list[str] = []
    for node in nodes:
        lines.append(_render_one(node, depth=depth, max_depth=max_depth))
    return "\n".join(lines)


def _render_one(node: _OutlineNode, *, depth: int, max_depth: int) -> str:
    indent = _INDENT * (depth - 1)
    attrs = render_attrs(node.attrs)
    open_tag = f"<{node.tag}{(' ' + attrs) if attrs else ''}>"
    line = f"{indent}- {open_tag} — {node.descriptor.label}"
    if node.children and depth < max_depth:
        nested = _render_nodes(
            list(node.children),
            depth=depth + 1,
            max_depth=max_depth,
        )
        if nested:
            line = line + "\n" + nested
    return line


__all__ = ["render_what_in_context"]
