"""Description factory for the ``ask_advisor`` tool."""

from __future__ import annotations

from tools._names import (
    ASK_ADVISOR_TOOL_NAME,
    SUBMIT_EXECUTION_SUCCESS_TOOL_NAME,
    SUBMIT_PLAN_CLOSES_GOAL_TOOL_NAME,
    SUBMIT_VERIFICATION_SUCCESS_TOOL_NAME,
)


def get_ask_advisor_description() -> str:
    return (
        f"Ask the advisor for a blocking, read-only audit of the terminal submission\n"
        f"you're about to make.\n"
        f"\n"
        f"Use this when:\n"
        f"- You're about to call a terminal tool (e.g., `{SUBMIT_EXECUTION_SUCCESS_TOOL_NAME}`,\n"
        f"  `{SUBMIT_VERIFICATION_SUCCESS_TOOL_NAME}`, `{SUBMIT_PLAN_CLOSES_GOAL_TOOL_NAME}`) and you want a\n"
        f"  second pair of eyes on (1) tool selection and (2) whether the work you've\n"
        f"  done actually supports the payload.\n"
        f"- The submission is high-stakes (closes a goal, marks an attempt\n"
        f"  complete).\n"
        f"\n"
        f"Do NOT use for:\n"
        f"- Trivial submissions where the right terminal is unambiguous and the\n"
        f"  work is obvious (e.g., a short summary acknowledging an already-passed\n"
        f"  eval).\n"
        f"- Fixing problems — the advisor only audits and cannot edit. Verifier and\n"
        f"  evaluator agents may apply trivial inline fixes themselves via\n"
        f"  `edit_file`/`write_file` (typo, wrong variable name, single-line\n"
        f"  obvious bug); the advisor's job is to confirm those fixes do not exceed\n"
        f"  that scope before approving a success terminal.\n"
        f"\n"
        f"Capabilities and constraints:\n"
        f"- Read-only. The advisor cannot mutate files.\n"
        f"- The advisor sees your original task and contract, a filtered version\n"
        f"  of your transcript, the terminal-tool catalog (with each terminal's\n"
        f"  review focus), and the submission you're about to make.\n"
        f"- Lenient approve bar: the advisor approves when your tool choice is\n"
        f"  right and your payload is plausibly supported, even if the work isn't\n"
        f"  pristine. It rejects only on real quality problems (wrong terminal,\n"
        f"  stubs, TODOs, unsupported claims).\n"
        f"- You get back `approve` / `reject` plus a summary covering: tool\n"
        f"  selection, payload-vs-work support, residual risks.\n"
        f"\n"
        f"Input shape:\n"
        f"- `tool_name`: the terminal you intend to call.\n"
        f"- `tool_payload`: the exact arguments you'd pass.\n"
        f"\n"
        f"Output shape:\n"
        f"- The advisor's summary text, with verdict in metadata.\n"
        f"\n"
        f"Common pitfalls:\n"
        f"- Calling `{ASK_ADVISOR_TOOL_NAME}` AFTER submitting the terminal — too late. Call\n"
        f"  BEFORE.\n"
        f"- Ignoring a prior `reject` and re-asking with the same payload — a\n"
        f"  caller that ignores prior feedback warrants a sharper second reject."
    )
