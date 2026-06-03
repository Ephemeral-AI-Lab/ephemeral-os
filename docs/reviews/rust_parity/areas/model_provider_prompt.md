# Rust parity audit — Model provider + SSE + prompt/context assembly

Area key: `model_provider_prompt` (domain: agent-core)
Reviewer: workflow-subagent. Source precedence: Python = ground truth; `docs/architecture/*.html` = corroboration; checklist = what-to-confirm.

## Ground truth

Python providers/prompt/message:

- `backend/src/providers/types.py:38-47` — `MessageRequest(model, messages, system_prompt=None, max_tokens=32768, tools=[], tool_choice=None)`; `:21-30` `UsageSnapshot(input_tokens, output_tokens, total_tokens property)`; `:55-59` `SupportsStreamingMessages.stream_message`.
- `backend/src/providers/provider.py:15-111` — `make_api_client` dispatch: external passthrough, `class_path` colon-form → importlib `cls(db_kwargs=)`, `EOS_DISABLE_CODING_PLAN_MODE=1` kill switch (`:46-56`), `[coding-plan-mode]` notice print (`:59-64`), empty/legacy → `AnthropicClient(api_key, base_url)` (`:75-84`).
- `backend/src/providers/clients/anthropic_native.py` — native SDK client. `MAX_RETRIES=3, BASE_DELAY=1.0, MAX_DELAY=30.0` (`:67-69`); retry gated on `emitted_any` (`:144-181`); refresh-on-401 once (`:156-166`); `_is_retryable` = `{429,500,502,503,529}` ∪ Connection/Timeout/OSError (`:299-305`); `_translate_error` 401/403→Auth, 429→RateLimit, else→Request (`:307-317`); mid-stream `ToolUseDeltaEvent` at `content_block_stop` (`:264-282`); thinking_delta→`ThinkingDeltaEvent` (`:257-259`); input_tokens from `final_msg.usage` (`:291-294`); OAuth `system_prefix` identity block #0 logic (`:198-218`); backoff `min(BASE_DELAY*2**attempt, MAX_DELAY)` (`:173`).
- `backend/src/providers/auth_strategy.py` — `_ApiKeyStrategy` (`use_auth_token` → `auth_token` vs `api_key`, `:43-46`), `_ClaudeOAuthStrategy` (macOS Keychain, `:71-133`), `CLAUDE_OAUTH_DEFAULT_HEADERS` (`:60-64`), `CLAUDE_OAUTH_SYSTEM_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."` (`:140-142`).
- `backend/src/providers/clients/coding_plan/codex.py` — Codex Responses client: headers (`:229-237`), flat tool envelope, `max_output_tokens` omitted, `store:false`, `parallel_tool_calls:true` (`:239-280`), SSE event translation (`:337-415`), refresh-on-401 (one extra attempt, `range(2)`, `:299-321`), `stop_reason` default `"end_turn"` (`:413`), JWT account extraction (`:79-110`).
- `backend/src/providers/errors.py:6-35` — `EphemeralOSApiError(status_code, request_id)` + Auth/RateLimit/Request subclasses.
- `backend/src/message/message.py` — `TextBlock/ToolUseBlock/ThinkingBlock/ToolResultBlock/SystemNotificationBlock` (`:14-75`); `to_api_param` drops ThinkingBlock (`:127-140`); `serialize_content_block` (`:143-174`): thinking→`{"type":"thinking"}`, system_notification→text wrapped in `<system-reminder>\n…\n</system-reminder>`, tool_result omits metadata/is_terminal; `parse_assistant_message` (`:177-201`) maps thinking/text/tool_use, drops unknown, mints `toolu_<uuid>` on missing id.
- `backend/src/message/events.py` — `ThinkingDeltaEvent/AssistantTextDeltaEvent/AssistantMessageCompleteEvent/ToolUseDeltaEvent` + engine events (`:22-136`); all carry `agent_name`/`agent_run_id`.
- `backend/src/prompt/runtime_prompt.py` — `build_runtime_system_prompt(settings, cwd)` (`:15-31`, appends fast-mode section); `build_termination_condition_prompt(terminal_tools)` (`:34-66`) emits `<Termination Condition>` block with the literal WARNING lines and sorted `` - `name` `` rows.
- `backend/src/prompt/prompt_report_recorder.py` — `PromptReportRecorder`: `next_seq` **pre-increments from 0 → first seq = 1** (`:33-35`); `record_llm_request/assistant/tool_results` with `model_dump(mode="json")` (`:51-99`).
- Engine call sites: `backend/src/engine/query/request.py:30-39` (one `next_seq` per turn, shared by all 3 rows); `backend/src/engine/agent/factory.py:159-165` (termination prompt appended), `:320-329` (`build_runtime_system_prompt` then profile body).
- Config: `backend/src/config/sections/providers.py:20-22` — `RetryConfig(max_retries=3, base_delay_s=1.0, max_delay_s=30.0)`.

Architecture corroboration: `docs/architecture/agent_loops/model-provider.html` (request/stream contract, mid-stream tool deltas, retry-before-visible, drop output_schema, coding-plan classes, refresh-on-401, `coding_plan_mode_error` logs, "config caveat: client has local retry constants — treat as separate evidence"); `docs/architecture/agent_loops/prompt-context.html` (`build_runtime_system_prompt` base + fast-mode, factory appends profile + terminal warning; system prompt is engine-owned, never a Message).

Parity corpus: `agent-core/parity/README.md:25-44` documents the **system-role transcript anomaly** — the Rust port fixes it (system stays a request field, never a `Message`); golden `session_golden.jsonl` is the faithful recorder output; `initial_messages_anomaly.json` freezes the buggy `_initial_message_records` output for a visible diff.

## Rust mapping

| Python | Rust |
|---|---|
| `providers/types.py` | `eos-llm-client/src/types.rs` (`UsageSnapshot`, `ToolSpec`, `ToolChoice`, `LlmRequest`+builder, `DEFAULT_MAX_TOKENS=32768`) |
| `message/message.py` | `eos-llm-client/src/message.rs` (`Message`, `ContentBlock`, `MessageRole`) — neutral transcript shape only; wire projection moved to provider modules |
| `message/events.py` (4 model events) | `eos-llm-client/src/events.rs` (`LlmStreamEvent` 4 variants + `StopReason`) |
| `providers/errors.py` | `eos-llm-client/src/error.rs` (`ProviderError`+`ProviderErrorKind`) |
| `auth_strategy.py` (explicit only) | `eos-llm-client/src/auth.rs` (`Auth::ApiKey`/`Bearer`) — OAuth/Keychain dropped |
| `anthropic_native.py` encode/decode | `eos-llm-client/src/anthropic.rs` |
| `codex.py` (loosely) / OpenAI Responses | `eos-llm-client/src/openai.rs` (no Python source) |
| retry loop | `eos-llm-client/src/retry.rs` (on `eos_config::RetryConfig`) |
| SSE framing (SDK-internal in Py) | `eos-llm-client/src/sse.rs` |
| `SupportsStreamingMessages` | `eos-llm-client/src/client.rs` (`LlmClient` trait) |
| `prompt/runtime_prompt.py::build_termination_condition_prompt` | `eos-engine/src/prompt/runtime_prompt.rs` (**rewritten text**) |
| `prompt/runtime_prompt.py::build_runtime_system_prompt` | **ABSENT** — base prompt passed in as `BuildQueryContextInput.base_system_prompt` |
| `prompt_report_recorder.py` | `eos-engine/src/prompt_report.rs` |

## Invariant table

| # | Invariant | Status | Sev | Python file:line | Rust file:line | Note |
|---|---|---|---|---|---|---|
| 1 | Anthropic + OpenAI SSE streaming parity | partial | medium | anthropic_native.py:234-296; codex.py:337-415 | anthropic.rs:129-271; openai.rs:109-224 | Anthropic decode is faithful; OpenAI is a NEW (no-Python) Responses decoder. Several Codex-specific behaviors NOT carried over (D2,D3,D4). |
| 2 | tool_use / Reasoning / text block parsing parity | partial | medium | message.py:143-201; anthropic_native.py:248-282 | anthropic.rs:158-271; message.rs:44-92 | Block parsing matches; but neutral `Reasoning` serializes `type:"reasoning"`, diverging from frozen `thinking` schema (D5). Empty tool_use id fails fast instead of minting `toolu_<uuid>` (D6). |
| 3 | Prompt assembly (system + runtime + context) parity | divergent | high | runtime_prompt.py:15-66; factory.py:320-329 | prompt/runtime_prompt.rs:9-28; agent/factory.rs:108-129 | `build_runtime_system_prompt` (incl. fast-mode) ABSENT; `build_termination_condition_prompt` TEXT fully rewritten — different wording, no `<Termination Condition>` tag, no WARNING lines (D1). |
| 4 | prompt_report golden parity | partial | medium | prompt_report_recorder.py:33-99; request.py:31 | prompt_report.rs:90-181 | Event shapes/fields match golden; but `next_seq` post-increments from 0 (Rust first seq=0) vs Python pre-increment from 0 (first seq=1) (D7). System-role anomaly fix is intentional (README §4). |

Extra constants verified equal: `DEFAULT_MAX_TOKENS=32768` (types.rs:112 ↔ types.py:45); `RetryConfig` 3/1.0/30.0/{429,500,502,503,529} (eos-config/providers.rs:29-32 ↔ config/sections/providers.py:20-22 ↔ anthropic_native.py:67-69); retry status set + 5xx Server grouping {500,502,503,529} (error.rs:71, retry.rs:88-98 ↔ anthropic_native.py:302); backoff `min(base*2^attempt, max)` (retry.rs:112 ↔ anthropic_native.py:173); 401/403→Auth, 429→RateLimit (error.rs:68-73 ↔ anthropic_native.py:313-317).

## Disparities

### D1 — `build_termination_condition_prompt` text fully rewritten (HIGH, divergent)
Python (`runtime_prompt.py:52-61`) emits, when terminal tools exist:
```
<Termination Condition>

WARNING: These are one-way exit tools.
If you call any of them, the run terminates immediately.
Your lifecycle ends at that moment: no more reasoning, no more tool calls, no recovery in the same run.
Do not call a termination tool until you are fully ready to end the run.

- `name1`
- `name2`

</Termination Condition>
```
Names are de-duplicated, stripped, and **sorted** (`:45-51`). Rust (`prompt/runtime_prompt.rs:13-27`) emits an entirely different string:
```
When your assigned work is complete, call exactly one terminal tool in its own final message.
Available terminal tools:
- `name`: <selection_guidance>
```
Differences: (a) no `<Termination Condition>` XML wrapper; (b) none of the WARNING/lifecycle lines; (c) rows include a `: selection_guidance` suffix pulled from `descriptor(terminal)` that has no Python analog; (d) ordering is `BTreeSet<ToolName>` iteration (by typed name) not the Python sorted-string set; (e) silently skips any terminal whose `TerminalTool::from_tool_name` returns None, where Python lists every name. **Why it matters:** the system prompt is a behavioral input to the model. This is the strongest one-way-exit guardrail in the Python prompt and it is gone; the model receives materially different termination guidance. **Fix:** port the Python literal verbatim (wrapper + WARNING lines + sorted `` - `name` `` rows), or document this as an intentional prompt redesign with sign-off.

### D2 — OpenAI `stop_reason` has no default; Codex defaults to `"end_turn"` (MEDIUM, divergent)
Codex Python reads `resp.get("stop_reason", "end_turn")` (`codex.py:413`) — a missing field yields `"end_turn"`. Rust `decode_openai` (`openai.rs:197-200`) reads `response.stop_reason` with `.and_then(...).map(StopReason::parse)` → `None` when absent. Note: real OpenAI Responses API does not emit `stop_reason` at all (it uses `status`/`incomplete_details`), so the fixtures (`parity/sse/openai/*.sse`, `tests/fixtures/openai/full.sse`) carry a synthetic `"stop_reason"` to make the test pass. **Why it matters:** against real OpenAI wire, every completion would surface `stop_reason: None`, where the Python Codex analog would surface `end_turn`. Downstream loop logic keying on stop reason could differ. **Fix:** either default OpenAI to `EndTurn` when absent (Codex parity) or document the OpenAI decoder as a placeholder pending real Responses-API stop mapping.

### D3 — Codex flat tool envelope / body fields not modeled by OpenAI encoder (MEDIUM, divergent)
Codex body (`codex.py:270-280`) sets `instructions`, `input`, `stream:true`, `store:false`, `parallel_tool_calls:true`, flat tools `{type:function, name, description, parameters}`, and **omits `max_output_tokens`**. Rust `encode_openai_body` (`openai.rs:234-249`) sets `model/input/max_output_tokens/stream/instructions/tools/tool_choice` — it **includes `max_output_tokens`** (Codex omits it), and **omits `store:false`/`parallel_tool_calls:true`**. Tool entry shape (`openai.rs:307-318`) matches the flat Codex shape but **adds `output_schema`** (Codex never sends it). **Why it matters:** if the OpenAI client is ever pointed at the Codex/ChatGPT endpoint, `max_output_tokens` and `output_schema` are exactly the fields Codex flags as `model_rejected`/`schema_rejected` (`codex.py:144-149`). This is a real wire divergence from the only existing Python Responses client. **Fix:** if OpenAI is meant to be the Codex replacement, mirror the omissions; otherwise label it a distinct generic-OpenAI client (current doc comment says "no Python source").

### D4 — OpenAI tool-buffer keying drops Codex's id fallbacks (LOW, divergent)
Codex keys the in-progress tool buffer by `item.get("id") or item.get("call_id")` and the emitted id by `item.get("call_id") or item.get("id") or f"toolu_{uuid4().hex}"` (`codex.py:371-376`). Rust keys the buffer strictly by `item["id"]` and the emitted id strictly by `item["call_id"]` (`openai.rs:147-156`), failing the stream with a Decode error if `call_id` is empty (`:169-178`). **Why it matters:** a Responses item missing `id` (keyed under `""`) or missing `call_id` behaves differently — Python tolerates and synthesizes, Rust drops/errors. Low severity because well-formed OpenAI streams always carry both. **Fix:** accept; or add `id`/`call_id` fallbacks if targeting the Codex endpoint.

### D5 — Neutral `Reasoning` block serializes `type:"reasoning"`, diverging from frozen `thinking` schema (MEDIUM, divergent)
Python `ThinkingBlock` has `type: Literal["thinking"]` (`message.py:30-34`) and `model_dump` emits `{"type":"thinking",...}`; the parity golden freezes this (`parity/schemas/thinking_block.schema.json` → const `"thinking"`). Rust `ContentBlock::Reasoning` (`message.rs:62-67`) serializes `{"type":"reasoning"}` (with `#[serde(alias="thinking")]` for *decode* only). The persisted neutral transcript / prompt_report `message.content` and `assistant` rows therefore write `"reasoning"` where Python wrote `"thinking"`. **Why it matters:** any consumer of the on-disk transcript/prompt-report JSONL that keys on block type sees a different tag than Python produced; old transcripts decode but new ones are not byte-identical to the frozen schema. This is the documented GC-llm-client-01 rename, **but** the prompt_report golden (`session_golden.jsonl`) contains no reasoning block, so the parity test (`parity/tests/prompt_report.rs`) cannot catch the divergence. **Fix:** confirmed-intentional rename — record it as an accepted transcript-format change and add a reasoning-block row to the golden so the rename is a reviewed, visible diff (mirrors the README's stated philosophy for the system-role fix).

### D6 — Empty/missing tool_use id fails the stream instead of minting `toolu_<uuid>` (LOW, divergent)
Python `parse_assistant_message` mints `toolu_{uuid4().hex}` when the SDK block has no id (`message.py:189-190`), and `_stream_once` yields the (possibly empty) id verbatim mid-stream (`anthropic_native.py:279`). Rust (`anthropic.rs:218-227`, `openai.rs:169-178`) rejects an empty id with a fatal `Decode` error (`ToolUseId` newtype forbids empty). The code comment concedes "Python passed the empty id through / synthesized a `toolu_<uuid>`". **Why it matters:** an Anthropic/OpenAI stream that ever sent an empty tool id would abort the whole turn in Rust but proceed in Python. Low severity (providers always send ids). **Fix:** accept (documented), or mint a default id in eos-types as the comment proposes.

### D7 — `next_seq` off-by-one vs Python pre-increment (MEDIUM, bug)
Python `next_seq` is `self._seq += 1; return self._seq` starting at 0 → **first returned seq = 1** (`prompt_report_recorder.py:33-35`); the golden uses 1/2/3 accordingly. Rust `next_seq` returns the current value then increments, starting at 0 → **first returned seq = 0** (`prompt_report.rs:90-95`); the Rust test asserts 0/0/0 (`prompt_report.rs:248-250`). Both correctly share one seq across the three rows of a turn (request.py:31 ↔ request.rs:21-24), so the per-turn semantics match — but the **absolute seq values are off by one** between the two implementations. **Why it matters:** prompt-report seq is a turn index in the persisted JSONL; a tool diffing Python-vs-Rust transcripts, or any consumer expecting 1-based turns, sees every turn shifted by one. The golden (`session_golden.jsonl` seq 1/2/3) is the frozen Python truth, and the Rust recorder would never reproduce a `seq:1` first turn. **Fix:** make Rust `next_seq` pre-increment (return `state.next_seq` after incrementing, or initialize to 1) to match Python, and re-point the Rust unit test.

### D8 — OAuth/coding-plan provider routing entirely dropped (informational / intentional)
The whole coding-plan axis — `make_api_client` class_path dispatch, `EOS_DISABLE_CODING_PLAN_MODE` kill switch, `[coding-plan-mode]` notice, `AnthropicPlanClient` (Keychain OAuth + `CLAUDE_OAUTH_SYSTEM_PREFIX` identity block #0), `CodexResponsesClient`, refresh-on-401, and `coding_plan_mode_error` categorized logging — has no Rust counterpart. `auth.rs:1-10` explicitly documents dropping the base-url heuristic and the macOS OAuth strategy (GC-llm-client-04). **Classification:** INTENTIONAL migration scope cut, not a bug — but it is a large behavioral surface (auth modes, kill switch, system-prefix identity block, error categorization) that the Rust port does not yet cover. Worth flagging for the migration tracker: provider selection/composition (`make_api_client` equivalent) and refresh-on-401 are not in this crate at all.

### D9 — Refresh-on-401 retry not ported (informational / intentional)
Both Python clients retry once after refreshing credentials on a 401 (`anthropic_native.py:156-166`, `codex.py:299-321`). Rust `retry.rs` treats `Authentication` (401/403) as **never retryable** (`retry.rs:94-97`) and the doc comment states "The Python refresh-on-401 retry is dropped with the OAuth strategy" (`retry.rs:10-11`). **Classification:** intentional, tied to D8 (no refreshable strategy exists in Rust). Consistent with the OAuth drop.

## Extra findings

- **Anthropic usage token sources match precisely:** Rust reads `input_tokens` from `message_start` and `output_tokens` from `message_delta` (anthropic.rs:160,251), matching the SDK `get_final_message` merge the Python comment describes. Verified against `tests/fixtures/anthropic/full.sse` (input 10 / output 15).
- **SSE splitter is robust** (`sse.rs`): CRLF tolerance, multi-line `data:` join, end-of-stream flush without trailing blank line, and a proptest proving chunk-boundary invariance — exceeds the Python path (which relied on the SDK's framing). No parity concern; an improvement.
- **`[DONE]` sentinel** is handled (sse.rs:123) matching Codex's `payload_text == "[DONE]"` skip (codex.py:341). Anthropic doesn't send it; harmless.
- **`message_stop` vs `get_final_message`:** Rust assembles the final `Message` from accumulated blocks at `message_stop` (anthropic.rs:253-266); Python uses the SDK's `get_final_message`. Functionally equivalent for the streamed blocks, but Rust will not recover any block the SDK would have repaired/added post-stream. Low risk.
- **`error_detail` truncates to 500 chars** (client.rs:68) vs Codex's `body_text[:512]` (codex.py:327) — minor (500 vs 512), only affects error message text, no behavioral branch keys on it.
- **`parse_tool_args` → `{}` on malformed/non-object** (sse.rs:204-212) matches Python's `except (JSONDecodeError, TypeError): args = {}` (anthropic_native.py:276-277, codex.py:394-395). Match.
- **`tool_result` wire omits metadata + is_terminal** on both providers (anthropic.rs:318-328, openai.rs:284-292) matches `serialize_content_block` (message.py:169-174). Match, with tests.
- **`system_notification` wrapping** `<system-reminder>\n{text}\n</system-reminder>` matches byte-for-byte (anthropic.rs:329-332, openai.rs:268-273 ↔ message.py:156-159). Match.
- **OpenAI message serialization is richer than Codex's:** Codex `build_body` only encodes user `TextBlock`s as `input_text` and passes through nothing else (`codex.py:244-255`); Rust `serialize_openai_message` (openai.rs:256-303) handles role-based `input_text`/`output_text`, tool_use→`function_call`, tool_result→`function_call_output`, and system_notification. This is a more complete (forward-looking) encoder than the Python S4 scope — divergent but in the "more complete" direction; verify it matches the real Responses API rather than the partial Codex body.
- **`MessageRole` rejects `"system"`** (message.rs:19-26, tested :176-184) structurally enforces the README §4 system-role fix — the anomaly cannot recur in Rust. Confirmed good.

## Open questions

1. Is the `build_termination_condition_prompt` rewrite (D1) an approved prompt redesign, or an accidental loss of the WARNING guardrail? No migration doc was found justifying the new text; treat as HIGH until confirmed.
2. Where does `build_runtime_system_prompt` (fast-mode section, cwd) get ported? It is absent from this crate; `base_system_prompt` is injected into `BuildQueryContextInput`. Is fast-mode guidance assembled elsewhere (runtime/config) or dropped entirely? Not located in eos-engine.
3. Is the OpenAI client (`openai.rs`) intended as the Codex/ChatGPT replacement or a separate generic OpenAI-Responses client? D2/D3/D4 hinge on this. The doc comment says "no Python source," implying generic — but then the `stop_reason`/`max_output_tokens` choices need real-API validation.
4. Should the prompt_report golden gain a reasoning-block row so the `thinking`→`reasoning` rename (D5) is a reviewed diff, per the README's own stated philosophy for the system-role fix?
5. The seq off-by-one (D7): is 0-based intentional for Rust, accepting that Rust transcripts will never match the frozen `session_golden.jsonl` seq values (1/2/3)? If so the golden should be re-derived; if not, it's a bug.
