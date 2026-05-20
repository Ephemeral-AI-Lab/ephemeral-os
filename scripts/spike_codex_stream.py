"""Phase 0 — Codex stream-translation smoke spike.

Throwaway script (deleted once Phase 2 starts) that POSTs ONE request to
``https://chatgpt.com/backend-api/codex/responses`` with the exact payload
shape EphemeralOS plan-mode will send:

* OAuth Bearer from ``~/.codex/auth.json``.
* JWT-decoded ``chatgpt_account_id`` claim from ``tokens.id_token`` →
  ``ChatGPT-Account-Id`` header (per plan A15 / v9 §6.5 Finding 3).
* Cloudflare-allowlist headers: ``originator: codex_cli_rs``, matching
  ``User-Agent: codex_cli_rs/...``, ``OpenAI-Beta: responses=experimental``.
* ``instructions``: representative EphemeralOS recipe system prompt (no
  forced identity preamble on Codex, unlike Anthropic).
* ``input``: 3 representative user-role spawn messages.
* ``tools``: 3-5 real snake_case schemas from ``backend/src/tools/``.

Two modes (mirroring Phase 0.3):

* ``--dry-run`` (default): build the request body, print it as JSON,
  do NOT hit the network. Authorization + ChatGPT-Account-Id redacted.
* ``--live``: read ``~/.codex/auth.json``, perform the actual POST,
  stream the SSE response to stdout, exit with a single-line verdict.

Output report (filled by S4-live in a future PRD round):
``.planning/codex_event_mapping.md``.

This script is NOT shipped under ``backend/src/`` — throwaway per v9 spec.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_SRC = REPO_ROOT / "backend" / "src"

# Cloudflare allowlist values per v9 §6.5 Finding 3. Hermes ships
# ``codex_cli_rs`` (Rust-flavored origin); we mirror to maximize the
# allowlist hit rate. ``originator`` and ``User-Agent`` are intentionally
# version-paired — Cloudflare's WAF looks at both.
CODEX_ORIGINATOR = "codex_cli_rs"
CODEX_UA_VERSION = "0.125"  # matches Codex CLI release that opened app-server
USER_AGENT = f"{CODEX_ORIGINATOR}/{CODEX_UA_VERSION}"

API_URL = "https://chatgpt.com/backend-api/codex/responses"

CODEX_AUTH_PATH = Path.home() / ".codex" / "auth.json"

# Three representative spawn messages — same shape as Phase 0.3.
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


def _b64url_decode(segment: str) -> bytes:
    """Decode a base64url JWT segment (handles missing padding)."""
    pad = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + pad)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Invalid base64url segment: {exc}") from exc


def jwt_extract_chatgpt_account_id(id_token: str) -> str:
    """Extract `chatgpt_account_id` from a Codex id_token payload.

    Per plan A15: NO signature verification — we're identifying the
    account we already authenticated against, not validating OpenAI's
    signature. Convergence: hermes uses `payload["chatgpt_account_id"]`
    (`agent/auxiliary_client.py:444-480`); pi extracts identically
    (`providers/openai-codex-responses.ts:1300-1310`). If claim missing,
    raise — the smoke verdict goes RECONSIDER.
    """
    parts = id_token.split(".")
    if len(parts) != 3:
        raise ValueError(
            f"id_token has {len(parts)} segments, expected 3 (JWT)"
        )
    payload_raw = _b64url_decode(parts[1])
    payload = json.loads(payload_raw)
    # Empirically (2026-05-20 live spike), the claim is namespaced under
    # the Auth0-style URL key, not a top-level claim. Plan A15 assumed
    # top-level (per hermes/pi reading); the real JWT from `codex login`
    # has `https://api.openai.com/auth.chatgpt_account_id`. Fall back to
    # top-level for forward-compatibility if OpenAI ever flattens.
    ns = payload.get("https://api.openai.com/auth")
    if isinstance(ns, dict):
        account_id = ns.get("chatgpt_account_id")
        if account_id:
            return account_id
    account_id = payload.get("chatgpt_account_id")
    if not account_id:
        raise ValueError(
            "id_token payload missing 'chatgpt_account_id' claim "
            "(checked top-level and 'https://api.openai.com/auth' namespace). "
            "Plan A15 RECONSIDER condition. Available keys: "
            f"{sorted(payload.keys())}"
        )
    return account_id


def load_codex_creds(*, dry_run: bool) -> tuple[str, str]:
    """Return (access_token, chatgpt_account_id) — placeholders in dry-run."""
    if dry_run:
        return (
            "sk-codex-DRY-RUN-ACCESS-PLACEHOLDER",
            "acct_DRY-RUN-ACCOUNT-PLACEHOLDER",
        )
    if not CODEX_AUTH_PATH.exists():
        raise FileNotFoundError(
            f"{CODEX_AUTH_PATH} not found. Log in via `codex auth login`."
        )
    blob = json.loads(CODEX_AUTH_PATH.read_text())
    tokens = blob.get("tokens", {})
    access = tokens.get("access_token")
    id_token = tokens.get("id_token")
    if not access:
        raise ValueError(f"{CODEX_AUTH_PATH} missing tokens.access_token")
    if not id_token:
        raise ValueError(f"{CODEX_AUTH_PATH} missing tokens.id_token")
    account_id = jwt_extract_chatgpt_account_id(id_token)
    return access, account_id


def load_representative_recipe_prompt() -> str:
    """Same loader strategy as Phase 0.3 — try real settings, fall back."""
    sys.path.insert(0, str(BACKEND_SRC))
    try:
        from config.settings import get_settings  # type: ignore
        from prompt.runtime_prompt import build_runtime_system_prompt  # type: ignore

        settings = get_settings()
        prompt = build_runtime_system_prompt(settings, cwd=str(REPO_ROOT))
        if prompt and prompt.strip():
            return prompt
    except Exception:  # noqa: BLE001
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
        "</System Role>"
    )


def load_real_tool_schemas() -> list[dict[str, Any]]:
    """Same loader as Phase 0.3 — load real registry, picked subset.

    Codex Responses API takes tools in OpenAI-Responses shape (NOT the
    Anthropic ``{name, description, input_schema}`` shape). We adapt
    inline: the real EphemeralOS tool exposes Pydantic-derived JSON
    Schema; we wrap it in the Responses-API ``{"type": "function",
    "function": {"name", "description", "parameters"}}`` envelope.
    """
    sys.path.insert(0, str(BACKEND_SRC))
    try:
        from tools.sandbox._lib.registry import make_sandbox_tools  # type: ignore

        sandbox_tools = make_sandbox_tools()
        picked: list[dict[str, Any]] = []
        picked_names = {"read_file", "edit_file", "shell"}
        for tool in sandbox_tools:
            schema = tool.to_api_schema()
            if schema.get("name") in picked_names:
                # Codex Responses API uses FLAT shape (name at top level),
                # not the Chat-Completions nested {"function": {...}}.
                picked.append(
                    {
                        "type": "function",
                        "name": schema["name"],
                        "description": schema.get("description", ""),
                        "parameters": schema.get("input_schema", {}),
                    }
                )
        if picked:
            return picked
    except Exception:  # noqa: BLE001
        pass

    # Fallback — same names as the real registry.
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file from the sandbox.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "Edit a file via exact-string replacement.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                    },
                    "required": ["path", "old_string", "new_string"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "shell",
                "description": "Execute a shell command in the sandbox.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "cwd": {"type": "string"},
                    },
                    "required": ["command"],
                },
            },
        },
    ]


def build_request_body() -> dict[str, Any]:
    recipe = load_representative_recipe_prompt()
    tools = load_real_tool_schemas()
    return {
        "model": "gpt-5.5",
        "instructions": recipe,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": SPAWN_MESSAGE_USER_1}
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": SPAWN_MESSAGE_USER_2}
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": SPAWN_MESSAGE_USER_3}
                ],
            },
        ],
        "tools": tools,
        "stream": True,
        "store": False,
        "parallel_tool_calls": True,
    }


def build_headers(access: str, account_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access}",
        "ChatGPT-Account-Id": account_id,
        "originator": CODEX_ORIGINATOR,
        "User-Agent": USER_AGENT,
        "OpenAI-Beta": "responses=experimental",
        "Content-Type": "application/json",
    }


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    out = dict(headers)
    if "Authorization" in out:
        out["Authorization"] = "Bearer <REDACTED>"
    if "ChatGPT-Account-Id" in out:
        out["ChatGPT-Account-Id"] = "<REDACTED>"
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
    import httpx  # local import — keeps dry-run runnable in minimal envs

    with httpx.Client(timeout=60.0) as client:
        try:
            with client.stream(
                "POST", API_URL, headers=headers, json=body
            ) as response:
                status = response.status_code
                cf_mit = response.headers.get("cf-mitigated", "")
                print(f"HTTP_STATUS={status}", file=sys.stderr)
                if cf_mit:
                    print(f"CF_MITIGATED={cf_mit}", file=sys.stderr)
                if status != 200:
                    text = response.read().decode("utf-8", errors="replace")
                    print(text)
                    verdict_tag = (
                        "CF_CHALLENGE" if cf_mit else f"STATUS_{status}_FAILED"
                    )
                    print(f"VERDICT={verdict_tag}")
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
        description="Phase 0 Codex stream-translation smoke spike."
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
        help="Read ~/.codex/auth.json and POST to chatgpt.com.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    body = build_request_body()
    access, account_id = load_codex_creds(dry_run=args.dry_run)
    headers = build_headers(access, account_id)
    if args.dry_run:
        return run_dry(body, headers)
    return run_live(body, headers)


if __name__ == "__main__":
    sys.exit(main())
