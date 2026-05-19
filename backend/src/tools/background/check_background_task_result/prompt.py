"""Description for the check_background_task_result tool."""

from __future__ import annotations

from tools._names import (
    CANCEL_BACKGROUND_TASK_TOOL_NAME,
    RUN_SUBAGENT_TOOL_NAME,
    SHELL_TOOL_NAME,
    WAIT_BACKGROUND_TASKS_TOOL_NAME,
)


def get_check_background_task_result_description() -> str:
    return (
        "Fetch the current result of one background task by id.\n"
        "\n"
        "Use this when:\n"
        "- You need to peek at a running subagent's progress (you get the last few\n"
        "  messages).\n"
        "- You need to retrieve the terminal output of a task that has finished.\n"
        "- A `[BACKGROUND COMPLETED]` notification arrived and you want the full\n"
        "  result.\n"
        "\n"
        "Do NOT use for:\n"
        f"- Polling — once you call this on a finished task, the engine treats it\n"
        "  as delivered and won't re-send the completion notification. Call once\n"
        "  per task, when you actually need the result.\n"
        f"- \"Are there any tasks?\" — use `{WAIT_BACKGROUND_TASKS_TOOL_NAME}` with a short\n"
        f"  timeout, or rely on the listing exposed by `{CANCEL_BACKGROUND_TASK_TOOL_NAME}`\n"
        "  errors.\n"
        "\n"
        "Capabilities and constraints:\n"
        f"- For `{RUN_SUBAGENT_TOOL_NAME}`: you get the terminal-tool output if finished, or\n"
        "  the last 5 messages otherwise (prefixed with `[cancelled]` when you\n"
        "  cancelled the task).\n"
        f"- For other backgroundable tools (e.g., `{SHELL_TOOL_NAME}`): you get the full\n"
        "  output verbatim once finished, a progress snapshot while running.\n"
        "- This call marks completed tasks as delivered as a side effect — see\n"
        "  above.\n"
        "\n"
        "Output shape (JSON):\n"
        "- `id`: the task id.\n"
        "- `status`: \"running\" | \"finished\" | \"failed\" | \"cancelled\".\n"
        "- `tool_command`: the rendered original tool invocation, for context.\n"
        "- `result`: terminal output or progress peek.\n"
        "\n"
        "Common pitfalls:\n"
        "- Calling this preemptively on a still-running subagent — you get a\n"
        f"  peek, not a result. Either wait for the notification or call\n"
        f"  `{WAIT_BACKGROUND_TASKS_TOOL_NAME}` if you need to block."
    )
