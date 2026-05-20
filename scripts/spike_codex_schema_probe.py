"""Phase 0.7 — Codex tool-schema validity probe.

Throwaway script (deleted once Phase 2 starts) that sends ONE Codex
Responses-API request per EphemeralOS tool schema, classifying each as:

* ``PASS`` — Codex accepts the schema as-is.
* ``SCHEMA_REJECT`` — 400 with a schema-validation error (the sanitizer
  candidates: nested objects, ``$ref``, ``additionalProperties: false``,
  unions, etc.).
* ``OTHER_ERROR`` — 4xx/5xx unrelated to schema shape (auth, rate-limit,
  Cloudflare challenge).

Output report (filled by --live run): ``.planning/codex_schema_validity_report.md``.

Modes mirror the Phase 0 / 0.3 spikes:

* ``--dry-run`` (default): build all per-tool request bodies, print as a
  JSON array, no network. Useful for sanity-checking the matrix before
  committing creds.
* ``--live``: post each request in sequence, print a per-tool result line,
  finish with a summary table. Honors the same Cloudflare-allowlist
  headers as ``spike_codex_stream.py``.

Helpers (``_b64url_decode``, ``jwt_extract_chatgpt_account_id``,
``load_codex_creds``) are duplicated locally rather than imported from
``spike_codex_stream`` — both are throwaways, and shared-helper coupling
between two soon-to-be-deleted scripts would be premature abstraction.
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

CODEX_ORIGINATOR = "codex_cli_rs"
CODEX_UA_VERSION = "0.125"
USER_AGENT = f"{CODEX_ORIGINATOR}/{CODEX_UA_VERSION}"
API_URL = "https://chatgpt.com/backend-api/codex/responses"
CODEX_AUTH_PATH = Path.home() / ".codex" / "auth.json"

PROBE_USER_MESSAGE = (
    "[representative spawn message — schema probe]\n"
    "Choose any available tool and call it with reasonable arguments. "
    "The purpose of this turn is to verify the tool schema is accepted; "
    "actual tool execution will be ignored."
)


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + pad)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Invalid base64url segment: {exc}") from exc


def jwt_extract_chatgpt_account_id(id_token: str) -> str:
    parts = id_token.split(".")
    if len(parts) != 3:
        raise ValueError(f"id_token has {len(parts)} segments, expected 3")
    payload = json.loads(_b64url_decode(parts[1]))
    # Empirically: claim is under Auth0-namespaced URL key, not top-level.
    ns = payload.get("https://api.openai.com/auth")
    if isinstance(ns, dict) and ns.get("chatgpt_account_id"):
        return ns["chatgpt_account_id"]
    account_id = payload.get("chatgpt_account_id")
    if not account_id:
        raise ValueError(
            "id_token payload missing 'chatgpt_account_id' "
            "(checked top-level and 'https://api.openai.com/auth'). "
            f"Keys: {sorted(payload.keys())}"
        )
    return account_id


def load_codex_creds(*, dry_run: bool) -> tuple[str, str]:
    if dry_run:
        return (
            "sk-codex-DRY-RUN-ACCESS-PLACEHOLDER",
            "acct_DRY-RUN-ACCOUNT-PLACEHOLDER",
        )
    if not CODEX_AUTH_PATH.exists():
        raise FileNotFoundError(
            f"{CODEX_AUTH_PATH} not found. Run `codex auth login`."
        )
    blob = json.loads(CODEX_AUTH_PATH.read_text())
    tokens = blob.get("tokens", {})
    access = tokens.get("access_token")
    id_token = tokens.get("id_token")
    if not access:
        raise ValueError(f"{CODEX_AUTH_PATH} missing tokens.access_token")
    if not id_token:
        raise ValueError(f"{CODEX_AUTH_PATH} missing tokens.id_token")
    return access, jwt_extract_chatgpt_account_id(id_token)


def load_all_real_tool_schemas() -> list[tuple[str, dict[str, Any]]]:
    """Return [(tool_name, anthropic_input_schema), ...] for every static tool.

    Covers sandbox + background + submission + ask_helper factories — the
    same 23-tool surface S5's collision test surveys. Order is stable
    across runs (factory iteration order).
    """
    sys.path.insert(0, str(BACKEND_SRC))
    out: list[tuple[str, dict[str, Any]]] = []
    try:
        from tools.ask_helper import make_ask_helper_tools  # type: ignore
        from tools.background import make_background_tools  # type: ignore
        from tools.sandbox._lib.registry import make_sandbox_tools  # type: ignore
        from tools.submission import make_submission_tools  # type: ignore

        for factory in (
            make_sandbox_tools,
            make_background_tools,
            make_submission_tools,
            make_ask_helper_tools,
        ):
            for tool in factory():
                schema = tool.to_api_schema()
                out.append((schema["name"], schema))
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] Could not load real registry: {exc}", file=sys.stderr)
        # Minimal fallback so --dry-run still produces a non-empty matrix.
        out.append(
            (
                "read_file",
                {
                    "name": "read_file",
                    "description": "Read a file from the sandbox.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            )
        )
    return out


def build_per_tool_request(
    tool_name: str, anthropic_schema: dict[str, Any]
) -> dict[str, Any]:
    """Codex Responses request with exactly one tool (the one being probed)."""
    return {
        "model": "gpt-5.5",
        "instructions": (
            "You are a tool-schema probe. Call the available tool once with "
            "any reasonable arguments. The probe ignores tool output."
        ),
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": PROBE_USER_MESSAGE}],
            }
        ],
        # Codex Responses uses FLAT tool envelope (top-level name), not the
        # Chat-Completions nested {"function": {...}} shape.
        "tools": [
            {
                "type": "function",
                "name": tool_name,
                "description": anthropic_schema.get("description", ""),
                "parameters": anthropic_schema.get("input_schema", {}),
            }
        ],
        "stream": True,
        "store": False,
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


def classify_response(
    status: int, body_text: str
) -> tuple[str, str]:
    """Return (classification, short_reason)."""
    if status == 200:
        return "PASS", "200 OK"
    if status == 400:
        lowered = body_text.lower()
        # Heuristic — Codex tends to return JSON with `error.message`
        # mentioning the offending schema key.
        schema_hints = (
            "schema",
            "parameters",
            "additionalproperties",
            "$ref",
            "type",
            "anyof",
            "oneof",
            "format",
        )
        if any(h in lowered for h in schema_hints):
            short = body_text[:160].replace("\n", " ")
            return "SCHEMA_REJECT", f"400: {short}"
        short = body_text[:160].replace("\n", " ")
        return "OTHER_ERROR", f"400 (non-schema): {short}"
    short = body_text[:160].replace("\n", " ")
    return "OTHER_ERROR", f"{status}: {short}"


def run_dry(matrix: list[dict[str, Any]], headers: dict[str, str]) -> int:
    print(
        json.dumps(
            {
                "mode": "dry-run",
                "url": API_URL,
                "headers": redact_headers(headers),
                "tool_count": len(matrix),
                "requests": matrix,
            },
            indent=2,
        )
    )
    return 0


def run_live(
    schemas: list[tuple[str, dict[str, Any]]],
    headers: dict[str, str],
) -> int:
    import httpx

    results: list[tuple[str, str, str]] = []
    with httpx.Client(timeout=60.0) as client:
        for name, schema in schemas:
            body = build_per_tool_request(name, schema)
            try:
                response = client.post(API_URL, headers=headers, json=body)
            except httpx.HTTPError as exc:
                results.append(
                    (name, "OTHER_ERROR", f"transport: {type(exc).__name__}")
                )
                continue
            classification, reason = classify_response(
                response.status_code, response.text
            )
            results.append((name, classification, reason))
            print(f"{name:40s} {classification:14s} {reason}")

    # Summary
    n_pass = sum(1 for _, c, _ in results if c == "PASS")
    n_schema = sum(1 for _, c, _ in results if c == "SCHEMA_REJECT")
    n_other = sum(1 for _, c, _ in results if c == "OTHER_ERROR")
    print("")
    print(f"SUMMARY: PASS={n_pass}  SCHEMA_REJECT={n_schema}  OTHER_ERROR={n_other}")
    if n_schema == 0 and n_other == 0:
        verdict = "SHIP-AS-IS"
    elif n_schema > 0 and n_other == 0:
        verdict = "SHIP-WITH-SANITIZER"
    else:
        verdict = "RECONSIDER"
    print(f"VERDICT={verdict}")
    return 0 if verdict != "RECONSIDER" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 0.7 Codex tool-schema validity probe."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Build per-tool requests, print as JSON array, no network (default).",
    )
    mode.add_argument(
        "--live",
        dest="dry_run",
        action="store_false",
        help="POST each per-tool request and classify response.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    schemas = load_all_real_tool_schemas()
    access, account_id = load_codex_creds(dry_run=args.dry_run)
    headers = build_headers(access, account_id)
    if args.dry_run:
        matrix = [
            {
                "tool_name": name,
                "request": build_per_tool_request(name, schema),
            }
            for name, schema in schemas
        ]
        return run_dry(matrix, headers)
    return run_live(schemas, headers)


if __name__ == "__main__":
    sys.exit(main())
