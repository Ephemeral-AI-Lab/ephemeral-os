"""Pipeline orchestrator — run and resume pipelines with checkpoint-based retry."""

from __future__ import annotations

import logging
import time
from copy import deepcopy
from typing import TYPE_CHECKING, Any
from collections.abc import Callable, Awaitable
from uuid import uuid4

from pipeline.models import (
    PipelineCheckpoint,
    PipelineRun,
    PipelineRunStatus,
    StepRecord,
    StepStatus,
)
from pipeline.schema import PipelineConfig
from pipeline.step_runner import StepRunner

if TYPE_CHECKING:
    from pipeline.store import PipelineStore
    from server.app_factory import SessionConfig

logger = logging.getLogger(__name__)

OnStepComplete = Callable[[str, Any], Awaitable[None]]


async def run_pipeline(
    config: PipelineConfig,
    goal: str,
    *,
    session_config: SessionConfig,
    store: PipelineStore | None = None,
    on_step_complete: OnStepComplete | None = None,
) -> PipelineRun:
    """Execute a pipeline sequentially, building up context_map."""
    run = PipelineRun(
        run_id=uuid4().hex[:12],
        pipeline_id=config.pipeline_id,
        goal=goal,
    )
    return await _execute(config, run, session_config, store, on_step_complete)


async def run_pipeline_with_run(
    config: PipelineConfig,
    run: PipelineRun,
    *,
    session_config: SessionConfig,
    store: PipelineStore | None = None,
    on_step_complete: OnStepComplete | None = None,
) -> PipelineRun:
    """Execute a pipeline with a pre-created PipelineRun (for API use)."""
    return await _execute(config, run, session_config, store, on_step_complete, skip_create=True)


async def resume_pipeline(
    config: PipelineConfig,
    run_id: str,
    checkpoint_id: str,
    *,
    context_map_patches: dict[str, dict[str, Any]] | None = None,
    session_config: SessionConfig,
    store: PipelineStore,
    on_step_complete: OnStepComplete | None = None,
) -> PipelineRun:
    """Resume a pipeline from a specific checkpoint.

    Users can:
    - Resume from ANY checkpoint, not just the latest
    - Patch context_map before resuming (fix bad step output)
    """
    checkpoint = await store.get_checkpoint(run_id, checkpoint_id)
    if checkpoint is None:
        raise ValueError(f"Checkpoint {checkpoint_id!r} not found for run {run_id!r}")

    run = await store.get_run(run_id)
    if run is None:
        raise ValueError(f"Run {run_id!r} not found")

    # Restore state from checkpoint
    run.context_map = deepcopy(checkpoint.context_map_snapshot)
    run.completed_steps = list(checkpoint.completed_steps)
    run.resumed_from_checkpoint = checkpoint_id
    run.attempt_number += 1
    run.error = None

    # Apply patches — user can fix bad output before retrying
    if context_map_patches:
        for step_name, patch in context_map_patches.items():
            if step_name in run.context_map:
                run.context_map[step_name].update(patch)

    return await _execute(config, run, session_config, store, on_step_complete)


async def _execute(
    config: PipelineConfig,
    run: PipelineRun,
    session_config: SessionConfig,
    store: PipelineStore | None,
    on_step_complete: OnStepComplete | None,
    skip_create: bool = False,
) -> PipelineRun:
    """Core execution loop shared by run and resume."""
    run.status = PipelineRunStatus.RUNNING
    run.started_at = run.started_at or time.time()

    if store and not skip_create:
        await store.create_run(run)
    elif store and skip_create:
        await store.update_run(run)

    for step_config in config.steps:
        # Skip disabled steps
        if not step_config.enabled:
            run.step_records.append(
                StepRecord(
                    name=step_config.name,
                    agent=step_config.agent,
                    status=StepStatus.SKIPPED,
                )
            )
            continue

        # Skip already-completed steps (checkpoint recovery)
        if step_config.name in run.completed_steps:
            continue

        run.current_step = step_config.name
        if store:
            await store.update_run(run)

        step_runner = StepRunner(step_config, config, run.context_map, session_config)

        try:
            result = await step_runner.run(run.goal)
        except Exception as exc:
            logger.error("Step %r failed: %s", step_config.name, exc, exc_info=True)
            run.step_records.append(
                StepRecord(
                    name=step_config.name,
                    agent=step_config.agent,
                    status=StepStatus.FAILED,
                    error=str(exc),
                    finished_at=time.time(),
                )
            )
            run.status = PipelineRunStatus.FAILED
            run.error = f"Step '{step_config.name}' failed: {exc}"
            run.finished_at = time.time()
            if store:
                await store.update_run(run)
            return run

        # Store output in context map
        run.context_map[step_config.name] = result.validated_output
        run.completed_steps.append(step_config.name)
        run.step_records.append(result.record)

        if on_step_complete:
            await on_step_complete(step_config.name, result)

        # Create checkpoint if enabled
        if step_config.checkpoint:
            cp = PipelineCheckpoint(
                checkpoint_id=f"{run.run_id}-cp-{len(run.checkpoints)}",
                run_id=run.run_id,
                step_name=step_config.name,
                step_index=config.steps.index(step_config),
                context_map_snapshot=deepcopy(run.context_map),
                completed_steps=list(run.completed_steps),
                step_records=list(run.step_records),
                created_at=time.time(),
            )
            run.checkpoints.append(cp)
            if store:
                await store.save_checkpoint(run, cp)

    run.status = PipelineRunStatus.COMPLETED
    run.current_step = None
    run.finished_at = time.time()
    if store:
        await store.update_run(run)
    return run
