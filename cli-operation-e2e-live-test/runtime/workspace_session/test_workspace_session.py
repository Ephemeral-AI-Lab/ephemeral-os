"""create_workspace_session finalize-policy live coverage."""

import pytest

from runtime.workspace_session.helpers import (
    assert_error,
    assert_exec_workspace_not_found,
    assert_file_workspace_not_found,
    assert_ok,
    assert_output,
    assert_teardown_clean,
    create_with_finalize_policy_flag,
    destroy_session,
    exec_bare,
    exec_in,
    file_read,
    file_write,
    interrupt,
    record_case,
    runtime_help,
    snapshot,
    wait_command,
    workspace_entry,
    workspace_tracker,
)


@pytest.mark.smoke
def test_WS_01_create_response_contract(sandbox, workspace_tracker):
    with record_case("WS-01") as rec:
        shared = workspace_tracker.create_session()
        shared_id = shared["workspace_session_id"]
        assert shared["network_profile"] == "shared", shared
        assert shared["finalize_policy"] == "no_op", shared

        snap = snapshot(sandbox)
        rec.add_artifact("snapshot-shared.json", snap)
        entry = workspace_entry(snap, shared_id)
        assert entry is not None, snap
        assert entry["finalize_policy"] == "no_op", entry
        assert entry["network_profile"] == "shared", entry

        isolated = workspace_tracker.create_session(network_profile="isolated")
        isolated_id = isolated["workspace_session_id"]
        assert isolated["network_profile"] == "isolated", isolated
        assert isolated["finalize_policy"] == "no_op", isolated

        assert_ok(workspace_tracker.destroy(shared_id))
        assert_ok(workspace_tracker.destroy(isolated_id))
        rec.axis("correctness", True, "create responses and snapshot contract matched")
        assert_teardown_clean(rec, sandbox, workspace_tracker)


@pytest.mark.smoke
def test_WS_02_no_op_session_survives_command_completion(sandbox, workspace_tracker):
    with record_case("WS-02") as rec:
        session = workspace_tracker.create_session()["workspace_session_id"]
        first = assert_output(exec_in(sandbox, session, "echo hi"), "hi")
        assert first["workspace_session_id"] == session, first

        second = assert_ok(exec_in(sandbox, session, "echo there > /workspace/ws02.txt"))
        assert second["workspace_session_id"] == session, second
        read = assert_ok(file_read(sandbox, "ws02.txt", workspace_session_id=session))
        assert read["content"] == "there", read

        assert_ok(workspace_tracker.destroy(session))
        rec.axis("correctness", True, "no_op session remained usable after command completion")
        assert_teardown_clean(rec, sandbox, workspace_tracker)


@pytest.mark.smoke
def test_WS_03_destroy_refuses_while_command_runs(sandbox, workspace_tracker):
    with record_case("WS-03") as rec:
        session = workspace_tracker.create_session()["workspace_session_id"]
        running = assert_ok(exec_in(sandbox, session, "sleep 30", yield_time_ms=0))
        command_id = workspace_tracker.track_command(running["command_session_id"])
        assert running["workspace_session_id"] == session, running
        assert running["status"] == "running", running

        refused = destroy_session(sandbox, session, grace_s=1)
        error = assert_error(refused, "operation_failed", "active command sessions")
        assert error.get("details", {}).get("active_command_session_ids") == [command_id], error

        cancelled = assert_ok(interrupt(sandbox, command_id))
        workspace_tracker.untrack_command(command_id)
        assert cancelled["status"] == "cancelled", cancelled
        assert cancelled["workspace_session_id"] == session, cancelled

        assert_ok(workspace_tracker.destroy(session))
        rec.axis("correctness", True, "destroy refused active command and succeeded after Ctrl-C")
        assert_teardown_clean(rec, sandbox, workspace_tracker)


@pytest.mark.medium
def test_WS_04_destroy_discards_and_sync_op_loses_cleanly(sandbox, workspace_tracker):
    with record_case("WS-04") as rec:
        session = workspace_tracker.create_session()["workspace_session_id"]
        assert_ok(file_write(sandbox, "ws04.txt", "discarded\n", workspace_session_id=session))

        assert_ok(workspace_tracker.destroy(session))
        stale_read = file_read(sandbox, ".gitkeep", workspace_session_id=session)
        assert_file_workspace_not_found(stale_read, session)

        published = assert_output(
            exec_bare(sandbox, "cat /workspace/ws04.txt 2>/dev/null || echo absent"),
            "absent",
        )
        implicit = workspace_tracker.track_workspace(published["workspace_session_id"])
        workspace_tracker.wait_finalized(implicit)

        rec.axis("correctness", True, "explicit destroy discarded changes and stale read lost cleanly")
        assert_teardown_clean(rec, sandbox, workspace_tracker)


@pytest.mark.medium
def test_WS_05_no_finalize_policy_flag_exists(sandbox, workspace_tracker):
    with record_case("WS-05") as rec:
        bad = create_with_finalize_policy_flag(sandbox)
        assert_error(bad, "invalid_request", "unknown flag for create_workspace_session: --finalize-policy")

        help_result = runtime_help("create_workspace_session")
        rec.add_artifact("create-help.json", help_result)
        assert help_result["returncode"] == 0, help_result
        assert "--network-profile" in help_result["stdout"], help_result
        assert "--finalize-policy" not in help_result["stdout"], help_result

        rec.axis("correctness", True, "bad flag rejected and runtime help omits finalize policy")
        assert_teardown_clean(rec, sandbox, workspace_tracker)


@pytest.mark.medium
def test_WS_06_destroyed_id_stays_dead(sandbox, workspace_tracker):
    with record_case("WS-06") as rec:
        session = workspace_tracker.create_session()["workspace_session_id"]
        assert_ok(workspace_tracker.destroy(session))

        assert_exec_workspace_not_found(exec_in(sandbox, session, "true"), session)
        assert_file_workspace_not_found(file_read(sandbox, ".gitkeep", workspace_session_id=session), session)
        assert_exec_workspace_not_found(destroy_session(sandbox, session, grace_s=1), session)

        fresh = workspace_tracker.create_session()["workspace_session_id"]
        assert fresh != session
        assert_ok(workspace_tracker.destroy(fresh))

        rec.axis("correctness", True, "stale id rejected and daemon accepted a fresh session")
        assert_teardown_clean(rec, sandbox, workspace_tracker)
