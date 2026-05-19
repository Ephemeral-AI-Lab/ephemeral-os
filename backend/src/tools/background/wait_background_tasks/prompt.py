"""Description for the wait_background_tasks tool."""

from __future__ import annotations

from tools._names import (
    CHECK_BACKGROUND_TASK_RESULT_TOOL_NAME,
    RUN_SUBAGENT_TOOL_NAME,
    SHELL_TOOL_NAME,
)


def get_wait_background_tasks_description() -> str:
    return (
        "Block until every running background task settles, or `timeout` expires.\n"
        "\n"
        "Use this when:\n"
        f"- You've launched parallel work (multiple `{RUN_SUBAGENT_TOOL_NAME}` /\n"
        f"  backgrounded `{SHELL_TOOL_NAME}`) and your next planning step depends on all of it\n"
        "  finishing.\n"
        "- You want a synchronization barrier before a verification/submission\n"
        "  step.\n"
        "\n"
        "Do NOT use for:\n"
        f"- \"Just give me one task's result\" — call `{CHECK_BACKGROUND_TASK_RESULT_TOOL_NAME}`\n"
        "  on the specific id; that's cheaper than blocking on everything.\n"
        "- Indefinite waits — `timeout` is bounded to [1, 300] seconds; schema\n"
        "  validation rejects anything outside.\n"
        "\n"
        "Capabilities and constraints:\n"
        "- You get one compact entry per task: `task_id`, `status`\n"
        "  (`running`|`finished`|`failed`), and `tool_command`.\n"
        "- This call does NOT return result bodies — call\n"
        f"  `{CHECK_BACKGROUND_TASK_RESULT_TOOL_NAME}` per task to fetch each.\n"
        "- Newly completed tasks are marked delivered so the engine does not\n"
        "  double-emit `[BACKGROUND COMPLETED]` messages.\n"
        "- The call returns immediately with a \"no tasks\" snapshot when nothing\n"
        "  is running.\n"
        "\n"
        "Output shape:\n"
        "- Rendered snapshot text (`wait_completed` | `wait_timed_out` |\n"
        "  `wait_no_tasks`) listing each task's status.\n"
        "- Metadata mirrors the snapshot for tool-call consumers.\n"
        "\n"
        "Common pitfalls:\n"
        "- Treating \"timed out\" as failure: it isn't. Tasks are still running;\n"
        "  either call again with more timeout, or cancel them explicitly.\n"
        f"- Calling `wait` when you only have one task —\n"
        f"  `{CHECK_BACKGROUND_TASK_RESULT_TOOL_NAME}` on its id is more direct."
    )
