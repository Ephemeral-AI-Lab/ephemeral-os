"""Pipeline REST API — CRUD, execution, checkpoints, and resume."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import Any, Callable, TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pipeline.db.store import DbPipelineStore
from pipeline.models import PipelineRunStatus
from pipeline.schema import PipelineConfig

if TYPE_CHECKING:
    from server.app_factory import SessionState

logger = logging.getLogger(__name__)

# Active runs tracked in memory (run_id -> asyncio.Task)
_active_runs: dict[str, asyncio.Task[Any]] = {}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RunPipelineRequest(BaseModel):
    goal: str


class ResumePipelineRequest(BaseModel):
    checkpoint_id: str
    context_map_patches: dict[str, dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_pipeline_router(
    get_pipeline_store: Callable[[], DbPipelineStore | None],
    get_session: Callable[[], "SessionState"],
) -> APIRouter:
    router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])

    def _require_store() -> DbPipelineStore:
        store = get_pipeline_store()
        if store is None or not store.is_available:
            raise HTTPException(
                status_code=503,
                detail="Pipeline store not available (database not configured)",
            )
        return store

    # -- Pipeline CRUD --------------------------------------------------------

    @router.get("")
    @router.get("/")
    async def list_pipelines() -> list[dict[str, Any]]:
        store = _require_store()
        configs = store.list_pipelines()
        return [c.model_dump(mode="json") for c in configs]

    @router.post("")
    @router.post("/")
    async def create_pipeline(config: PipelineConfig) -> dict[str, Any]:
        store = _require_store()
        store.save_pipeline(config)
        return config.model_dump(mode="json")

    @router.get("/templates")
    async def list_templates() -> list[dict[str, Any]]:
        from pipeline.templates import load_bundled_templates

        return [t.model_dump(mode="json") for t in load_bundled_templates()]

    @router.get("/{pipeline_id}")
    async def get_pipeline(pipeline_id: str) -> dict[str, Any]:
        store = _require_store()
        config = store.get_pipeline(pipeline_id)
        if config is None:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        return config.model_dump(mode="json")

    @router.put("/{pipeline_id}")
    async def update_pipeline(
        pipeline_id: str, config: PipelineConfig
    ) -> dict[str, Any]:
        store = _require_store()
        if config.pipeline_id != pipeline_id:
            raise HTTPException(
                status_code=400, detail="pipeline_id in body must match URL"
            )
        store.save_pipeline(config)
        return config.model_dump(mode="json")

    @router.delete("/{pipeline_id}")
    async def delete_pipeline(pipeline_id: str) -> dict[str, str]:
        store = _require_store()
        if not store.delete_pipeline(pipeline_id):
            raise HTTPException(status_code=404, detail="Pipeline not found")
        return {"status": "deleted"}

    # -- Pipeline execution ---------------------------------------------------

    @router.post("/{pipeline_id}/run")
    async def start_pipeline_run(
        pipeline_id: str, request: RunPipelineRequest
    ) -> dict[str, Any]:
        store = _require_store()
        config = store.get_pipeline(pipeline_id)
        if config is None:
            raise HTTPException(status_code=404, detail="Pipeline not found")

        session = get_session()
        if session.config is None:
            raise HTTPException(status_code=503, detail="Session not initialized")

        from pipeline.runner import run_pipeline

        async def _run() -> None:
            try:
                await run_pipeline(
                    config,
                    request.goal,
                    session_config=session.config,
                    store=store,
                )
            except Exception:
                logger.exception("Pipeline run failed for %s", pipeline_id)

        task = asyncio.create_task(_run())
        # Generate a run_id preview (the actual run_id is created inside run_pipeline)
        # We return pipeline_id so the client can poll via list_runs
        _active_runs[pipeline_id] = task
        task.add_done_callback(lambda _: _active_runs.pop(pipeline_id, None))

        return {"status": "started", "pipeline_id": pipeline_id}

    # -- Run queries ----------------------------------------------------------

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        store = _require_store()
        run = await store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return asdict(run)

    @router.get("/{pipeline_id}/runs")
    async def list_runs(pipeline_id: str) -> list[dict[str, Any]]:
        store = _require_store()
        runs = await store.list_runs(pipeline_id=pipeline_id)
        return [asdict(r) for r in runs]

    # -- Checkpoints & resume -------------------------------------------------

    @router.get("/runs/{run_id}/checkpoints")
    async def list_checkpoints(run_id: str) -> list[dict[str, Any]]:
        store = _require_store()
        checkpoints = await store.list_checkpoints(run_id)
        return [
            {
                "checkpoint_id": cp.checkpoint_id,
                "step_name": cp.step_name,
                "step_index": cp.step_index,
                "completed_steps": cp.completed_steps,
                "created_at": cp.created_at,
            }
            for cp in checkpoints
        ]

    @router.post("/runs/{run_id}/resume")
    async def resume_run(
        run_id: str, request: ResumePipelineRequest
    ) -> dict[str, Any]:
        store = _require_store()
        run = await store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

        config = store.get_pipeline(run.pipeline_id)
        if config is None:
            raise HTTPException(status_code=404, detail="Pipeline config not found")

        session = get_session()
        if session.config is None:
            raise HTTPException(status_code=503, detail="Session not initialized")

        from pipeline.runner import resume_pipeline

        async def _resume() -> None:
            try:
                await resume_pipeline(
                    config,
                    run_id,
                    request.checkpoint_id,
                    context_map_patches=request.context_map_patches,
                    session_config=session.config,
                    store=store,
                )
            except Exception:
                logger.exception("Pipeline resume failed for run %s", run_id)

        task = asyncio.create_task(_resume())
        _active_runs[run_id] = task
        task.add_done_callback(lambda _: _active_runs.pop(run_id, None))

        return {
            "status": "resuming",
            "run_id": run_id,
            "checkpoint_id": request.checkpoint_id,
        }

    @router.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict[str, str]:
        task = _active_runs.get(run_id)
        if task is None:
            raise HTTPException(status_code=404, detail="No active run found")
        task.cancel()
        return {"status": "cancelled"}

    return router
