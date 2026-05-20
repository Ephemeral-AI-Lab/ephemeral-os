"""Phase 0.3 — Anthropic OAuth end-to-end smoke spike.

Throwaway script (deleted once Phase 1 starts) that POSTs ONE request to
``https://api.anthropic.com/v1/messages`` with the exact payload shape
EphemeralOS plan-mode will send:

* OAuth Bearer from macOS Keychain (``Claude Code-credentials``).
* Required headers: ``anthropic-beta``, spoofed ``User-Agent``, ``x-app``.
* ``system``: block #0 = mandatory Claude Code identity literal,
  block #1 = a representative EphemeralOS recipe system prompt.
* ``messages``: 3 representative user-role spawn messages.
* ``tools``: 3-5 real snake_case schemas loaded from ``backend/src/tools/``.

Two modes:

* ``--dry-run`` (default): build the request body, print it as JSON, do NOT
  hit the network. Used to verify the payload shape is well-formed before the
  live run. Authorization header is redacted to ``Bearer <REDACTED>``.
* ``--live``: read keychain, perform the actual POST, stream the SSE response
  to stdout (one JSON line per event), exit with a single-line verdict
  ``STATUS_<code>_<reason>`` so the operator can paste it into
  ``.planning/anthropic_oauth_smoke.md``.

This script is NOT shipped under ``backend/src/`` — it lives at the repo
root's ``scripts/`` per the v9 amendment (see
``.planning/coding_plan_mode_plan.md`` §6.5).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# Plan v9 spec said `backend/src/engine/context_engine/recipes/` but the
# repo actually has `backend/src/task_center/context_engine/recipes/`. The
# spike's pluggable system-prompt loader handles either path; the plan will
# be updated alongside S3.
REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_SRC = REPO_ROOT / "backend" / "src"

# Mandatory first system block per three-repo convergence (hermes, pi,
# openclaw). Without this, Anthropic OAuth returns intermittent 500s and
# content-filter rejections. See `.planning/coding_plan_mode_plan.md` A13.
CLAUDE_CODE_IDENTITY_PREFIX = (
    "You are Claude Code, Anthropic's official CLI for Claude."
)

# Beta header values. ``oauth-2025-04-20`` is required for OAuth-bearer
# auth; ``claude-code-20250219`` is the Claude-Code-vendor beta gate.
ANTHROPIC_BETA_HEADER = "claude-code-20250219,oauth-2025-04-20"

# User-Agent spoof. Real Claude Code currently ships ``2.1.x``; using a
# realistic version reduces the chance of UA-based fingerprinting tripping
# Cloudflare. This is vendor impersonation — see plan ADR §6.5 accepted-
# impersonation note.
USER_AGENT = "claude-cli/2.1.75 (external, cli)"

ANTHROPIC_VERSION = "2023-06-01"

API_URL = "https://api.anthropic.com/v1/messages"

KEYCHAIN_SERVICE = "Claude Code-credentials"

# Three representative spawn messages mirroring the agent's bootstrap shape.
# Real spawn pipeline injects (1) task assignment, (2) initial context
# packet, (3) instruction to begin — we use placeholders that are realistic
# in size and structure but contain no internal identifiers.
SPAWN_MESSAGE_USER_1 = (
    "[representative spawn message 1 — task assignment]\n"
    "You are assigned to implement the following local task. Read the "
    "specification carefully, then begin by exploring the relevant files "
    "before making any changes.\n\n"
    "Task: Add a unit test that verifies a sample function returns the "
    "expected value for a list of three test cases."
)
SPAWN_MESSAGE_USER_2 = (
    "[representative spawn message 2 — context packet]\n"
    "<assigned_task task_id=\"smoke-test-task\">\n"
    "  <description>Demonstrates the canonical EphemeralOS spawn shape.</description>\n"
    "  <acceptance>Tests pass under .venv/bin/pytest.</acceptance>\n"
    "</assigned_task>"
)
SPAWN_MESSAGE_USER_3 = (
    "[representative spawn message 3 — begin]\n"
    "Begin work. Use the available tools to inspect, edit, and verify."
)


def load_keychain_token(*, dry_run: bool) -> str:
    """Return the OAuth access token, or a placeholder in dry-run."""
    if dry_run:
        return "sk-ant-oat01-DRY-RUN-PLACEHOLDER"
    user = os.environ["USER"]
    raw = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            user,
            "-w",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    blob = json.loads(raw)
    return blob["claudeAiOauth"]["accessToken"]


def load_representative_recipe_prompt() -> str:
    """Return a representative EphemeralOS recipe system prompt.

    Strategy: try a real EphemeralOS Settings-based load via
    ``prompt.build_runtime_system_prompt``; if any import or settings init
    fails (likely without proper env config), fall back to a hand-authored
    representative string. The fallback is intentionally REALISTIC in
    structure and length — what matters for the smoke is shape and size,
    not exact content fidelity.
    """
    sys.path.insert(0, str(BACKEND_SRC))
    try:
        from config.settings import get_settings  # type: ignore
        from prompt.runtime_prompt import build_runtime_system_prompt  # type: ignore

        settings = get_settings()
        prompt = build_runtime_system_prompt(settings, cwd=str(REPO_ROOT))
        if prompt and prompt.strip():
            return prompt
    except Exception:  # noqa: BLE001 — fallback is the whole point.
        pass

    return (
        "<System Role>\n"
        "You are an autonomous coding agent operating inside EphemeralOS, a "
        "multi-agent framework that runs tasks under isolated sandboxes with "
        "layerstack overlay + OCC concurrency control.\n\n"
        "Your behavior:\n"
        "- Read files before editing them.\n"
        "- Run tests after meaningful changes.\n"
        "- Communicate concisely; prefer evidence over speculation.\n"
        "- When uncertain, surface the uncertainty rather than guessing.\n"
        "</System Role>\n\n"
        "<Tooling>\n"
        "You have access to file-manipulation tools (read_file, edit_file, "
        "write_file), search tools (grep, glob), and shell execution.\n"
        "</Tooling>\n\n"
        "<Termination Condition>\n"
        "When you have completed the assigned task and verified it, call "
        "the appropriate termination tool.\n"
        "</Termination Condition>"
    )


def load_real_tool_schemas() -> list[dict[str, Any]]:
    """Return Anthropic-format schemas for 3-5 real sandbox tools.

    Imports the live registry. If imports fail (likely without backend env
    init), falls back to representative hand-authored schemas mirroring the
    real shape: snake_case names, JSON-Schema input_schema with required
    fields. The fallback uses the EXACT names from
    ``backend/src/tools/_names.py`` so the smoke still proves snake_case
    custom tools survive the OAuth wire.
    """
    sys.path.insert(0, str(BACKEND_SRC))
    try:
        from tools.sandbox._lib.registry import make_sandbox_tools  # type: ignore

        sandbox_tools = make_sandbox_tools()
        schemas: list[dict[str, Any]] = []
        # Pick 3 (read_file, edit_file, shell) — enough to prove the wire
        # without spamming the request.
        picked_names = {"read_file", "edit_file", "shell"}
        for tool in sandbox_tools:
            schema = tool.to_api_schema()
            if schema.get("name") in picked_names:
                # Anthropic OAuth-tier rejects unknown fields like output_schema
                # (req_011CbCvq95iDuGHspySQeW8j: "tools.0.custom.output_schema:
                # Extra inputs are not permitted"). Keep only the wire fields.
                schemas.append({
                    k: v for k, v in schema.items()
                    if k in {"name", "description", "input_schema"}
                })
        if schemas:
            return schemas
    except Exception:  # noqa: BLE001
        pass

    # Fallback — representative shapes, exact real names.
    return [
        {
            "name": "read_file",
            "description": "Read a file from the sandbox.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum lines to read.",
                    },
                },
                "required": ["path"],
            },
        },
        {
            "name": "edit_file",
            "description": "Edit a file via exact-string replacement.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
        {
            "name": "shell",
            "description": "Execute a shell command in the sandbox.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    ]


def build_request_body() -> dict[str, Any]:
    """Build the full Messages-API request body."""
    recipe_prompt = load_representative_recipe_prompt()
    tools = load_real_tool_schemas()
    return {
        "model": "claude-sonnet-4-5",
        "max_tokens": 1024,
        "stream": True,
        "system": [
            {"type": "text", "text": CLAUDE_CODE_IDENTITY_PREFIX},
            {"type": "text", "text": recipe_prompt},
        ],
        "messages": [
            {"role": "user", "content": SPAWN_MESSAGE_USER_1},
            {"role": "user", "content": SPAWN_MESSAGE_USER_2},
            {"role": "user", "content": SPAWN_MESSAGE_USER_3},
        ],
        "tools": tools,
    }


def build_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "anthropic-version": ANTHROPIC_VERSION,
        "anthropic-beta": ANTHROPIC_BETA_HEADER,
        "User-Agent": USER_AGENT,
        "x-app": "cli",
        "Content-Type": "application/json",
    }


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    out = dict(headers)
    if "Authorization" in out:
        out["Authorization"] = "Bearer <REDACTED>"
    return out


def run_dry(body: dict[str, Any], headers: dict[str, str]) -> int:
    print(
        json.dumps(
            {
                "mode": "dry-run",
                "url": API_URL,
                "headers": redact_headers(headers),
                "body": body,
            },
            indent=2,
        )
    )
    return 0


def run_live(body: dict[str, Any], headers: dict[str, str]) -> int:
    import httpx  # local import — keeps --dry-run runnable in minimal envs

    with httpx.Client(timeout=60.0) as client:
        try:
            with client.stream(
                "POST", API_URL, headers=headers, json=body
            ) as response:
                status = response.status_code
                print(f"HTTP_STATUS={status}", file=sys.stderr)
                if status != 200:
                    text = response.read().decode("utf-8", errors="replace")
                    print(text)
                    print(f"VERDICT=STATUS_{status}_FAILED")
                    return 1
                printed_any = False
                for line in response.iter_lines():
                    if not line:
                        continue
                    print(line)
                    printed_any = True
                if not printed_any:
                    print("VERDICT=STATUS_200_NO_STREAM")
                    return 1
                print("VERDICT=STATUS_200_OK")
                return 0
        except httpx.HTTPError as exc:
            print(f"VERDICT=HTTP_ERROR_{type(exc).__name__}")
            print(str(exc))
            return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 0.3 Anthropic OAuth smoke spike."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Build and print the request without hitting the network (default).",
    )
    mode.add_argument(
        "--live",
        dest="dry_run",
        action="store_false",
        help="Read keychain and POST to api.anthropic.com.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    body = build_request_body()
    token = load_keychain_token(dry_run=args.dry_run)
    headers = build_headers(token)
    if args.dry_run:
        return run_dry(body, headers)
    return run_live(body, headers)


if __name__ == "__main__":
    sys.exit(main())
