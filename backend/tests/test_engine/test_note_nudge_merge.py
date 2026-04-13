"""Tests for mid-task note nudge metadata propagation.

Verifies that ``edits_since_last_note``, ``files_edited_since_last_note``,
and ``_note_nudge_at_edit`` survive the per-tool-call ``with_overrides`` /
``merge_runtime_metadata`` round-trip used by ``StreamingToolExecutor``.

Regression tests for the bug where these counters were written to a copy
of the metadata and never merged back, so the nudge in
``_run_query_loop`` never fired.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel, Field

from engine.core.streaming_executor import StreamingToolExecutor
from message import ConversationMessage
from providers.types import ApiToolUseDeltaEvent
from tools.core.base import (
    BaseTool,
    BaseToolkit,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
)
from tools.core.runtime import (
    ExecutionMetadata,
    MERGED_RUNTIME_METADATA_KEYS,
    merge_runtime_metadata,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class EditFileInput(BaseModel):
    file_path: str = Field(description="Path to edit")


class FakeEditTool(BaseTool):
    """Simulates daytona_edit_file — increments edit counter on success."""

    name = "daytona_edit_file"
    description = "Edit a file in the sandbox."
    input_model = EditFileInput

    async def execute(
        self, arguments: EditFileInput, context: ToolExecutionContext
    ) -> ToolResult:
        md = getattr(context, "metadata", None)
        if md is not None and md.get("work_item_id"):
            count = md.get("edits_since_last_note", 0) or 0
            md["edits_since_last_note"] = count + 1
            files: list[str] = md.get("files_edited_since_last_note") or []
            if arguments.file_path not in files:
                files.append(arguments.file_path)
            md["files_edited_since_last_note"] = files
        return ToolResult(
            output=json.dumps({"file_path": arguments.file_path, "status": "edited"})
        )


class PostNoteInput(BaseModel):
    content: str = Field(description="Note content")


class FakePostNoteTool(BaseTool):
    """Simulates post_note — resets edit counter."""

    name = "post_note"
    description = "Post a progress note."
    input_model = PostNoteInput

    async def execute(
        self, arguments: PostNoteInput, context: ToolExecutionContext
    ) -> ToolResult:
        md = getattr(context, "metadata", None)
        if md is not None:
            md["edits_since_last_note"] = 0
            md["files_edited_since_last_note"] = []
        return ToolResult(output="Note posted.")


def _make_registry(*tools: BaseTool) -> ToolRegistry:
    registry = ToolRegistry()
    toolkit = BaseToolkit(name="test", description="Test", tools=list(tools))
    registry.register_toolkit(toolkit)
    return registry


def _make_team_metadata(**overrides) -> ExecutionMetadata:
    base = ExecutionMetadata(
        sandbox_id="sbx-1",
        team_run_id="TR1",
        work_item_id="W1",
        agent_name="developer",
    )
    for k, v in overrides.items():
        base[k] = v
    return base


def _make_assistant_msg() -> ConversationMessage:
    return ConversationMessage(role="assistant", content=[])


def _make_tool_event(tool_id: str, name: str, tool_input: dict) -> ApiToolUseDeltaEvent:
    return ApiToolUseDeltaEvent(
        id=tool_id,
        name=name,
        input=tool_input,
    )


# ---------------------------------------------------------------------------
# Unit tests: merge_runtime_metadata
# ---------------------------------------------------------------------------


class TestMergeRuntimeMetadata:
    """Verify nudge keys are in the merge allowlist and propagate correctly."""

    def test_nudge_keys_in_merged_set(self):
        assert "edits_since_last_note" in MERGED_RUNTIME_METADATA_KEYS
        assert "files_edited_since_last_note" in MERGED_RUNTIME_METADATA_KEYS
        assert "_note_nudge_at_edit" in MERGED_RUNTIME_METADATA_KEYS

    def test_edit_counter_merges_from_copy_to_original(self):
        original = _make_team_metadata()
        copy = original.with_overrides(tool_id="tc-1")

        # Simulate _track_edit_for_note_nudge on the copy
        copy["edits_since_last_note"] = 1
        copy["files_edited_since_last_note"] = ["/testbed/foo.py"]

        merge_runtime_metadata(original=original, updated=copy)

        assert original.get("edits_since_last_note") == 1
        assert original.get("files_edited_since_last_note") == ["/testbed/foo.py"]

    def test_edit_counter_accumulates_across_sequential_merges(self):
        """Three sequential tool calls each incrementing the counter."""
        original = _make_team_metadata()
        files = ["/testbed/a.py", "/testbed/b.py", "/testbed/c.py"]

        for i, f in enumerate(files, 1):
            copy = original.with_overrides(tool_id=f"tc-{i}")
            count = copy.get("edits_since_last_note", 0) or 0
            copy["edits_since_last_note"] = count + 1
            edited = copy.get("files_edited_since_last_note") or []
            edited.append(f)
            copy["files_edited_since_last_note"] = edited
            merge_runtime_metadata(original=original, updated=copy)

        assert original.get("edits_since_last_note") == 3
        assert original.get("files_edited_since_last_note") == files

    def test_nudge_at_edit_marker_merges_back(self):
        original = _make_team_metadata()
        copy = original.with_overrides(tool_id="tc-1")
        copy["_note_nudge_at_edit"] = 3

        merge_runtime_metadata(original=original, updated=copy)

        assert original.get("_note_nudge_at_edit") == 3

    def test_post_note_reset_merges_back(self):
        """After post_note resets the counter to 0, the original should reflect it."""
        original = _make_team_metadata()
        original["edits_since_last_note"] = 5
        original["files_edited_since_last_note"] = ["/a.py", "/b.py"]

        copy = original.with_overrides(tool_id="tc-post")
        # post_note resets
        copy["edits_since_last_note"] = 0
        copy["files_edited_since_last_note"] = []

        merge_runtime_metadata(original=original, updated=copy)

        # 0 is falsy but not None, so the merge should still propagate it
        assert original.extras.get("edits_since_last_note") == 0
        assert original.extras.get("files_edited_since_last_note") == []

    def test_with_overrides_creates_independent_copy(self):
        """Mutations on copy must not affect original until merge."""
        original = _make_team_metadata()
        copy = original.with_overrides(tool_id="tc-1")

        copy["edits_since_last_note"] = 99
        copy["files_edited_since_last_note"] = ["/modified.py"]

        # Before merge, original is untouched
        assert original.get("edits_since_last_note", 0) == 0
        assert original.get("files_edited_since_last_note") is None

    def test_merge_does_not_overwrite_with_none(self):
        """If the copy never set a nudge key, original keeps its value."""
        original = _make_team_metadata()
        original["edits_since_last_note"] = 2
        original["files_edited_since_last_note"] = ["/a.py"]

        # Copy where no edits happened (read-only tool)
        copy = original.with_overrides(tool_id="tc-read")
        # Don't touch edits_since_last_note on the copy at all
        # But it was copied from original, so it should have 2
        merge_runtime_metadata(original=original, updated=copy)

        # Should preserve the value from the copy (which is 2)
        assert original.get("edits_since_last_note") == 2


# ---------------------------------------------------------------------------
# Integration tests: StreamingToolExecutor round-trip
# ---------------------------------------------------------------------------


class TestStreamingExecutorNudgePropagation:
    """End-to-end: edit counter survives StreamingToolExecutor dispatch."""

    @pytest.mark.asyncio
    async def test_single_edit_increments_counter(self):
        meta = _make_team_metadata()
        registry = _make_registry(FakeEditTool())
        executor = StreamingToolExecutor(
            tool_registry=registry,
            context=ToolExecutionContext(cwd="/tmp", metadata=meta),
        )

        event = _make_tool_event("tc-1", "daytona_edit_file", {"file_path": "/testbed/foo.py"})
        executor.add_tool(event, _make_assistant_msg())
        results = await executor.get_remaining()

        assert len(results) == 1
        assert not results[0].is_error
        assert meta.get("edits_since_last_note") == 1
        assert meta.get("files_edited_since_last_note") == ["/testbed/foo.py"]

    @pytest.mark.asyncio
    async def test_three_edits_reach_nudge_threshold(self):
        meta = _make_team_metadata()
        registry = _make_registry(FakeEditTool())
        files = ["/testbed/a.py", "/testbed/b.py", "/testbed/c.py"]

        for i, f in enumerate(files):
            executor = StreamingToolExecutor(
                tool_registry=registry,
                context=ToolExecutionContext(cwd="/tmp", metadata=meta),
            )
            event = _make_tool_event(f"tc-{i}", "daytona_edit_file", {"file_path": f})
            executor.add_tool(event, _make_assistant_msg())
            await executor.get_remaining()

        assert meta.get("edits_since_last_note") == 3
        assert meta.get("files_edited_since_last_note") == files
        # Nudge condition: edits >= 3 and edits - last_nudge >= 3
        edits = meta.get("edits_since_last_note", 0) or 0
        last_nudge = meta.get("_note_nudge_at_edit", 0) or 0
        assert edits >= 3 and edits - last_nudge >= 3

    @pytest.mark.asyncio
    async def test_post_note_resets_counter_via_executor(self):
        meta = _make_team_metadata()
        registry = _make_registry(FakeEditTool(), FakePostNoteTool())

        # Make 3 edits
        for i in range(3):
            executor = StreamingToolExecutor(
                tool_registry=registry,
                context=ToolExecutionContext(cwd="/tmp", metadata=meta),
            )
            event = _make_tool_event(
                f"tc-{i}", "daytona_edit_file", {"file_path": f"/testbed/{i}.py"}
            )
            executor.add_tool(event, _make_assistant_msg())
            await executor.get_remaining()

        assert meta.get("edits_since_last_note") == 3

        # Post a note — counter resets
        executor = StreamingToolExecutor(
            tool_registry=registry,
            context=ToolExecutionContext(cwd="/tmp", metadata=meta),
        )
        note_event = _make_tool_event("tc-note", "post_note", {"content": "progress"})
        executor.add_tool(note_event, _make_assistant_msg())
        await executor.get_remaining()

        assert meta.extras.get("edits_since_last_note") == 0
        assert meta.extras.get("files_edited_since_last_note") == []

    @pytest.mark.asyncio
    async def test_counter_survives_five_sequential_tool_calls(self):
        """Counter accumulates correctly across many executor instances."""
        meta = _make_team_metadata()
        registry = _make_registry(FakeEditTool())

        for i in range(5):
            executor = StreamingToolExecutor(
                tool_registry=registry,
                context=ToolExecutionContext(cwd="/tmp", metadata=meta),
            )
            event = _make_tool_event(
                f"tc-{i}", "daytona_edit_file", {"file_path": f"/testbed/file{i}.py"}
            )
            executor.add_tool(event, _make_assistant_msg())
            await executor.get_remaining()

        assert meta.get("edits_since_last_note") == 5
        assert len(meta.get("files_edited_since_last_note")) == 5

    @pytest.mark.asyncio
    async def test_duplicate_file_edits_counted_but_file_list_deduplicated(self):
        """Editing the same file twice increments counter but not file list."""
        meta = _make_team_metadata()
        registry = _make_registry(FakeEditTool())

        for i in range(3):
            executor = StreamingToolExecutor(
                tool_registry=registry,
                context=ToolExecutionContext(cwd="/tmp", metadata=meta),
            )
            # Same file each time
            event = _make_tool_event(
                f"tc-{i}", "daytona_edit_file", {"file_path": "/testbed/same.py"}
            )
            executor.add_tool(event, _make_assistant_msg())
            await executor.get_remaining()

        assert meta.get("edits_since_last_note") == 3
        assert meta.get("files_edited_since_last_note") == ["/testbed/same.py"]

    @pytest.mark.asyncio
    async def test_no_tracking_without_work_item_id(self):
        """Non-team metadata (no work_item_id) should not track edits."""
        meta = ExecutionMetadata(sandbox_id="sbx-1")
        registry = _make_registry(FakeEditTool())
        executor = StreamingToolExecutor(
            tool_registry=registry,
            context=ToolExecutionContext(cwd="/tmp", metadata=meta),
        )

        event = _make_tool_event("tc-1", "daytona_edit_file", {"file_path": "/testbed/foo.py"})
        executor.add_tool(event, _make_assistant_msg())
        await executor.get_remaining()

        assert meta.get("edits_since_last_note", 0) == 0


# ---------------------------------------------------------------------------
# Nudge condition tests
# ---------------------------------------------------------------------------


class TestNudgeConditionLogic:
    """Verify the nudge fires at the right thresholds."""

    def _should_nudge(self, meta: ExecutionMetadata) -> bool:
        """Replicate the nudge condition from _run_query_loop."""
        edits = meta.get("edits_since_last_note", 0) or 0
        last_nudge = meta.get("_note_nudge_at_edit", 0) or 0
        return edits >= 3 and edits - last_nudge >= 3

    def test_no_nudge_at_zero(self):
        assert not self._should_nudge(_make_team_metadata())

    def test_no_nudge_at_two(self):
        meta = _make_team_metadata()
        meta["edits_since_last_note"] = 2
        assert not self._should_nudge(meta)

    def test_nudge_at_three(self):
        meta = _make_team_metadata()
        meta["edits_since_last_note"] = 3
        assert self._should_nudge(meta)

    def test_no_re_nudge_until_three_more_edits(self):
        meta = _make_team_metadata()
        meta["edits_since_last_note"] = 3
        meta["_note_nudge_at_edit"] = 3  # Nudge already fired at 3
        assert not self._should_nudge(meta)

        meta["edits_since_last_note"] = 5
        assert not self._should_nudge(meta)

        meta["edits_since_last_note"] = 6
        assert self._should_nudge(meta)  # 6 - 3 = 3, fires again

    def test_nudge_after_post_note_and_more_edits(self):
        meta = _make_team_metadata()
        # Had 5 edits, nudge fired at 3, then posted note (reset to 0)
        meta["edits_since_last_note"] = 0
        meta["_note_nudge_at_edit"] = 0
        assert not self._should_nudge(meta)

        # 3 more edits
        meta["edits_since_last_note"] = 3
        assert self._should_nudge(meta)
