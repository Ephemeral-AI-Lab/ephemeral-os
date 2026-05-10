"""Unit tests for the SWE-EVO live e2e AuditRecorder.

Exercises the 4 ORM commit listeners (Mission/Episode/Attempt/Task) plus the
agent_run_id -> task_id mapping listener, the per-Task message recorder
gating by primary role, and the run.json/metrics.json writers.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

import pytest

from live_e2e.audit.bus import AuditEventBus
from live_e2e.audit.events import Event, EventType
from live_e2e.audit.node_id import NodeId
from live_e2e.audit.recorder import AuditRecorder
from live_e2e.stores import (
    TaskCenterStoreBundle,
    create_per_test_task_center_stores,
)
from db.models.agent_run import AgentRunRecord
from db.models.task_center import (
    TaskCenterRequestRecord,
    TaskCenterRunRecord,
    TaskCenterTaskRecord,
)
from sqlalchemy.orm import sessionmaker
from task_center.domain import (
    EpisodeCreationReason,
    MissionStatus,
)


_RUN_ID = "run-abc"
_REQUEST_ID = "req-1"


@pytest.fixture
def stores() -> Iterator[TaskCenterStoreBundle]:
    bundle = create_per_test_task_center_stores()
    try:
        yield bundle
    finally:
        bundle.close()


def _session_factory(bundle: TaskCenterStoreBundle) -> sessionmaker:
    return sessionmaker(
        bind=bundle.engine,
        autoflush=False,
        expire_on_commit=False,
    )


def _seed_run(bundle: TaskCenterStoreBundle, run_id: str = _RUN_ID) -> None:
    sf = _session_factory(bundle)
    with sf() as db:
        now = datetime.now(UTC)
        db.add(
            TaskCenterRequestRecord(
                id=_REQUEST_ID,
                cwd="/testbed",
                sandbox_id="sbx-1",
                request_prompt="goal",
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            TaskCenterRunRecord(
                id=run_id,
                request_id=_REQUEST_ID,
                status="running",
                started_at=now,
            )
        )
        db.commit()


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _make_recorder(
    tmp_path: Path,
    *,
    run_id: str = _RUN_ID,
    bus: AuditEventBus | None = None,
) -> AuditRecorder:
    return AuditRecorder(
        run_dir=tmp_path / "run",
        task_center_run_id=run_id,
        bus=bus,
        scenario_name="correctness_testing",
        instance_id="dask__dask_2023.3.2_2023.4.0",
        sandbox_id="sbx-1",
    )


def test_mission_insert_writes_latest_snapshot(
    tmp_path: Path, stores: TaskCenterStoreBundle
) -> None:
    _seed_run(stores)
    recorder = _make_recorder(tmp_path)
    recorder.start()
    try:
        mission = stores.mission_store.insert(
            task_center_run_id=_RUN_ID,
            requested_by_task_id="entry_task_1",
            goal="solve the problem",
        )
    finally:
        recorder.dispose()

    mission_dir = recorder.run_dir / f"mission_01_{mission.id}"
    snapshot = mission_dir / "mission.json"
    assert snapshot.exists()
    row = _read_json(snapshot)
    assert row["id"] == mission.id
    assert row["status"] == "open"
    assert not (mission_dir / "mission.jsonl").exists()


def test_mission_update_overwrites_latest_snapshot(
    tmp_path: Path, stores: TaskCenterStoreBundle
) -> None:
    _seed_run(stores)
    recorder = _make_recorder(tmp_path)
    recorder.start()
    try:
        mission = stores.mission_store.insert(
            task_center_run_id=_RUN_ID,
            requested_by_task_id="entry_task_1",
            goal="solve the problem",
        )
        stores.mission_store.set_status(
            mission.id,
            status=MissionStatus.SUCCEEDED,
            final_outcome={"ok": True},
            closed_at=datetime.now(UTC),
        )
    finally:
        recorder.dispose()

    snapshot = recorder.run_dir / f"mission_01_{mission.id}" / "mission.json"
    row = _read_json(snapshot)
    assert row["status"] == "succeeded"
    assert row["final_outcome"] == {"ok": True}


def test_episode_and_attempt_listeners(
    tmp_path: Path, stores: TaskCenterStoreBundle
) -> None:
    _seed_run(stores)
    recorder = _make_recorder(tmp_path)
    recorder.start()
    try:
        mission = stores.mission_store.insert(
            task_center_run_id=_RUN_ID,
            requested_by_task_id="entry_task_1",
            goal="solve the problem",
        )
        episode = stores.episode_store.insert(
            mission_id=mission.id,
            sequence_no=1,
            creation_reason=EpisodeCreationReason.INITIAL,
            goal="ep goal",
            attempt_budget=3,
        )
        attempt = stores.attempt_store.insert(
            episode_id=episode.id,
            attempt_sequence_no=1,
        )
    finally:
        recorder.dispose()

    mission_dir = recorder.run_dir / f"mission_01_{mission.id}"
    episode_dir = mission_dir / f"episode_01_{episode.id}"
    attempt_dir = episode_dir / f"attempt_01_{attempt.id}"
    assert _read_json(episode_dir / "episode.json")["id"] == episode.id
    assert _read_json(attempt_dir / "attempt.json")["id"] == attempt.id
    assert not (episode_dir / "episode.jsonl").exists()
    assert not (attempt_dir / "attempt.jsonl").exists()


def _insert_task(
    bundle: TaskCenterStoreBundle,
    *,
    task_id: str,
    role: str,
    run_id: str = _RUN_ID,
    task_center_attempt_id: str | None = None,
    agent_name: str | None = None,
) -> None:
    sf = _session_factory(bundle)
    with sf() as db:
        now = datetime.now(UTC)
        db.add(
            TaskCenterTaskRecord(
                id=task_id,
                task_center_run_id=run_id,
                role=role,
                agent_name=agent_name,
                task_input="input",
                status="pending",
                summaries=[],
                needs=[],
                task_center_attempt_id=task_center_attempt_id,
                context_packet_id=None,
                system_prompt=None,
                user_prompt=None,
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()


def test_task_dir_placement_per_role(
    tmp_path: Path, stores: TaskCenterStoreBundle
) -> None:
    _seed_run(stores)
    recorder = _make_recorder(tmp_path)
    recorder.start()
    try:
        mission = stores.mission_store.insert(
            task_center_run_id=_RUN_ID,
            requested_by_task_id="entry_task_1",
            goal="goal",
        )
        episode = stores.episode_store.insert(
            mission_id=mission.id,
            sequence_no=1,
            creation_reason=EpisodeCreationReason.INITIAL,
            goal="ep",
            attempt_budget=3,
        )
        attempt = stores.attempt_store.insert(
            episode_id=episode.id,
            attempt_sequence_no=1,
        )

        _insert_task(stores, task_id="entry_task_1", role="entry_executor")
        _insert_task(
            stores,
            task_id="task_planner",
            role="planner",
            task_center_attempt_id=attempt.id,
        )
        _insert_task(
            stores,
            task_id="task_executor",
            role="executor",
            task_center_attempt_id=attempt.id,
        )
        _insert_task(
            stores,
            task_id="task_evaluator",
            role="evaluator",
            task_center_attempt_id=attempt.id,
        )
    finally:
        recorder.dispose()

    entry_dir = recorder.run_dir / "entry_executor_entry_task_1"
    assert (entry_dir / "task.json").exists()

    attempt_dir = (
        recorder.run_dir
        / f"mission_01_{mission.id}"
        / f"episode_01_{episode.id}"
        / f"attempt_01_{attempt.id}"
    )
    assert (attempt_dir / "01_planner_task_planner" / "task.json").exists()
    assert (attempt_dir / "02_executor_task_executor" / "task.json").exists()
    assert (attempt_dir / "03_evaluator_task_evaluator" / "task.json").exists()


def test_helper_role_filtered(
    tmp_path: Path, stores: TaskCenterStoreBundle
) -> None:
    _seed_run(stores)
    recorder = _make_recorder(tmp_path)
    recorder.start()
    try:
        _insert_task(stores, task_id="helper_1", role="helper")
    finally:
        recorder.dispose()

    helper_dirs = list(recorder.run_dir.glob("*helper_1*"))
    assert helper_dirs == []


def test_generator_verifier_task_uses_verifier_dir_and_message_recorder(
    tmp_path: Path, stores: TaskCenterStoreBundle
) -> None:
    _seed_run(stores)
    recorder = _make_recorder(tmp_path)
    recorder.start()
    try:
        mission = stores.mission_store.insert(
            task_center_run_id=_RUN_ID,
            requested_by_task_id="entry_task_1",
            goal="goal",
        )
        episode = stores.episode_store.insert(
            mission_id=mission.id,
            sequence_no=1,
            creation_reason=EpisodeCreationReason.INITIAL,
            goal="ep",
            attempt_budget=3,
        )
        attempt = stores.attempt_store.insert(
            episode_id=episode.id,
            attempt_sequence_no=1,
        )
        _insert_task(
            stores,
            task_id="task_verifier",
            role="generator",
            agent_name="verifier",
            task_center_attempt_id=attempt.id,
        )
    finally:
        recorder.dispose()

    verifier_dir = (
        recorder.run_dir
        / f"mission_01_{mission.id}"
        / f"episode_01_{episode.id}"
        / f"attempt_01_{attempt.id}"
        / "01_verifier_task_verifier"
    )
    assert (verifier_dir / "task.json").exists()
    assert recorder.message_recorder_for_task("task_verifier") is not None


def test_sandbox_events_are_mirrored_to_run_jsonl(tmp_path: Path) -> None:
    bus = AuditEventBus()
    recorder = _make_recorder(tmp_path, bus=bus)
    recorder.start()
    try:
        bus.publish(
            Event(
                type=EventType.SANDBOX_OCC_CHANGES_COMMITTED,
                node=NodeId(task_center_run_id=_RUN_ID, tool_name="write_file"),
                payload={"status": "committed", "changed_paths": ["a.txt"]},
                correlation_id="corr-1",
            )
        )
        bus.publish(
            Event(
                type=EventType.EXECUTOR_SUCCESS,
                node=NodeId(task_center_run_id=_RUN_ID, agent_name="executor"),
                payload={"checkpoint": "done"},
            )
        )
    finally:
        recorder.dispose()

    rows = _read_jsonl(recorder.run_dir / "sandbox_events.jsonl")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "sandbox_occ_changes_committed"
    assert rows[0]["node"]["task_center_run_id"] == _RUN_ID
    assert rows[0]["node"]["tool_name"] == "write_file"
    assert rows[0]["payload"] == {
        "status": "committed",
        "changed_paths": ["a.txt"],
    }
    assert rows[0]["correlation_id"] == "corr-1"


def test_dispose_unregisters_listeners(
    tmp_path: Path, stores: TaskCenterStoreBundle
) -> None:
    _seed_run(stores)
    recorder = _make_recorder(tmp_path)
    recorder.start()
    try:
        m1 = stores.mission_store.insert(
            task_center_run_id=_RUN_ID,
            requested_by_task_id="entry_task_1",
            goal="g1",
        )
    finally:
        recorder.dispose()

    m2 = stores.mission_store.insert(
        task_center_run_id=_RUN_ID,
        requested_by_task_id="entry_task_2",
        goal="g2",
    )

    assert (recorder.run_dir / f"mission_01_{m1.id}").exists()
    assert not (recorder.run_dir / f"mission_02_{m2.id}").exists()


def test_run_json_and_metrics_json_written(
    tmp_path: Path, stores: TaskCenterStoreBundle
) -> None:
    _seed_run(stores)
    recorder = _make_recorder(tmp_path)
    recorder.start()
    run_json = recorder.run_dir / "run.json"
    assert run_json.exists()
    started = json.loads(run_json.read_text())
    assert started["status"] == "running"
    assert started["task_center_run_id"] == _RUN_ID

    recorder.dispose()
    finished = json.loads(run_json.read_text())
    assert finished["status"] == "finished"
    assert finished["finished_ts"] is not None

    metrics_json = recorder.run_dir / "metrics.json"
    assert metrics_json.exists()
    payload = json.loads(metrics_json.read_text())
    assert "per_tool" in payload


def test_agent_run_id_to_task_id_mapping(
    tmp_path: Path, stores: TaskCenterStoreBundle
) -> None:
    """The 5th listener populates agent_run_id -> task_id."""
    _seed_run(stores)
    recorder = _make_recorder(tmp_path)
    recorder.start()
    try:
        _insert_task(
            stores,
            task_id="entry_task_1",
            role="entry_executor",
            agent_name="entry_executor_v1",
        )
        agent_run_id = str(uuid.uuid4())
        sf = _session_factory(stores)
        with sf() as db:
            db.add(
                AgentRunRecord(
                    id=agent_run_id,
                    task_id="entry_task_1",
                    agent_name="entry_executor_v1",
                    message_history=None,
                    terminal_tool_result=None,
                    token_count=0,
                    error=None,
                    created_at=datetime.now(UTC),
                )
            )
            db.commit()

        rec = recorder.message_recorder_for_agent_run(agent_run_id)
        assert rec is not None
        assert recorder.message_recorder_for_task("entry_task_1") is rec
    finally:
        recorder.dispose()
