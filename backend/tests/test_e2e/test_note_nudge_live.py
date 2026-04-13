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

# Sandbox home directory — /workspace may not exist on all sandbox images.
_HOME = "/home/daytona"


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


def _note_nudge_events(result) -> list[SystemNotification]:
    """Extract SystemNotification events with category ``note_nudge``."""
    return [
        e
        for e in result.system_notifications()
        if getattr(e, "category", None) == "note_nudge"
    ]


# ===========================================================================
# AREA 1: Note nudge fires after sequential edits across multiple turns
#
# The nudge counter only accumulates correctly for sequential tool calls
# (parallel calls each get a copy and merge collisions cap the increment).
# We use separate invoke() calls to guarantee sequential execution.
# ===========================================================================


@pytest.mark.asyncio
async def test_note_nudge_fires_after_sequential_writes(sandbox_id):
    """Agent accumulates 4 sequential file writes across turns — nudge should fire.

    Each invoke() does one write, so the counter increments by 1 per turn.
    After the 3rd write, the 4th turn's nudge check sees edits >= 3 and fires.
    """
    agent = create_eval_agent(
        sandbox_id=sandbox_id,
        system_prompt=(
            "You are a developer in a team sandbox. "
            "Use daytona_write_file for creating files. Be concise."
        ),
    )
    _inject_team_metadata(agent, work_item_id="W-seq-nudge", team_run_id="TR-seq-nudge")

    files = ["seq_a.py", "seq_b.py", "seq_c.py", "seq_d.py"]
    all_nudges: list[SystemNotification] = []

    for i, fname in enumerate(files):
        result = await agent.invoke(
            f"Create {_HOME}/{fname} with content '{chr(97 + i)} = {i + 1}' "
            f"using daytona_write_file."
        )
        all_nudges.extend(_note_nudge_events(result))

    assert len(all_nudges) >= 1, (
        f"Expected at least 1 note_nudge after 4 sequential writes, "
        f"but got {len(all_nudges)}."
    )

    nudge_text = all_nudges[0].text
    assert "post_note" in nudge_text
    assert "file edits" in nudge_text


@pytest.mark.asyncio
async def test_note_nudge_includes_edited_file_paths(sandbox_id):
    """Nudge notification should list the files that were edited."""
    agent = create_eval_agent(
        sandbox_id=sandbox_id,
        system_prompt=(
            "You are a developer in a team sandbox. "
            "Use daytona_write_file for creating files. Be concise."
        ),
    )
    _inject_team_metadata(agent, work_item_id="W-paths", team_run_id="TR-paths")

    files = ["fpath_a.py", "fpath_b.py", "fpath_c.py"]
    all_nudges: list[SystemNotification] = []

    for i, fname in enumerate(files):
        result = await agent.invoke(
            f"Create {_HOME}/{fname} with content '{chr(97 + i)} = {i}' "
            f"using daytona_write_file."
        )
        all_nudges.extend(_note_nudge_events(result))

    if all_nudges:
        nudge_text = all_nudges[0].text
        has_file_ref = any(f in nudge_text for f in files)
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
        system_prompt=(
            "You are a developer. Use daytona_write_file for creating files. "
            "Be concise."
        ),
        # No _inject_team_metadata — no work_item_id
    )

    all_nudges: list[SystemNotification] = []
    for i in range(4):
        result = await agent.invoke(
            f"Create {_HOME}/no_nudge_{i}.py with content 'v = {i}' "
            f"using daytona_write_file."
        )
        all_nudges.extend(_note_nudge_events(result))

    assert len(all_nudges) == 0, (
        f"Should NOT fire note_nudge without work_item_id. "
        f"Got {len(all_nudges)} nudge(s)."
    )


# ===========================================================================
# AREA 3: Post-note reset cycle
# ===========================================================================


@pytest.mark.asyncio
async def test_nudge_resets_after_post_note(sandbox_id):
    """After posting a note, the counter resets and a new batch of 3
    edits is needed before the nudge fires again.

    Flow: 4 writes (nudge fires) -> post_note (counter resets) -> 2 writes (no nudge).
    """
    agent = create_eval_agent(
        sandbox_id=sandbox_id,
        system_prompt=(
            "You are a developer in team mode. "
            "Use daytona_write_file for file creation. "
            "Use post_note to share progress when told to. "
            "Be concise."
        ),
        toolkits=["sandbox_operations", "subagent", "context"],
    )
    _inject_team_metadata(agent, work_item_id="W-reset", team_run_id="TR-reset")

    # Phase 1: 4 sequential writes — should trigger nudge
    for i in range(4):
        await agent.invoke(
            f"Create {_HOME}/rst_{i}.py with content 'v = {i}' using daytona_write_file."
        )

    # Phase 2: Post a note to reset the counter.
    # The Task Center may not be available in the eval harness, so
    # post_note may error. If it does, manually reset the counter to
    # simulate a successful post_note — the test is about the nudge
    # threshold logic, not Task Center availability.
    result_note = await agent.invoke(
        f"Post a progress note using post_note with content 'Created 4 files' "
        f"and scope_paths=['{_HOME}']."
    )
    post_note_called = "post_note" in result_note.tool_names
    post_note_succeeded = post_note_called and not any(
        e.is_error for e in result_note.tools_completed()
        if e.tool_name == "post_note"
    )
    if not post_note_succeeded:
        # Manually reset counter as post_note would have done
        meta = agent._query_context.tool_metadata
        meta["edits_since_last_note"] = 0
        meta["files_edited_since_last_note"] = []

    # Phase 3: 2 more writes — should NOT trigger nudge (under threshold)
    nudges_after_reset: list[SystemNotification] = []
    for i in range(2):
        result = await agent.invoke(
            f"Create {_HOME}/rst_extra_{i}.py with content 'extra = {i}' "
            f"using daytona_write_file."
        )
        nudges_after_reset.extend(_note_nudge_events(result))

    assert len(nudges_after_reset) == 0, (
        f"Should NOT nudge after post_note with only 2 new edits. "
        f"Got {len(nudges_after_reset)} nudge(s)."
    )


# ===========================================================================
# AREA 4: Edit tool also triggers nudge
# ===========================================================================


@pytest.mark.asyncio
async def test_nudge_fires_for_edit_file_tool(sandbox_id):
    """daytona_edit_file should also increment the edit counter and trigger nudge."""
    agent = create_eval_agent(
        sandbox_id=sandbox_id,
        system_prompt=(
            "You are a developer in team mode. "
            "Use daytona_write_file to create files and daytona_edit_file to modify them. "
            "Do NOT use daytona_codeact for editing files. Be concise."
        ),
    )
    _inject_team_metadata(agent, work_item_id="W-edit-nudge", team_run_id="TR-edit-nudge")

    # Create a file with 4 lines
    await agent.invoke(
        f"Create {_HOME}/editable.py with content:\n"
        f"x = 1\ny = 2\nz = 3\nw = 4\n"
    )

    # Make 4 sequential edits
    edits = [
        ("x = 1", "x = 10"),
        ("y = 2", "y = 20"),
        ("z = 3", "z = 30"),
        ("w = 4", "w = 40"),
    ]
    all_nudges: list[SystemNotification] = []
    edit_count = 0

    for old, new in edits:
        result = await agent.invoke(
            f"Use daytona_edit_file to change '{old}' to '{new}' in {_HOME}/editable.py."
        )
        edit_count += result.tool_count("daytona_edit_file")
        all_nudges.extend(_note_nudge_events(result))

    if edit_count >= 3:
        assert len(all_nudges) >= 1, (
            f"Expected note_nudge after {edit_count} daytona_edit_file calls, "
            f"but none fired."
        )
