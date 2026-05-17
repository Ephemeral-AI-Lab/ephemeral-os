"""``advisor`` and ``resolver`` recipes — helper agents spawned by parents.

Helper agents (advisor, resolver) inherit the parent's full
:class:`ContextPacket` so they reason inside the parent's frame, not in
isolation (plan §3.3.8).

Inheritance policy: every parent block is copied with priority demoted by
one level (``required → high → medium → low → low``). Inherited blocks
carry ``metadata['inherited_from_parent'] = 'true'`` so the renderer can
group them under ``# Parent context``. The concrete helper request is
appended by the helper tool after composition.
"""

from __future__ import annotations

from task_center.context_engine.core import ContextEngineDeps, ContextEngineError
from task_center.context_engine.packet import (
    ContextBlock,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.recipes_registry import ContextRecipe
from task_center.context_engine.scope import ContextScope

ADVISOR_ID = "advisor"
RESOLVER_ID = "resolver"

_REQUIRED_FIELDS = frozenset({"goal_id", "task_id", "parent_packet_id"})

_DEMOTION = {
    ContextPriority.REQUIRED: ContextPriority.HIGH,
    ContextPriority.HIGH: ContextPriority.MEDIUM,
    ContextPriority.MEDIUM: ContextPriority.LOW,
    ContextPriority.LOW: ContextPriority.LOW,
}


def demote_priority(priority: ContextPriority) -> ContextPriority:
    return _DEMOTION[priority]


def _build_helper_packet(
    *,
    target_role: str,
    scope: ContextScope,
    deps: ContextEngineDeps,
) -> ContextPacket:
    if deps.context_packet_store is None:
        raise ContextEngineError(
            "Helper recipes require ContextEngineDeps.context_packet_store; "
            "wire ContextPacketStore through app startup."
        )
    parent_packet = deps.context_packet_store.get(scope.parent_packet_id)
    if parent_packet is None:
        raise ContextEngineError(
            f"Parent packet {scope.parent_packet_id!r} not found"
        )
    # Skip inherited role_instruction blocks: the helper tool appends its own
    # role_instruction after compose, and the renderer concatenates every
    # role_instruction text. Inheriting the parent's instruction would
    # contaminate user msg 2 with the parent's ask.
    blocks = [
        ContextBlock(
            kind=parent_block.kind,
            priority=demote_priority(parent_block.priority),
            text=parent_block.text,
            source_id=parent_block.source_id,
            source_kind=parent_block.source_kind,
            metadata={**parent_block.metadata, "inherited_from_parent": "true"},
        )
        for parent_block in parent_packet.blocks
        if parent_block.kind != "role_instruction"
    ]
    return ContextPacket(
        target_role=target_role,
        target_id=scope.task_id,
        canonical_refs=ContextRefs(
            goal_id=scope.goal_id,
            task_id=scope.task_id,
        ),
        blocks=blocks,
        metadata={"inherits_from": parent_packet.id},
        source_ids=[b.source_id for b in blocks if b.source_id],
    )


def _advisor_build(scope: ContextScope, deps: ContextEngineDeps) -> ContextPacket:
    return _build_helper_packet(target_role="advisor", scope=scope, deps=deps)


def _resolver_build(scope: ContextScope, deps: ContextEngineDeps) -> ContextPacket:
    return _build_helper_packet(target_role="resolver", scope=scope, deps=deps)


ADVISOR_RECIPE = ContextRecipe(
    id=ADVISOR_ID,
    required_scope_fields=_REQUIRED_FIELDS,
    build=_advisor_build,
)

RESOLVER_RECIPE = ContextRecipe(
    id=RESOLVER_ID,
    required_scope_fields=_REQUIRED_FIELDS,
    build=_resolver_build,
)
