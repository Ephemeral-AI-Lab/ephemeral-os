"""Built-in tool for querying background task status."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field, model_validator

from tools.core.base import BaseTool, ToolExecutionContext, ToolResult

from ._common import apply_last_n_lines, task_id_field, validate_task_id


class CheckBackgroundProgressInput(BaseModel):
    """Input for check_background_progress tool."""
    task_id: str = task_id_field()
    last_n_lines: int = Field(
        default=20,
        ge=1,
        description="Number of output lines to include for completed tasks. Use to limit verbose output.",
    )

    @model_validator(mode="after")
    def _validate_task_id(self) -> CheckBackgroundProgressInput:
        err = validate_task_id(self.task_id)
        if err:
            raise ValueError(err)
        return self


class CheckBackgroundProgressTool(BaseTool):
    """Query the status of background tasks (non-blocking)."""

    name: str = "check_background_progress"
    description: str = (
        "Check the current status of background tasks (non-blocking). Returns an instant snapshot "
        "of task status. Pass an exact task_id like \"bg_1\" or \"all\" to target every task. "
        "For blocking wait, use wait_for_background_task instead."
    )
    input_model: type[BaseModel] = CheckBackgroundProgressInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, CheckBackgroundProgressInput)
        # task_id is enforced non-empty by the model_validator on the input
        # schema; pydantic will raise before reaching here if it's missing.

        manager = context.metadata.get("background_task_manager")
        if manager is None:
            return ToolResult(
                output=(
                    "ERROR: background task manager is not available in this "
                    "context — no background tasks can be queried."
                ),
                is_error=True,
            )

        target_id = None if arguments.task_id == "all" else arguments.task_id
        status = manager.get_status(task_id=target_id)

        if not status:
            if target_id is not None:
                return ToolResult(
                    output=f"No background task found with ID: {target_id}",
                    is_error=True,
                )
            return ToolResult(output="No background tasks.", is_error=False)

        apply_last_n_lines(status, arguments.last_n_lines)
        return ToolResult(output=json.dumps(status, indent=2), is_error=False)

    def is_read_only(self, arguments: BaseModel) -> bool:
        return True
