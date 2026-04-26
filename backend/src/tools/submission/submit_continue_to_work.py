"""Terminal tool (evaluator-only): expand into continuation work."""

from __future__ import annotations

from pydantic import AliasChoices, BaseModel, Field

from tools.core.base import ToolExecutionContext, ToolResult
from tools.core.decorator import tool
from tools.submission._models import SubmissionOutput


class ContinueToWorkInput(BaseModel):
    task_input: str = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("task_input", "summary"),
        description=(
            "Input for the continuation executor: which acceptance_criteria are "
            "not yet satisfied, what gap remains, and what to focus on. The "
            "legacy 'summary' key is accepted."
        ),
    )


@tool(
    name="submit_continue_to_work",
    description=(
        "Terminal (evaluator-only): expand the evaluator into continuation work. "
        "TaskCenter spawns a continuation executor under the evaluator; the "
        "original executor remains awaiting until the continuation chain closes."
    ),
    input_model=ContinueToWorkInput,
    output_model=SubmissionOutput,
    is_terminal_tool=True,
)
async def submit_continue_to_work(
    task_input: str,
    *,
    context: ToolExecutionContext,
) -> ToolResult:
    role = context.metadata.get("role")
    if role != "evaluator":
        return ToolResult(
            output=(
                "submit_continue_to_work is evaluator-only "
                f"(current role={role!r}); executors must use submit_task_completion "
                "or one of the handoff tools instead."
            ),
            is_error=True,
        )
    tc = context.metadata.get("task_center")
    task_id = context.metadata.get("task_id")
    if tc is None or task_id is None:
        return ToolResult(
            output="submit_continue_to_work: missing task_center or task_id in metadata",
            is_error=True,
        )
    tc.submit_continue_to_work(task_id, task_input)
    return ToolResult(output=SubmissionOutput(status="accepted").model_dump_json())
