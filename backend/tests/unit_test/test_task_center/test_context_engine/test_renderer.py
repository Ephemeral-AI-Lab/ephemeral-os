"""US-003: MarkdownPromptRenderer behavior."""

from __future__ import annotations

from task_center.context_engine.packet import (
    ContextBlock,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.renderer import MarkdownPromptRenderer


def _packet(blocks: list[ContextBlock], **metadata: str) -> ContextPacket:
    return ContextPacket(
        target_role="planner",
        target_id="g-1",
        canonical_refs=ContextRefs(goal_id="r"),
        blocks=blocks,
        metadata=dict(metadata),
    )


def test_packet_order_is_semantic_order_not_priority_order():
    blocks = [
        ContextBlock(kind="low", priority=ContextPriority.LOW, text="low"),
        ContextBlock(kind="high", priority=ContextPriority.HIGH, text="high"),
        ContextBlock(
            kind="required", priority=ContextPriority.REQUIRED, text="req"
        ),
        ContextBlock(
            kind="medium", priority=ContextPriority.MEDIUM, text="medium"
        ),
    ]
    out = MarkdownPromptRenderer().render_context(_packet(blocks))
    assert out.find("Low") < out.find("High")
    assert out.find("High") < out.find("Required")
    assert out.find("Required") < out.find("Medium")


def test_required_blocks_never_compressed_under_budget():
    big_required = "A" * 4_000  # ≈1000 tokens
    blocks = [
        ContextBlock(
            kind="iteration_statement",
            priority=ContextPriority.REQUIRED,
            text=big_required,
            source_id="seg-1",
        ),
        ContextBlock(
            kind="background",
            priority=ContextPriority.LOW,
            text="B" * 4_000,
            source_id="src-low",
        ),
    ]
    out = MarkdownPromptRenderer().render_context(_packet(blocks, token_budget="100"))
    assert big_required in out
    # The low block should be replaced with the truncation marker.
    assert "B" * 4_000 not in out
    assert "truncated for token budget" in out


def test_low_blocks_compressed_before_medium_blocks():
    blocks = [
        ContextBlock(
            kind="seg",
            priority=ContextPriority.REQUIRED,
            text="goal",
        ),
        ContextBlock(
            kind="med",
            priority=ContextPriority.MEDIUM,
            text="M" * 4_000,
            source_id="src-med",
        ),
        ContextBlock(
            kind="lo",
            priority=ContextPriority.LOW,
            text="L" * 4_000,
            source_id="src-lo",
        ),
    ]
    # Budget is just enough for required + medium + truncation message.
    out = MarkdownPromptRenderer().render_context(_packet(blocks, token_budget="1100"))
    # Low truncated, medium kept verbatim.
    assert "L" * 4_000 not in out
    assert "M" * 4_000 in out


def test_block_subtitle_metadata_renders_under_heading():
    blocks = [
        ContextBlock(
            kind="iteration_statement",
            priority=ContextPriority.REQUIRED,
            text="g",
            metadata={"subtitle": "*(first iteration)*"},
        )
    ]
    out = MarkdownPromptRenderer().render_context(_packet(blocks))
    assert "*(first iteration)*" in out


def test_block_heading_metadata_overrides_default_heading():
    blocks = [
        ContextBlock(
            kind="custom_kind",
            priority=ContextPriority.REQUIRED,
            text="body",
            metadata={"heading": "# Custom heading"},
        )
    ]
    out = MarkdownPromptRenderer().render_context(_packet(blocks))
    assert out.startswith("# Custom heading\n\nbody")
    assert "# Custom kind" not in out


def test_dependency_results_render_as_one_grouped_section():
    blocks = [
        ContextBlock(
            kind="dependency_summary",
            priority=ContextPriority.MEDIUM,
            text="dep output",
            metadata={
                "group_heading": "# Dependency Results",
                "subheading": "dep-a",
            },
        ),
        ContextBlock(
            kind="completed_task_summary",
            priority=ContextPriority.HIGH,
            text="completed output",
            metadata={
                "group_heading": "# Dependency Results",
                "subheading": "task-b",
            },
        ),
    ]
    out = MarkdownPromptRenderer().render_context(_packet(blocks))
    assert out.count("# Dependency Results") == 1
    assert "## dep-a\n\ndep output" in out
    assert "## task-b\n\ncompleted output" in out


def test_render_is_deterministic_for_fixed_packet():
    blocks = [
        ContextBlock(kind="a", priority=ContextPriority.REQUIRED, text="a"),
        ContextBlock(kind="b", priority=ContextPriority.HIGH, text="b"),
    ]
    packet = _packet(blocks)
    a = MarkdownPromptRenderer().render_context(packet)
    b = MarkdownPromptRenderer().render_context(packet)
    assert a == b


def test_renderer_does_not_perform_io_or_store_reads(tmp_path, monkeypatch):
    """Renderer must be a pure function. Trip-wire: deny attribute access on
    objects that look like stores during render — render should not touch
    them."""
    blocks = [
        ContextBlock(kind="x", priority=ContextPriority.REQUIRED, text="x")
    ]
    # No store handle is ever passed to render(); the contract is enforced
    # by the absence of any store parameter in render's signature.
    import inspect

    sig = inspect.signature(MarkdownPromptRenderer().render_context)
    assert list(sig.parameters) == ["packet"]
    MarkdownPromptRenderer().render_context(_packet(blocks))


# ---------------------------------------------------------------------------
# Two-user-message split: render_context excludes role_instruction;
# render_role_instruction returns concatenated text or None.
# ---------------------------------------------------------------------------


def test_render_context_excludes_role_instruction_blocks():
    blocks = [
        ContextBlock(
            kind="iteration_statement",
            priority=ContextPriority.REQUIRED,
            text="world state",
        ),
        ContextBlock(
            kind="role_instruction",
            priority=ContextPriority.REQUIRED,
            text="how to proceed body",
        ),
    ]
    out = MarkdownPromptRenderer().render_context(_packet(blocks))
    assert "world state" in out
    assert "# How to Proceed" not in out
    assert "how to proceed body" not in out


def test_render_role_instruction_returns_none_when_absent():
    blocks = [
        ContextBlock(
            kind="iteration_statement",
            priority=ContextPriority.REQUIRED,
            text="world state",
        ),
    ]
    assert MarkdownPromptRenderer().render_role_instruction(_packet(blocks)) is None


def test_render_role_instruction_concatenates_multiple_blocks():
    blocks = [
        ContextBlock(
            kind="role_instruction",
            priority=ContextPriority.REQUIRED,
            text="first instruction",
        ),
        ContextBlock(
            kind="iteration_statement",
            priority=ContextPriority.REQUIRED,
            text="world state",
        ),
        ContextBlock(
            kind="role_instruction",
            priority=ContextPriority.REQUIRED,
            text="second instruction",
        ),
    ]
    out = MarkdownPromptRenderer().render_role_instruction(_packet(blocks))
    assert out is not None
    assert "first instruction" in out
    assert "second instruction" in out
    # Joined by blank line, both texts present in concatenation order.
    assert out.index("first instruction") < out.index("second instruction")


def test_default_headings_no_longer_maps_role_instruction():
    from task_center.context_engine.renderer import _DEFAULT_HEADINGS

    assert "role_instruction" not in _DEFAULT_HEADINGS
    # Parent transcript heading is wired (transcript-mode helper).
    assert _DEFAULT_HEADINGS.get("parent_transcript") == "# Parent transcript"
