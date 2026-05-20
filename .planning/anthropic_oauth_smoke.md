# Phase 0.3 — Anthropic OAuth End-to-End Smoke Spike Report

**Phase reference:** `.planning/coding_plan_mode_plan.md` §6.5 (v9 amendment).
**Spike script:** `scripts/spike_anthropic_oauth.py`.
**Date executed:** 2026-05-20.
**Operator:** Yifan (macOS Keychain `Claude Code-credentials`, subscription `max`, tier `default_claude_max_20x`).

---

## Purpose

Prove that Anthropic's OAuth Messages-API endpoint accepts EphemeralOS's
actual payload shape — identity preamble + custom recipe system prompt + 3
synthetic user messages + real snake_case tool schemas from `backend/src/tools/` —
**before** Phase 1 commits to ~10 acceptance criteria worth of refactor.

GO ⇒ Phase 1 starts.
RECONSIDER ⇒ payload shape needs adjustment (system-prompt sanitization,
schema fields, headers, etc.); re-plan before Phase 1.

---

## Run command

```
.venv/bin/python scripts/spike_anthropic_oauth.py --live > /tmp/anthropic_smoke.out 2>&1
```

*(Use `--dry-run` to preview the request without hitting the network — no
keychain access required.)*

---

## Headers (redacted)

```
Authorization: Bearer <REDACTED>
anthropic-version: 2023-06-01
anthropic-beta: claude-code-20250219,oauth-2025-04-20
User-Agent: claude-cli/2.1.75 (external, cli)
x-app: cli
Content-Type: application/json
```

## Payload shape (summary)

* `model`: `claude-sonnet-4-5`
* `system`: 2 blocks — `[0]` Claude Code identity preamble, `[1]` representative recipe system prompt (real EphemeralOS settings if loadable, otherwise fallback string).
* `messages`: 3 user-role spawn messages (`task assignment`, `context packet`, `begin`).
* `tools`: 3 real schemas exported from `tools.sandbox._lib.registry.make_sandbox_tools()` → `to_api_schema()` — names `read_file`, `edit_file`, `shell`.

---

## Response

**HTTP status:** `200 OK` (after one-shot fix — see Notes).

**Response headers of interest:** model `claude-sonnet-4-5-20250929`, request-id
`msg_01ECrMGX7SkvuquYhtgv83mE`. No `cf-mitigated` challenge. No content-filter
rejection. Standard service tier.

**Stream excerpt** (events observed end-to-end):

```
event: message_start          → model claude-sonnet-4-5-20250929, input_tokens=3469
event: content_block_start    → index=0, type=text
event: content_block_delta    → "I'll start by exploring the repository structure ..."
event: content_block_stop     → index=0
event: content_block_start    → index=1, type=tool_use, name="shell", id=toolu_01GsUGrR...
event: content_block_delta    → input_json_delta partials: {"command": "find . -type f -name \"*.py\" | head -20"}
event: content_block_stop     → index=1
event: content_block_start    → index=2, type=tool_use, name="shell", id=toolu_011YBHgX...
event: content_block_delta    → input_json_delta partials: {"command": "ls -la"}
event: content_block_stop     → index=2
event: message_delta          → stop_reason=tool_use, output_tokens=125
event: message_stop
```

Full raw output (4116 bytes) saved to `/tmp/anthropic_smoke.out` during the run.

### Notes — first attempt failure & fix

First invocation returned HTTP 400 (`req_011CbCvq95iDuGHspySQeW8j`):

```
tools.0.custom.output_schema: Extra inputs are not permitted
```

Root cause: `BaseTool.to_api_schema()` emits an extra `output_schema` field
(Pydantic-derived) that Anthropic's OAuth Messages-API rejects. Spike was fixed
to strip the payload down to `{name, description, input_schema}` before
sending. Re-run returned HTTP 200 with two `tool_use` content blocks and
`stop_reason=tool_use` — proves the OAuth path supports our snake_case custom
tools end-to-end.

**Implication for Phase 1:** `AnthropicClient.stream_message()` under the OAuth
strategy must filter outgoing tool schemas to the Anthropic-Messages-API
allowlist. This is a one-line transformation; it does NOT require recipe-layer
changes (Principle 2 preserved).

---

## Verdict

**Result:** **GO** (2026-05-20).

**Rationale:**

1. Anthropic OAuth endpoint accepts EphemeralOS's exact payload shape — identity preamble
   (block #0), real recipe-style system prompt (block #1), 3 synthetic user spawn
   messages, and 3 snake_case sandbox tool schemas — with **no content-filter rejection**.
2. The model emitted two well-formed `tool_use` content blocks invoking our custom `shell`
   tool, confirming the OAuth path supports framework-owned tool loops end-to-end
   (Principle 1 satisfied — we keep layerstack/OCC + audit ownership).
3. The one HTTP 400 observed (`output_schema` field rejection) is a known mechanical fix:
   sanitize tool schemas to the Anthropic allowlist (`name`, `description`,
   `input_schema`) before sending. This is a one-line transformation inside
   `AnthropicClient` and adds zero new acceptance criteria to Phase 1's scope.
4. All five OAuth-tier headers (Authorization, anthropic-version, anthropic-beta,
   User-Agent `claude-cli/2.1.75`, x-app `cli`) round-trip cleanly. No
   `cf-mitigated` challenge. The Hermes-pattern-A wire matches our v9 plan
   verbatim.
5. Subscription tier `default_claude_max_20x` confirms Claude Max coverage; the
   overage-credit warning documented in pre-mortem #1 (and surfaced via A11
   `plan_mode_active=true`) remains the right user-visible mitigation.

### Decision matrix

| Observation                                                 | Implies                                            |
|-------------------------------------------------------------|----------------------------------------------------|
| HTTP 200 + ≥1 `content_block_start` (text or tool_use)      | **GO** — payload accepted, proceed to Phase 1.     |
| HTTP 200 + only `message_start`/`message_stop`, no content  | **GO with caveat** — proceed, watch for empty turns in Phase 1 manual smoke. |
| HTTP 4xx with content-filter rejection on system prompt     | **RECONSIDER** — sanitize recipe prompt (strip competitor names, internal identifiers). |
| HTTP 4xx on tool schema (extra `output_schema` field, etc.) | **RECONSIDER** — strip non-Anthropic-Messages-API fields before send. |
| HTTP 4xx on headers (missing/wrong `anthropic-beta`)        | **RECONSIDER** — re-derive correct beta + UA from latest Claude Code release. |
| HTTP 403 / `cf-mitigated: challenge`                        | **RECONSIDER** — non-residential IP path; investigate before Phase 1. |
| HTTP 5xx repeated                                            | **RECONSIDER** — Anthropic OAuth path possibly degraded; retry then file. |

---

## Follow-up

* If **GO**: bump status header in `.planning/coding_plan_mode_plan.md` to
  `Phase 0.3 = GO (<date>)`. Proceed to Phase 0 (Codex stream-translation
  spike) and Phase 0.7 (Codex schema validity) in the next progressive round.
* If **RECONSIDER**: file specific failure mode here, re-plan Phase 1
  scope (or insert a Phase 0.4 mitigation step), do NOT touch
  `backend/src/providers/clients/anthropic_native.py` constructor signature
  yet.
