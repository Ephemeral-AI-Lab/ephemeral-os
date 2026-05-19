"""Description factory for the ``ask_resolver`` tool."""

from __future__ import annotations

from tools._names import (
    ASK_ADVISOR_TOOL_NAME,
    EDIT_FILE_TOOL_NAME,
    SUBMIT_RESOLVER_RESULT_TOOL_NAME,
    WRITE_FILE_TOOL_NAME,
)


def get_ask_resolver_description() -> str:
    return (
        f"Ask the resolver to address unresolved verifier or evaluator issues. The\n"
        f"resolver may edit files and submits via `{SUBMIT_RESOLVER_RESULT_TOOL_NAME}`.\n"
        f"\n"
        f"Use this when:\n"
        f"- A verifier or evaluator has surfaced concrete issues (failing checks,\n"
        f"  missing artifacts, wrong outputs) and you want a focused agent to fix\n"
        f"  them before you re-submit.\n"
        f"- The issues are well-described enough to act on without a fresh planning\n"
        f"  pass.\n"
        f"\n"
        f"Do NOT use for:\n"
        f"- Read-only review — use `{ASK_ADVISOR_TOOL_NAME}`. The resolver edits files; the\n"
        f"  advisor does not.\n"
        f"- Open-ended replanning — the resolver works from your contract,\n"
        f"  transcript, and issue list; it doesn't re-derive the plan.\n"
        f"\n"
        f"Capabilities and constraints:\n"
        f"- The resolver has edit access (`{EDIT_FILE_TOOL_NAME}`, `{WRITE_FILE_TOOL_NAME}`, etc.) inside\n"
        f"  your workspace.\n"
        f"- It sees your original task and contract, a filtered version of your\n"
        f"  transcript, your `issues_to_resolve`, and the optional\n"
        f"  `issue_context`.\n"
        f"- It terminates via\n"
        f"  `{SUBMIT_RESOLVER_RESULT_TOOL_NAME}(verdict, summary, changed_files,\n"
        f"  remaining_issues)`.\n"
        f"\n"
        f"Input shape:\n"
        f"- `issues_to_resolve`: bullet list of concrete issues (≥ 1 required).\n"
        f"- `issue_context`: optional free-form additional context.\n"
        f"\n"
        f"Output shape:\n"
        f"- The resolver's summary text, with resolution status in metadata.\n"
        f"\n"
        f"Common pitfalls:\n"
        f"- Issues phrased vaguely (\"make it better\") — the resolver needs\n"
        f"  falsifiable problems (\"`test_foo` fails with `ZeroDivisionError` on\n"
        f"  line 42\").\n"
        f"- Treating an unresolved result as failure — it's a signal that some\n"
        f"  issues remain. Check `remaining_issues` and decide whether to re-ask\n"
        f"  or escalate."
    )
