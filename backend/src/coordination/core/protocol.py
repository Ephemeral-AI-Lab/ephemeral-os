"""Formal protocol for coordination store backends.

Defines the contract that any store (DB-backed or in-memory) must implement.
All coordination engine modules depend on this interface.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class CoordinationStoreProtocol(Protocol):
    """Contract for coordination store backends."""

    @property
    def is_available(self) -> bool: ...

    # ------------------------------------------------------------------
    # Task status transitions (CAS)
    # ------------------------------------------------------------------

    def compare_and_update_task_status(
        self,
        run_id: str,
        task_id: str,
        expected_status: str = "",
        new_status: str = "",
        **fields: Any,
    ) -> bool:
        """Atomic CAS on task status. Returns True if transition succeeded."""
        ...

    def finalize_task_result(
        self,
        run_id: str,
        task_id: str,
        *,
        new_status: str = "",
        artifact: dict | None = None,
        summary: str | None = None,
        result_preview: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Atomic finalization of a task result with artifact persistence."""
        ...

    # ------------------------------------------------------------------
    # Task queries
    # ------------------------------------------------------------------

    def get_task(self, run_id: str, task_id: str) -> dict | None:
        """Return a single task record or None."""
        ...

    def list_tasks(self, run_id: str) -> list[dict]:
        """Return all task records for a run."""
        ...

    def count_in_flight_tasks(self, run_id: str) -> int:
        """Return count of queued + running tasks for a run."""
        ...

    def get_task_status_summary(self, run_id: str) -> dict[str, int] | None:
        """Return {status: count} summary or None."""
        ...

    # ------------------------------------------------------------------
    # Task mutations
    # ------------------------------------------------------------------

    def create_tasks(self, run_id: str, tasks: Any) -> None:
        """Persist new tasks for a run."""
        ...

    def reset_incomplete_task_to_pending(self, run_id: str, task_id: str) -> bool:
        """Reset a queued/running task back to pending."""
        ...

    # ------------------------------------------------------------------
    # Artifact persistence
    # ------------------------------------------------------------------

    def save_artifact(self, run_id: str, task_id: str, artifact: Any) -> None:
        """Persist a task artifact."""
        ...

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def get_coordination_plan(self, run_id: str) -> dict | None:
        """Return a run record or None."""
        ...

    def update_run_status(self, run_id: str, *, status: str) -> None:
        """Update the run's top-level status."""
        ...

    def update_run_counters(self, run_id: str, **deltas: int) -> None:
        """Increment run-level task counters."""
        ...

    def create_coordination_plan(
        self,
        run_id: str,
        *,
        goal: str,
        status: str = "planning",
        total_tasks: int = 0,
        metadata_json: str | None = None,
    ) -> dict[str, Any]:
        """Create a new coordination run record."""
        ...

    def delete_coordination_plan(self, run_id: str) -> bool:
        """Delete a coordination run and its associated tasks."""
        ...

    # ------------------------------------------------------------------
    # Run metadata
    # ------------------------------------------------------------------

    def get_run_metadata(self, run_id: str) -> dict:
        """Return run metadata dict (never None)."""
        ...

    def update_run_metadata(self, run_id: str, *, metadata: dict) -> bool:
        """Merge metadata updates into the run."""
        ...

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def set_cancellation_requested(self, run_id: str) -> None:
        """Mark a run as cancellation-requested."""
        ...
