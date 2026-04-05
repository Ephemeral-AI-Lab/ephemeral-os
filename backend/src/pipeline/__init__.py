"""Generic pipeline engine for EphemeralOS.

A pipeline is an ordered sequence of steps. Each step runs an agent (work step)
followed by an optional posthook agent (another agent run that formats/submits
the output). Steps share an incremental context map.
"""

from pipeline.schema import InputDepConfig, PipelineConfig, PipelineStepConfig
from pipeline.models import (
    PipelineCheckpoint,
    PipelineRun,
    PipelineRunStatus,
    StepRecord,
    StepStatus,
)

__all__ = [
    "InputDepConfig",
    "PipelineCheckpoint",
    "PipelineConfig",
    "PipelineRun",
    "PipelineRunStatus",
    "PipelineStepConfig",
    "StepRecord",
    "StepStatus",
]
