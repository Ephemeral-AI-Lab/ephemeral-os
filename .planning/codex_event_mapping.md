# Phase 0 — Codex Stream-Translation Smoke Spike Report

**Phase reference:** `.planning/coding_plan_mode_plan.md` Phases table, Phase 0.
**Spike script:** `scripts/spike_codex_stream.py`.
**Date executed:** 2026-05-20.
**Operator:** Yifan (`~/.codex/auth.json`, ChatGPT Plus account).

---

## Purpose

Map every Codex Responses-API SSE event observed in a smoke call onto one
of EphemeralOS's existing `ApiStreamEvent` variants. Decide whether the
existing union is sufficient or needs extension.

---

## Run command

```
.venv/bin/python scripts/spike_codex_stream.py --live > /tmp/codex_stream.out 2>&1
```

## Headers sent (redacted)

```
Authorization: Bearer <REDACTED>
ChatGPT-Account-Id: <REDACTED>
originator: codex_cli_rs
User-Agent: codex_cli_rs/0.125
OpenAI-Beta: responses=experimental
Content-Type: application/json
```

## Response

**HTTP status:** `200 OK`
**cf-mitigated:** *(none — request passed Cloudflare allowlist on first try)*
**Model returned:** `gpt-5.5` (request-id `resp_05936113f03d8049016a0d0e4f756081958c3feb5224cc1392`)
**Stream excerpt:** see `/tmp/codex_stream.out` (full 32+ KB SSE log).

### Key empirical findings before mapping

1. **Cloudflare allowlist matches plan §6.5 v9-A4 exactly.** The full 5-header
   set (`Authorization`, `ChatGPT-Account-Id`, `originator: codex_cli_rs`,
   `User-Agent: codex_cli_rs/0.125`, `OpenAI-Beta: responses=experimental`)
   was accepted with no `cf-mitigated: challenge`. Hermes pattern A
   reproduced verbatim.
2. **`chatgpt_account_id` JWT claim is namespaced, NOT top-level.** Plan
   A15 (citing hermes + pi) reads `payload["chatgpt_account_id"]`. Empirical
   id_token has it at `payload["https://api.openai.com/auth"]["chatgpt_account_id"]`
   (Auth0 namespaced-claim convention). Spike `jwt_extract_chatgpt_account_id`
   patched to check the namespace first, fall back to top-level for forward
   compatibility. **Phase 2 `CodexResponsesClient` must use the namespaced
   lookup.**
3. **Model gating.** ChatGPT-account auth REJECTS `gpt-5-codex` and `gpt-5`
   with HTTP 400 `"not supported when using Codex with a ChatGPT account"`.
   `gpt-5.5` is the model returned by `codex login` on this machine
   (default in `~/.codex/config.toml`). Phase 2 must select the model from
   the user's local Codex config, not hard-code a model id.
4. **Tool envelope is FLAT.** Codex Responses rejects the Chat-Completions
   nested `{"type":"function","function":{"name":...}}` shape with `400
   Missing required parameter 'tools[0].name'`. The accepted shape is
   `{"type":"function","name":..., "description":..., "parameters":...}`
   at the top level. Phase 2 must emit the flat envelope.

---

## Event-mapping table

Seven distinct SSE event types observed:

| Codex event type | First seen | Maps to EphemeralOS variant | Notes |
|------------------|------------|------------------------------|-------|
| `response.created` | sequence #0 | `ApiMessageStartEvent` (analog of Anthropic `message_start`) | Carries `response.id`, model, instructions echo, full tools array, usage=null. |
| `response.in_progress` | seq #1 | *(no-op; informational duplicate of `created`)* | Same payload as `created` minus `created_at` semantics. Discard or coalesce. |
| `response.output_item.added` | seq #2 | `ApiContentBlockStartEvent` (text \| tool_use \| thinking) | `item.type` discriminator: `"reasoning"` → thinking block (Anthropic-equivalent), `"function_call"` → tool_use start (carries `call_id`, `name`, empty `arguments`), `"message"` → text block. |
| `response.output_item.done` | seq #3 | `ApiContentBlockStopEvent` | Identical role to Anthropic `content_block_stop`. |
| `response.function_call_arguments.delta` | seq #5 | `ApiToolUseDeltaEvent` (partial JSON args) | Codex chunks tool-call JSON the same way Anthropic does via `input_json_delta`. |
| `response.function_call_arguments.done` | seq ~37 | *(merge into `ApiContentBlockStopEvent` for the function-call item)* | Tells us the full args string is now complete; tool_use boundary is well-defined. No new variant needed. |
| `response.completed` | terminal | `ApiMessageCompleteEvent` (+ `UsageSnapshot`) | Final usage block with input/output token counts. |

### Variants NOT yet observed in this minimal smoke (single tool turn, no text)

- `response.output_text.delta` — expected during text-emitting turns; maps to `ApiTextDeltaEvent`.
- `response.reasoning_summary_text.delta` — expected when reasoning summaries are enabled; maps to `ApiThinkingDeltaEvent`.

Both are described in the OpenAI Responses API docs and were not exercised
by the spike's pure tool-use turn. They do NOT block GO — both map to
existing variants (text → `ApiTextDeltaEvent`, reasoning → `ApiThinkingDeltaEvent`).

---

## Verdict

**Result:** **GO** (2026-05-20).

**Rationale:**

1. All 7 distinct Codex Responses-API SSE event types observed map cleanly
   onto the existing `ApiStreamEvent` union with NO new variants required.
2. Tool-use boundaries are well-defined — `output_item.added` (with
   `item.type=function_call`) + `function_call_arguments.delta` (streaming
   args) + `function_call_arguments.done` + `output_item.done` collectively
   reproduce Anthropic's `content_block_start` / `input_json_delta` /
   `content_block_stop` lifecycle with no lossy coalescing.
3. The 5-header Cloudflare allowlist passes verbatim per plan v9-A4
   (`codex_cli_rs` originator + matching User-Agent). No `cf-mitigated`
   challenge.
4. Three contract corrections surfaced (JWT namespaced claim, model gating
   for ChatGPT-account, flat tool envelope) — all are mechanical, none
   require extending the event union. Phase 2 `CodexResponsesClient` picks
   these up as implementation details.
5. EphemeralOS's `ApiStreamEvent` union is sufficient as-is. Phase 2
   (`CodexResponsesClient` implementation) can proceed without an
   EXTEND-UNION step.

### Implementation notes propagated to Phase 2

- `tokens.id_token` payload claim lookup: try
  `payload["https://api.openai.com/auth"]["chatgpt_account_id"]` first,
  fall back to `payload["chatgpt_account_id"]`, then raise
  `CodexCredentialIncompleteError`.
- Read model id from `~/.codex/config.toml` `model = "..."` line, NOT
  from a hard-coded constant. Default to `gpt-5.5` only if config is
  absent.
- Tool envelope: `{"type":"function","name":...,"description":...,"parameters":...}`
  flat at top-level. Do NOT nest under `{"function":{...}}`.
- Discriminate `output_item.type`: `"reasoning"` → thinking, `"function_call"`
  → tool_use, `"message"` → text.
