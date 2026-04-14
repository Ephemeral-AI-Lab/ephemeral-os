"""ActivityTracker — edit/turn counting and auto-checkpoint triggering.

Extracted from TaskCenter. Tracks per-task edit counts, turn counts,
and decides when to fire auto-checkpoints.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable

from team.models import Note

logger = logging.getLogger(__name__)


class ActivityTracker:
    """Tracks edit/turn activity per task and triggers auto-checkpoints."""

    def __init__(
        self,
        team_run_id: str,
        note_posted_cb: Callable[[Note], None] | None = None,
    ) -> None:
        self._activity_counters: dict[str, dict[str, Any]] = {}
        self._checkpoint_inflight: set[str] = set()
        self._checkpoint_snapshots: dict[str, dict[str, int]] = {}
        self._team_run_id = team_run_id
        self._note_posted_cb = note_posted_cb

    def _get_counters(self, task_id: str) -> dict[str, Any]:
        if task_id not in self._activity_counters:
            self._activity_counters[task_id] = {"edits": 0, "turns": 0, "edit_history": []}
        return self._activity_counters[task_id]

    def on_edit(self, task_id: str, file_path: str) -> None:
        c = self._get_counters(task_id)
        c["edits"] += 1
        c["edit_history"].append(file_path)
        c["turns"] = 0

    def on_posthook(self, task_id: str) -> None:
        self._get_counters(task_id)["turns"] = 0

    def tick(self, task_id: str) -> None:
        self._get_counters(task_id)["turns"] += 1

    def on_note_posted(self, note: Note) -> None:
        if note.agent_name in {"system", "checkpoint"}:
            return
        self._checkpoint_inflight.discard(note.task_id)
        if note.task_id not in self._activity_counters:
            return
        c = self._activity_counters[note.task_id]
        snapshot = self._checkpoint_snapshots.pop(note.task_id, None)
        if snapshot is None:
            self._activity_counters[note.task_id] = {"edits": 0, "turns": 0, "edit_history": []}
            return
        c["edits"] = max(0, c["edits"] - snapshot.get("edits", 0))
        c["turns"] = max(0, c["turns"] - snapshot.get("turns", 0))
        covered_history = snapshot.get("edit_history_len", 0)
        if covered_history > 0:
            c["edit_history"] = c["edit_history"][covered_history:]

    def should_checkpoint(self, task_id: str) -> str | None:
        if task_id in self._checkpoint_inflight:
            return None
        c = self._get_counters(task_id)
        if c["edits"] >= 5:
            return "edit"
        if c["turns"] >= 15:
            return "turn"
        return None

    @staticmethod
    def _recent_unique_files(edit_history: list[str], *, limit: int = 10) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for path in reversed(edit_history):
            if path in seen:
                continue
            seen.add(path)
            ordered.append(path)
            if len(ordered) >= limit:
                break
        ordered.reverse()
        return ordered

    async def check(
        self,
        task_id: str,
        graph: dict[str, Any],
        scope_paths: list[str],
        agent_name: str,
        agent_run_id: str | None,
        snapshot: list[dict] | None,
        api_client: Any,
        model: str | None,
        post_note_cb: Callable[[Note], Any],
    ) -> bool:
        trigger = self.should_checkpoint(task_id)
        if trigger is None:
            return False
        self._checkpoint_inflight.add(task_id)
        c = self._get_counters(task_id)
        counter_snapshot = {
            "edits": int(c["edits"]),
            "turns": int(c["turns"]),
            "edit_history_len": len(c["edit_history"]),
        }
        self._checkpoint_snapshots[task_id] = counter_snapshot

        logger.info(
            "[activity_tracker] auto-note trigger=%s task=%s agent=%s edits=%d turns=%d scope=%s",
            trigger,
            task_id,
            agent_name,
            counter_snapshot["edits"],
            counter_snapshot["turns"],
            ",".join(scope_paths) if scope_paths else "-",
        )

        content: str | None = None
        posted = False
        try:
            if api_client and snapshot is not None:
                from external_trigger.tc_note import (
                    EDIT_CHECKPOINT_PROMPT,
                    TURN_CHECKPOINT_PROMPT,
                    run_checkpoint_note,
                )

                prompt = EDIT_CHECKPOINT_PROMPT if trigger == "edit" else TURN_CHECKPOINT_PROMPT
                try:
                    result = await run_checkpoint_note(
                        task_id=task_id,
                        agent_run_id=agent_run_id or task_id,
                        messages=snapshot or [],
                        prompt=prompt,
                        trigger=trigger,
                        max_tokens=500,
                        model=model,
                        api_client=api_client,
                    )
                except Exception:
                    logger.warning(
                        "[activity_tracker] checkpoint note generation failed for task=%s trigger=%s; falling back to factual note",
                        task_id,
                        trigger,
                        exc_info=True,
                    )
                else:
                    if result.note_summary:
                        content = result.note_summary

            if content is None:
                if trigger == "edit":
                    files = ", ".join(
                        self._recent_unique_files(
                            c["edit_history"][: counter_snapshot["edit_history_len"]],
                        ),
                    )
                    content = f"Auto-checkpoint ({counter_snapshot['edits']} edits): {files}"
                else:
                    content = (
                        f"Auto-checkpoint: {counter_snapshot['turns']} turns without progress note"
                    )

            note = Note(
                id=str(uuid.uuid4()),
                task_id=task_id,
                agent_name=f"{agent_name} (auto)",
                content=content,
                timestamp=time.time(),
                scope_paths=scope_paths,
            )
            await post_note_cb(note)
            if self._note_posted_cb is not None:
                self._note_posted_cb(note)
            posted = True
            return True
        finally:
            self._checkpoint_inflight.discard(task_id)
            if not posted:
                self._checkpoint_snapshots.pop(task_id, None)
