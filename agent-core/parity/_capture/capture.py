"""Capture Phase-0 parity fixtures from the live Python backend.

One-shot reproducer (committed for provenance; the committed JSON/SQL/JSONL under
`parity/{schemas,sqlite,prompt_report}/` are the real artifacts). Run from the
repo root:

    uv run python agent-core/parity/_capture/capture.py

Captures:
  * schemas/*.schema.json      — Pydantic `model_json_schema()` for Message +
                                 content blocks + the default tool output model.
  * prompt_report/session_golden.jsonl — faithful PromptReportRecorder output
                                 (system_prompt is a separate request field;
                                 messages are role user/assistant only).
  * prompt_report/initial_messages_anomaly.json — the real role=system record
                                 produced by engine.agent.lifecycle
                                 ._initial_message_records (the bug anchor §4
                                 fixes; preserved here as frozen Python truth).
  * sqlite/schema.sql          — canonical sqlite_master for the clean
                                 (create_all) schema of the seven target tables.

SSE byte-stream fixtures are authored as static files under parity/sse/ (they are
raw provider wire format, not Python-derived); see capture is not responsible for
them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "backend" / "src"
OUT = Path(__file__).resolve().parents[1]  # agent-core/parity
sys.path.insert(0, str(SRC))


def write_json(rel: str, obj: object) -> None:
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
    print("wrote", p.relative_to(OUT))


def write_jsonl(rel: str, rows: list[dict]) -> None:
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    # One compact JSON object per line; sorted keys for byte-stability.
    body = "\n".join(json.dumps(r, sort_keys=True) for r in rows)
    p.write_text(body + "\n")
    print("wrote", p.relative_to(OUT))


def dump_schemas() -> None:
    from message.message import (
        Message,
        TextBlock,
        ToolUseBlock,
        ThinkingBlock,
        ToolResultBlock,
        SystemNotificationBlock,
    )
    from tools._framework.core.results import TextToolOutput
    from agents.definition.model import AgentDefinition

    models = {
        "message": Message,
        "text_block": TextBlock,
        "tool_use_block": ToolUseBlock,
        "thinking_block": ThinkingBlock,
        "tool_result_block": ToolResultBlock,
        "system_notification_block": SystemNotificationBlock,
        "text_tool_output": TextToolOutput,
        # eos-agent-def golden: the schemars schema for AgentDefinition is
        # compared against this on field names + enum values (AC-eos-agent-def-09).
        "agent_definition": AgentDefinition,
    }
    for name, model in models.items():
        write_json(f"schemas/{name}.schema.json", model.model_json_schema())

    # Best-effort ToolSpec envelope. The default registry binds tools per-agent,
    # so it may be empty here; richer ToolSpec goldens are an eos-tools /
    # eos-llm-client phase obligation (documented in parity/README.md).
    try:
        from tools import create_default_tool_registry

        specs = create_default_tool_registry().to_api_schema()
        if specs:
            write_json("schemas/tool_specs.schema.json", specs)
        else:
            print("SKIP tool_specs: default registry is empty (tools bind per-agent)")
    except Exception as exc:  # noqa: BLE001 - best-effort capture
        print("SKIP tool_specs:", exc)


def dump_prompt_report() -> None:
    from message.message import Message, TextBlock, ToolUseBlock, ToolResultBlock
    from providers.types import UsageSnapshot

    system_prompt = "You are a helpful coding agent."
    user = Message.from_user_text("List the files in the repo.")
    assistant = Message(
        role="assistant",
        content=[
            TextBlock(text="I'll list the files."),
            ToolUseBlock(
                tool_use_id="toolu_0001",
                name="exec_command",
                input={"command": "ls"},
            ),
        ],
    )
    tool_result = ToolResultBlock(
        tool_use_id="toolu_0001",
        content="README.md\nsrc\n",
        is_error=False,
    )
    usage = UsageSnapshot(input_tokens=120, output_tokens=18)
    base = {"agent_run_id": "ar_demo", "agent": "root", "model": "claude-opus-4-8"}

    # Faithful PromptReportRecorder event schema (see
    # backend/src/prompt/prompt_report_recorder.py). The live recorder also
    # prepends a wall-clock `ts` float at write time; it is omitted here so the
    # golden is deterministic.
    events = [
        {
            **base,
            "event": "llm_request",
            "seq": 1,
            "system_prompt": system_prompt,
            "messages": [user.model_dump(mode="json")],
            "tools": [],
        },
        {
            **base,
            "event": "assistant",
            "seq": 2,
            "message": assistant.model_dump(mode="json"),
            "usage": usage.model_dump(mode="json"),
        },
        {
            **base,
            "event": "tool_results",
            "seq": 3,
            "tool_results": [tool_result.model_dump(mode="json")],
        },
    ]
    write_jsonl("prompt_report/session_golden.jsonl", events)

    # The system-role anomaly: capture the REAL output of the source function so
    # the bug is frozen exactly as Python produces it.
    try:
        from engine.agent.lifecycle import _initial_message_records

        anomaly = _initial_message_records(
            system_prompt=system_prompt,
            seed_messages=[],
            prompt="List the files in the repo.",
        )
        source = "engine.agent.lifecycle._initial_message_records"
    except Exception as exc:  # noqa: BLE001 - fall back to a faithful reproduction
        print("note: importing _initial_message_records failed, reproducing:", exc)
        anomaly = [
            {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
            user.model_dump(mode="json"),
        ]
        source = "reproduced from engine.agent.lifecycle._initial_message_records"
    write_json(
        "prompt_report/initial_messages_anomaly.json",
        {"source": source, "records": anomaly},
    )


def dump_sqlite() -> None:
    from sqlalchemy import create_engine, text
    from db.base import Base
    from db.models import (  # noqa: F401 - imports register tables on Base.metadata
        RequestRecord,
        TaskRecord,
        WorkflowRecord,
        IterationRecord,
        AttemptRecord,
        AgentRunRecord,
        ModelRegistrationRecord,
    )

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    statements: list[str] = []
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE sql IS NOT NULL ORDER BY type, name"
            )
        )
        for (sql,) in result:
            statements.append(sql.strip().rstrip(";") + ";")
    out = OUT / "sqlite" / "schema.sql"
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "-- Canonical clean schema (Base.metadata.create_all) for the seven\n"
        "-- target tables. Captured by parity/_capture/capture.py; the live DDL\n"
        "-- patches in db/engine.py.initialize_db are intentionally NOT applied\n"
        "-- (eos-db replaces them with versioned migrations). Do not edit by hand.\n\n"
    )
    out.write_text(header + "\n\n".join(statements) + "\n")
    print("wrote", out.relative_to(OUT), f"({len(statements)} statements)")


if __name__ == "__main__":
    dump_schemas()
    dump_prompt_report()
    dump_sqlite()
    print("done")
