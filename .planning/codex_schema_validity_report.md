# Phase 0.7 — Codex Tool-Schema Validity Probe Report

**Phase reference:** `.planning/coding_plan_mode_plan.md` Phase 0.7 + A4 + A16.
**Spike script:** `scripts/spike_codex_schema_probe.py`.
**Date executed:** 2026-05-20.
**Operator:** Yifan (`~/.codex/auth.json`, ChatGPT Plus account).

---

## Run command

```
.venv/bin/python scripts/spike_codex_schema_probe.py --live > /tmp/codex_schema.out 2>&1
```

## Headers sent

Same 5-header allowlist as Phase 0 (`Authorization`, `ChatGPT-Account-Id`, `originator: codex_cli_rs`, `User-Agent: codex_cli_rs/0.125`, `OpenAI-Beta: responses=experimental`).

## Pre-conditions fixed during this run

1. **Model:** `gpt-5-codex` rejected for ChatGPT-account auth → switched to `gpt-5.5` (the user's local `~/.codex/config.toml` default).
2. **Tool envelope:** nested `{"function":{...}}` rejected → switched to FLAT `{"type":"function","name":...,"parameters":...}`.
3. **`max_output_tokens`:** rejected as "Unsupported parameter" on ChatGPT-account auth → removed from request body. (Codex CLI does not set it either.)

These three fixes are mechanical request-shape requirements, NOT schema sanitizer rules.

---

## Tool-by-tool results

All **23** EphemeralOS tools probed (sandbox + submission + ask_helper + background factories):

| Status | Count | Tools |
|--------|-------|-------|
| PASS (HTTP 200) | **23/23** | read_file, write_file, edit_file, shell, glob, grep, cancel_background_task, check_background_task_result, wait_background_tasks, submit_plan_closes_goal, submit_plan_defers_goal, submit_execution_handoff, submit_execution_success, submit_execution_failure, submit_verification_success, submit_verification_failure, submit_evaluation_success, submit_evaluation_failure, submit_advisor_feedback, submit_resolver_result, submit_exploration_result, ask_advisor, ask_resolver |
| SCHEMA_REJECT | 0/23 | *(none)* |
| OTHER_ERROR | 0/23 | *(none)* |

**SUMMARY:** `PASS=23  SCHEMA_REJECT=0  OTHER_ERROR=0`

---

## Sanitizer rules required

**None.** All Pydantic-derived JSON schemas in `backend/src/tools/` round-trip through Codex Responses API as-is. The hermes `tools/schema_sanitizer.py` module is not needed for our tool set.

Empirical: Codex accepts our `additionalProperties: false`, our nested `properties` maps, our `required` arrays, our string types with no `format`, and our `default` fields without complaint. No `$ref`, `$defs`, `oneOf`, or `anyOf` constructs appear in our generated schemas (they're generated from straight Pydantic field types), so even the hermes-documented rejections cannot trigger.

---

## Verdict

**Result:** **SHIP-AS-IS** (2026-05-20).

**A16 status:** marked **N/A** — no sanitizer module required for Phase 2 `CodexResponsesClient` against ChatGPT-account auth on `gpt-5.5`. If future tools introduce `$ref`/`anyOf` constructs, re-run this probe; the SHIP-WITH-SANITIZER branch then activates A16 as originally specified.

**Risk note:** all probes were against `gpt-5.5`. If Phase 2 needs to support additional models (e.g., a `gpt-5-codex` variant gated on API-key auth), re-run the probe per model.
