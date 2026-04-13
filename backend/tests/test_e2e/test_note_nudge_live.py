# ruff: noqa
"""Live E2E: mid-task note nudge notification fires after 3+ file edits.

Verifies the full pipeline: agent makes sequential edits in a real Daytona
sandbox, the query loop detects the accumulated edit counter, and a
SystemNotification with category ``note_nudge`` is injected — prompting the
agent to call ``post_note``.

This is a regression test for the bug where ``edits_since_last_note`` was
written to a per-tool-call metadata copy (via ``with_overrides``) and never
merged back to the original metadata, so the nudge never fired.

Requires live LLM API + Daytona sandbox.
Run with: pytest tests/test_e2e/test_note_nudge_live.py -m live -v
"""

from __future__ import annotations

import pytest

from engine.testing.eval_agent import EvalAgent
from message.stream_events import SystemNotification
from tests.test_e2e.conftest import create_eval_agent, create_test_sandbox, delete_test_sandbox

pytestmark = [pytest.mark.e2e, pytest.mark.live]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sandbox_id():
    if not EvalAgent.has_all():
        pytest.skip("LLM + Daytona credentials required")
    sb = create_test_sandbox("note-nudge")
    yield sb["id"]
    delete_test_sandbox(sb["id"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _inject_team_metadata(agent: EvalAgent, *, work_item_id: str, team_run_id: str) -> None:
    """Set team-mode metadata fields on an EvalAgent's query context.

    The note nudge tracker requires ``work_item_id`` to be present —
    without it, ``_track_edit_for_note_nudge`` returns early. This
    helper simulates team mode without needing the full team executor.
    """
    meta = agent._query_context.tool_metadata
    meta.work_item_id = work_item_id
    meta.team_run_id = team_run_id
    meta["team_mode_enabled"] = True


_EDIT_HEAVY_SYSTEM_PROMPT = (
    "You are a developer working in a team sandbox. "
    "You have access to daytona_write_file and daytona_edit_file for editing files, "
    "daytona_codeact for running commands, and post_note for sharing progress. "
    "When asked to create multiple files, use daytona_write_file for EACH file individually. "
    "Do NOT combine files into a single tool call. "
    "You MUST create each file using a separate tool call."
)

_EDIT_HEAVY_PROMPT = (
    "Create these 5 files in /workspace, each with a separate daytona_write_file call:\n"
    "1. /workspace/note_nudge_a.py — content: 'a = 1'\n"
    "2. /workspace/note_nudge_b.py — content: 'b = 2'\n"
    "3. /workspace/note_nudge_c.py — content: 'c = 3'\n"
    "4. /workspace/note_nudge_d.py — content: 'd = 4'\n"
    "5. /workspace/note_nudge_e.py — content: 'e = 5'\n"
    "Create each file one at a time."
)


def _note_nudge_events(result) -> list[SystemNotification]:
    """Extract SystemNotification events with category ``note_nudge``."""
    return [
        e
        for e in result.system_notifications()
        if getattr(e, "category", None) == "note_nudge"
    ]


# ===========================================================================
# AREA 1: Note nudge fires after batch of edits
# ===========================================================================


@pytest.mark.asyncio
async def test_note_nudge_fires_after_multiple_writes(sandbox_id):
    """Agent making 3+ file writes should trigger a note_nudge notification.

    The nudge fires at the top of the next query-loop turn after the edit
    counter reaches 3. This test creates 5 files and verifies the nudge
    appears at least once in the event stream.
    """
    agent = create_eval_agent(
        sandbox_id=sandbox_id,
        system_prompt=_EDIT_HEAVY_SYSTEM_PROMPT,
    )
    _inject_team_metadata(agent, work_item_id="W-nudge-test", team_run_id="TR-nudge-test")

    result = await agent.invoke(_EDIT_HEAVY_PROMPT)

    # Should have made at least 3 write calls
    write_count = result.tool_count("daytona_write_file")
    assert write_count >= 3, (
        f"Expected at least 3 daytona_write_file calls, got {write_count}. "
        f"Tools used: {result.tool_names}"
    )

    # The note_nudge SystemNotification should have fired
    nudges = _note_nudge_events(result)
    assert len(nudges) >= 1, (
        f"Expected note_nudge notification after {write_count} file edits, "
        f"but none fired. "
        f"All system notifications: "
        f"{[(n.category, n.text[:60]) for n in result.system_notifications()]}. "
        f"Tool sequence: {result.tool_names}"
    )

    # Nudge text should mention the edit count and post_note
    nudge_text = nudges[0].text
    assert "post_note" in nudge_text, (
        f"Nudge text should mention post_note. Got: {nudge_text}"
    )
    assert "file edits" in nudge_text, (
        f"Nudge text should mention file edits. Got: {nudge_text}"
    )


@pytest.mark.asyncio
async def test_note_nudge_includes_edited_file_paths(sandbox_id):
    """Nudge notification should list the files that were edited."""
    agent = create_eval_agent(
        sandbox_id=sandbox_id,
        system_prompt=_EDIT_HEAVY_SYSTEM_PROMPT,
    )
    _inject_team_metadata(agent, work_item_id="W-paths-test", team_run_id="TR-paths-test")

    result = await agent.invoke(
        "Create these 3 files, each with a separate daytona_write_file call:\n"
        "1. /workspace/path_a.py — content: 'x = 1'\n"
        "2. /workspace/path_b.py — content: 'y = 2'\n"
        "3. /workspace/path_c.py — content: 'z = 3'\n"
    )

    nudges = _note_nudge_events(result)
    if nudges:
        nudge_text = nudges[0].text
        # At least one of the created files should appear in the nudge
        has_file_ref = any(
            f in nudge_text
            for f in ("path_a.py", "path_b.py", "path_c.py")
        )
        assert has_file_ref, (
            f"Nudge should list edited files. Got: {nudge_text}"
        )


# ===========================================================================
# AREA 2: No nudge without team context
# ===========================================================================


@pytest.mark.asyncio
async def test_no_nudge_without_work_item_id(sandbox_id):
    """Without work_item_id (non-team mode), no nudge should fire."""
    agent = create_eval_agent(
        sandbox_id=sandbox_id,
        system_prompt=_EDIT_HEAVY_SYSTEM_PROMPT,
        # No _inject_team_metadata — no work_item_id
    )

    result = await agent.invoke(
        "Create these 4 files, each with a separate daytona_write_file call:\n"
        "1. /workspace/no_nudge_a.py — content: 'a = 1'\n"
        "2. /workspace/no_nudge_b.py — content: 'b = 2'\n"
        "3. /workspace/no_nudge_c.py — content: 'c = 3'\n"
        "4. /workspace/no_nudge_d.py — content: 'd = 4'\n"
    )

    nudges = _note_nudge_events(result)
    assert len(nudges) == 0, (
        f"Should NOT fire note_nudge without work_item_id. "
        f"Got {len(nudges)} nudge(s). "
        f"Tool sequence: {result.tool_names}"
    )


# ===========================================================================
# AREA 3: Post-note reset cycle
# ===========================================================================


@pytest.mark.asyncio
async def test_nudge_resets_after_post_note(sandbox_id):
    """After posting a note, the counter resets and a new batch of 3
    edits is needed before the nudge fires again.

    This test uses two sequential invocations on the same agent to
    simulate the full cycle: edits -> nudge -> post_note -> more edits.
    """
    agent = create_eval_agent(
        sandbox_id=sandbox_id,
        system_prompt=(
            "You are a developer in team mode. "
            "Use daytona_write_file for file creation. "
            "Use post_note to share progress when asked. "
            "Do NOT combine multiple files into one call."
        ),
    )
    _inject_team_metadata(agent, work_item_id="W-reset-test", team_run_id="TR-reset-test")

    # Turn 1: Create 4 files — should trigger nudge
    result1 = await agent.invoke(
        "Create these 4 files with separate daytona_write_file calls:\n"
        "1. /workspace/reset_a.py — content: 'a = 1'\n"
        "2. /workspace/reset_b.py — content: 'b = 2'\n"
        "3. /workspace/reset_c.py — content: 'c = 3'\n"
        "4. /workspace/reset_d.py — content: 'd = 4'\n"
    )

    # Turn 2: Post a note to reset the counter
    result2 = await agent.invoke(
        "Post a progress note using post_note with content 'Created 4 files for reset test' "
        "and scope_paths=['/workspace']."
    )

    # Verify post_note was called
    assert "post_note" in result2.tool_names, (
        f"Expected post_note call. Tools used: {result2.tool_names}"
    )

    # Turn 3: Create 2 more files — should NOT trigger nudge (under threshold)
    result3 = await agent.invoke(
        "Create these 2 files with separate daytona_write_file calls:\n"
        "1. /workspace/reset_e.py — content: 'e = 5'\n"
        "2. /workspace/reset_f.py — content: 'f = 6'\n"
    )

    nudges_turn3 = _note_nudge_events(result3)
    assert len(nudges_turn3) == 0, (
        f"Should NOT nudge after post_note with only 2 new edits. "
        f"Got {len(nudges_turn3)} nudge(s). "
        f"Tool sequence: {result3.tool_names}"
    )


# ===========================================================================
# AREA 4: Edit tool also triggers nudge (not just write)
# ===========================================================================


@pytest.mark.asyncio
async def test_nudge_fires_for_edit_file_tool(sandbox_id):
    """daytona_edit_file should also increment the edit counter and trigger nudge."""
    agent = create_eval_agent(
        sandbox_id=sandbox_id,
        system_prompt=(
            "You are a developer in team mode. "
            "Use daytona_write_file to create files and daytona_edit_file to modify them. "
            "Do NOT use daytona_codeact for editing files."
        ),
    )
    _inject_team_metadata(agent, work_item_id="W-edit-nudge", team_run_id="TR-edit-nudge")

    # First create a file
    await agent.invoke(
        "Create /workspace/editable.py with content:\n"
        "x = 1\ny = 2\nz = 3\nw = 4\n"
    )

    # Now make 4 sequential edits to the same file
    result = await agent.invoke(
        "Make these 4 edits to /workspace/editable.py using daytona_edit_file, "
        "one at a time with separate tool calls:\n"
        "1. Change 'x = 1' to 'x = 10'\n"
        "2. Change 'y = 2' to 'y = 20'\n"
        "3. Change 'z = 3' to 'z = 30'\n"
        "4. Change 'w = 4' to 'w = 40'\n"
    )

    edit_count = result.tool_count("daytona_edit_file")
    if edit_count >= 3:
        nudges = _note_nudge_events(result)
        assert len(nudges) >= 1, (
            f"Expected note_nudge after {edit_count} daytona_edit_file calls, "
            f"but none fired. "
            f"Tool sequence: {result.tool_names}"
        )
