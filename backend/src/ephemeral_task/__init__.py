"""EphemeralTask module — single-shot LLM calls for Conductor and TaskCenter active mode."""

from ephemeral_task.tc_note import (
    CHECKPOINT_SYSTEM_PROMPT,
    EDIT_CHECKPOINT_PROMPT,
    TURN_CHECKPOINT_PROMPT,
    NoteSummary,
    run_checkpoint,
)
from ephemeral_task.core import EphemeralTaskResult, Snapshot, call_llm
from ephemeral_task.pause_assessment import PauseVerdict, assess_pause

__all__ = [
    "CHECKPOINT_SYSTEM_PROMPT",
    "EDIT_CHECKPOINT_PROMPT",
    "TURN_CHECKPOINT_PROMPT",
    "EphemeralTaskResult",
    "NoteSummary",
    "Snapshot",
    "PauseVerdict",
    "assess_pause",
    "call_llm",
    "run_checkpoint",
]
