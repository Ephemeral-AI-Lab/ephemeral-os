# impl-eos-llm-client — provider-neutral LLM types + direct HTTP/SSE Anthropic & OpenAI clients

> Owning crate in the agent-core workspace. Conforms to ./spec-conventions.md.
> Plan section: ../backend_agent_core_rust_migration_PLAN.md §6 (lines 433-501)
> and "API Client Layer" (lines 1068-1102).

## 1. Purpose & Responsibility (SRP)

`eos-llm-client` owns the **provider-neutral conversation/streaming vocabulary**
and the **direct HTTP/SSE clients** that turn an `LlmRequest` into a stream of
normalized `LlmStreamEvent`s. It is the single boundary where a wire protocol
(Anthropic Messages, OpenAI Responses) is encoded from neutral types and decoded
back into neutral types. It owns `Message`/content blocks, `UsageSnapshot`,
`LlmRequest`, `LlmStreamEvent`, `ProviderError`, `ToolSpec`, and the `LlmClient`
trait (anchor §5).

This crate **must NOT**:

- Depend on any provider SDK (`anthropic`, `async-openai`, etc.) — direct
  `reqwest` + a hand-rolled SSE frame parser only (plan §6, §API Client Layer).
- Own engine-domain events. `ToolExecution*`, `BackgroundTaskStarted`, and
  `SystemNotification` (the runtime notification dataclass) live with the
  `EventSource` trait in `eos-engine`; this crate's `LlmStreamEvent` carries
  ONLY the four model-stream variants the plan names.
- Carry `ToolName`/`ToolIntent`/`ToolExecutor`/registry — those stay in
  `eos-tools`, which depends on this crate for `ToolSpec` (anchor §5a).
- Port `coding_plan` clients, the `class_path` importlib dispatch in
  `providers/provider.py`, or the macOS-Keychain OAuth strategy
  (`auth_strategy.py::_ClaudeOAuthStrategy`) — explicit non-goals (anchor §2,
  plan §6 gap closeout).
- Decide tool visibility, build the model-facing `Vec<ToolSpec>` (that is the
  agent-spawn / request-construction job upstream), or hold lifecycle policy.

## 2. Dependencies

- **Upstream crates (depends on):**
  - `eos-types` — newtype ID `ToolUseId` and `JsonObject` (anchor §5).
    `ToolUseBlock.tool_use_id` uses the `ToolUseId` newtype. `ProviderError.request_id` is **not** an `eos-types`
    `RequestId` (that is the internal `str(uuid.uuid4())` request id): it is the
    provider's opaque HTTP `request-id`/`x-request-id` header (`str | None` in
    `errors.py`), so it stays a plain `Option<String>`.
  - `eos-config` — `RetryConfig` (`max_retries`, `base_delay_s`, `max_delay_s`,
    `status_codes`) drives `retry.rs`; replaces the module-level `MAX_RETRIES /
    BASE_DELAY / MAX_DELAY` constants in `anthropic_native.py` (plan §SRP "Unify
    retry config. Do not keep local retry constants in a provider client").
- **Downstream consumers (used by):** `eos-tools` (authors `ToolSpec`),
  `eos-engine` (constructs `LlmRequest`, consumes `LlmStreamEvent`, owns the
  `EventSource` seam that wraps an `LlmClient`).
- **External crates** (pinned via `[workspace.dependencies]` inheritance,
  `proj-workspace-deps`):

| Crate | Why | rust-skills rule |
|---|---|---|
| `tokio` | async runtime primitives (`time::sleep` for retry backoff); no runtime is created here, only awaited (`&self`/`async fn`) | `async-tokio-runtime` |
| `reqwest` (rustls, `stream`) | direct HTTP POST with streaming body; no provider SDK | plan §6 (NO SDKs) |
| `futures` / `futures-util` | model the response as `impl Stream<Item = Result<LlmStreamEvent, ProviderError>>`; `async-stream` for the generator | `anti-type-erasure` |
| `async-stream` | `try_stream!` to express incremental SSE→event decoding as a `Stream` without a manual `poll_next` state machine | `anti-type-erasure` |
| `bytes` | zero-copy accumulation of SSE byte chunks before frame split | `mem-zero-copy` |
| `serde`, `serde_json` | wire encode/decode of request bodies and SSE `data:` JSON payloads | anchor §3 (Pydantic→serde) |
| `schemars` | `JsonSchema` derive for `ToolSpec` input/output schema authoring downstream | anchor §10 |
| `thiserror` | the single `ProviderError` enum (`err-thiserror-lib`) | `err-thiserror-lib` |
| `async-trait` | `LlmClient` is used behind `Arc<dyn LlmClient>` at the composition root; native async-fn-in-trait is not `dyn`-safe yet (anchor §6) | `async-tokio-runtime` |
| `secrecy` | wrap `Auth` credentials in `SecretString` so they are redacted in `Debug`/logs (plan reliability rule: never dump secrets) | — |
| `tracing` | structured stream-parse logging that never includes tool args/secrets (plan reliability rule) | — |
| `proptest` (dev) | property tests for the SSE frame splitter | `test-proptest-properties` |

## 3. Scope & Source Mapping

| Python source | Rust target | What moves / what is dropped |
|---|---|---|
| `message/message.py` (`TextBlock`, `ToolUseBlock`, `ThinkingBlock`, `ToolResultBlock`, `SystemNotificationBlock`, `Message`, `serialize_content_block`, `parse_assistant_message`) | `message.rs` | Moves as neutral `ContentBlock`/`Message`. `ThinkingBlock`→`ReasoningBlock` (gap closeout). `serialize_content_block`/`parse_assistant_message` are **dropped from the neutral type** and re-expressed as per-provider encode/decode in `anthropic.rs`/`openai.rs` (plan §6 "Move provider projection out of message domain"). `is_terminal` marker stays a neutral field (engine-consumed, never serialized). |
| `message/events.py` (`ThinkingDeltaEvent`, `AssistantTextDeltaEvent`, `AssistantMessageCompleteEvent`, `ToolUseDeltaEvent`, `ToolExecution*`, `BackgroundTaskStartedEvent`, `StreamEvent`) | `events.rs` | Only the four **model-stream** variants move here as `LlmStreamEvent`. `ToolExecution*`, `BackgroundTaskStarted`, `SystemNotification` are **dropped** (they are engine-domain `EventSource` events owned by `eos-engine`). `agent_name`/`agent_run_id` identity fields are dropped (engine stamps those on its own event envelope). |
| `providers/types.py` (`UsageSnapshot`, `MessageRequest`, `SupportsStreamingMessages`) | `types.rs`, `client.rs` | `UsageSnapshot`→`types.rs`; `MessageRequest`→`LlmRequest`; `SupportsStreamingMessages`→`LlmClient` trait in `client.rs`. `total_tokens` becomes a method. |
| `providers/errors.py` (`EphemeralOSApiError` + subclasses) | `error.rs` | Collapsed into one `ProviderError { kind, status_code, request_id, message }` enum carrying a `ProviderErrorKind` (anchor §5). |
| `providers/provider.py` (`make_api_client`, `_resolve_class_path`, `class_path` dispatch) | — | **Dropped.** No importlib/`class_path` dynamic dispatch (anchor §2). Client selection is typed by `llm_provider` at the composition root, not here. |
| `providers/auth_strategy.py` (`AuthStrategy`, `_ApiKeyStrategy`, `_ClaudeOAuthStrategy`, keychain) | `auth.rs` | Only the explicit-credential shape survives as an `Auth` enum (`ApiKey` / `Bearer`). Base-url heuristic and the Keychain OAuth strategy are dropped (gap closeout, anchor §2). |
| `providers/clients/anthropic_native.py` (`AnthropicClient`, retry loop, `_translate_error`, `_is_retryable`, `_stream_once`) | `anthropic.rs`, `retry.rs`, `sse.rs` | SDK stream replaced by `reqwest` POST `/v1/messages` `stream:true` + `sse.rs`. Retry loop→`retry.rs` driven by `RetryConfig`. `_translate_error`→`error.rs` mapping. `_emit_coding_plan_mode_error`, `system_prefix` OAuth identity injection, refresh-on-401 are dropped. |
| `providers/clients/coding_plan/*` | — | **Dropped** (anchor §2). |
| (new — no Python source) | `openai.rs` | `reqwest` POST `/v1/responses` `stream:true`, parse `response.output_text.delta`, function-call argument deltas, `response.completed`; map to the same `LlmStreamEvent` variants. |

**In scope:** neutral types, two concrete clients, SSE parser, retry gating,
explicit auth, error mapping.
**Out of scope:** registries, tool execution, the `EventSource` wrapper,
prompt-report transcript persistence (the `system`-role fix is honored by *not*
representing `system` as a `Message` role — see §10 GC-llm-client-03).

## 4. File & Module Layout

```
eos-llm-client/
  src/
    lib.rs          // pub use re-exports (proj-pub-use-reexport); workspace lints
    types.rs        // UsageSnapshot, LlmRequest, ToolChoice, ToolSpec
    message.rs      // Message, MessageRole, ContentBlock (+ ContentBlock::Reasoning), compat decode map
    events.rs       // LlmStreamEvent (4 normalized variants), StopReason
    error.rs        // ProviderError, ProviderErrorKind
    client.rs       // LlmClient trait (#[async_trait]); LlmStream type alias
    auth.rs         // Auth { ApiKey, Bearer } enum + header application
    sse.rs          // pub(crate) incremental SSE frame splitter (zero-copy)
    retry.rs        // pub(crate) retry gate driven by eos_config::RetryConfig
    anthropic.rs    // AnthropicClient: encode LlmRequest -> /v1/messages, decode SSE
    openai.rs       // OpenAiClient: encode LlmRequest -> /v1/responses, decode SSE
```

`lib.rs` re-exports the owned contracts; `sse.rs`/`retry.rs` are
`pub(crate)` internals (`proj-pub-crate-internal`). Provider encode/decode helpers
are `pub(crate)` inside their modules.

## 5. Contracts Owned Here

Per the Ownership Map (anchor §5) this crate **defines** the following. All are
`#[non_exhaustive]` where they may grow (`api-non-exhaustive`) and derive
`Debug, Clone, PartialEq` (`api-common-traits`).

- **`ToolSpec`** — neutral tool declaration sent to the model. `eos-tools`
  depends on this crate to author it (anchor §5a).

  ```rust
  #[non_exhaustive]
  #[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
  pub struct ToolSpec {
      pub name: String,
      pub description: String,
      pub input_schema: JsonObject,           // serde_json::Value object (schemars-generated upstream)
      pub output_schema: Option<JsonObject>,  // dropped by Anthropic encode, mapped by OpenAI encode
  }
  ```

- **`Message` / `MessageRole` / `ContentBlock`** — provider-neutral conversation
  (see §6).
- **`LlmRequest`** — neutral request (see §6).
- **`UsageSnapshot`** — `{ input_tokens, output_tokens }` with `total_tokens()`.
- **`LlmStreamEvent` / `StopReason`** — the four normalized stream variants.
- **`ProviderError` / `ProviderErrorKind`** — the crate's single `thiserror` enum.
- **`Auth`** — explicit auth kind.
- **`LlmClient` trait** — the seam (DIP + LSP, anchor §6):

  ```rust
  pub type LlmStream =
      Pin<Box<dyn Stream<Item = Result<LlmStreamEvent, ProviderError>> + Send>>;

  #[async_trait::async_trait]
  pub trait LlmClient: Send + Sync {
      /// Open a streaming model invocation. The retry gate (§8) runs **lazily
      /// inside the returned stream generator**, so the outer `Err` is reserved
      /// for synchronous request-construction failures only (URL/header/body
      /// build). All connect, auth, rate-limit, transport, and decode errors —
      /// including a non-retryable failure on the very first attempt — surface
      /// as `Err` **items** of the returned stream, not as the outer `Err`. The
      /// caller observes a single linear stream.
      async fn stream_message(&self, request: LlmRequest) -> Result<LlmStream, ProviderError>;
  }
  ```

  **Object-safety / async note:** `LlmClient` is stored as `Arc<dyn LlmClient>`
  at the `eos-runtime` composition root (heterogeneous: Anthropic, OpenAI,
  mock), so it uses `#[async_trait]` (native async-fn-in-trait is not yet
  `dyn`-safe, anchor §6). The returned `LlmStream` is a boxed `Stream` for the
  same reason. Inside each concrete client, the per-attempt stream body is built
  with `async-stream::try_stream!` (the `stream_once` generator, zero `dyn`
  there); `retry.rs` wraps it in an **outer** `try_stream!` generator
  (`retry_stream`) that re-invokes the per-attempt factory across retries and
  tracks `emitted_visible` (§7). Boxing happens only once, at the `LlmStream`
  alias. Not sealed — `mock` lives in test code and downstream may add a replay
  client behind the same seam.

**Contracts USED (not redefined here):** `ToolUseId`,
`JsonObject` (see impl-eos-types.md); `RetryConfig`
(see impl-eos-config.md §providers). (`ProviderError.request_id` is the provider
HTTP header string, not the internal `RequestId` newtype.)

## 6. Types, Fields & Schemas

All wire/DTO types derive `Serialize, Deserialize` and (where authored to the
model) `JsonSchema`. Newtype IDs come from `eos-types`. Internal `*Wire` structs
used only for serde mapping are `pub(crate)`.

### `MessageRole` (enum, `type-enum-states`)

| Variant | serde rename |
|---|---|
| `User` | `"user"` |
| `Assistant` | `"assistant"` |

Source: `message.py::Message.role = Literal["user","assistant"]`. **No `System`
variant** — system prompt is a request field, not a `Message` (GC-llm-client-03).

### `ContentBlock` (enum, serde `tag = "type"`)

Source: `message.py::ContentBlock` discriminated union. `#[non_exhaustive]`.

| Variant | Fields | serde tag | source-of-truth | notes |
|---|---|---|---|---|
| `Text` | `text: String` | `"text"` | `TextBlock` | |
| `ToolUse` | `tool_use_id: ToolUseId`, `name: String`, `input: JsonObject` | `"tool_use"` | `ToolUseBlock` | id newtype (`type-newtype-ids`); default-generated `toolu_<uuid>` lives in `eos-types`/engine, not here |
| `Reasoning` | `text: String` | `"reasoning"` | `ThinkingBlock` (**renamed**) | GC-llm-client-01; compat decode accepts legacy `"thinking"` |
| `ToolResult` | `tool_use_id: ToolUseId`, `content: String`, `is_error: bool`, `metadata: JsonObject`, `is_terminal: bool` | `"tool_result"` | `ToolResultBlock` | `is_terminal` and `metadata` are normal neutral serde fields (persisted in transcripts/audit, like Python `model_dump`); the **provider encoders omit BOTH `is_terminal` and `metadata`** from the wire body (only `type/tool_use_id/content/is_error` are sent) — not the neutral type; see §8.6 |
| `SystemNotification` | `text: String` | `"system_notification"` | `SystemNotificationBlock` | neutral block; Anthropic encode flattens to a `text` block wrapped in `<system-reminder>…</system-reminder>` |

```rust
#[non_exhaustive]
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ContentBlock {
    Text { text: String },
    ToolUse { tool_use_id: ToolUseId, name: String, input: JsonObject },
    Reasoning { text: String },
    ToolResult {
        tool_use_id: ToolUseId,
        content: String,
        #[serde(default)] is_error: bool,
        #[serde(default)] metadata: JsonObject,
        #[serde(default)] is_terminal: bool,
    },
    SystemNotification { text: String },
}
```

> The `#[serde(...)]` derive above is the **neutral** (transcript/audit)
> representation, NOT the provider wire format. Provider bodies are produced by
> dedicated `*Wire` structs in `anthropic.rs`/`openai.rs` so all projection
> stays in the provider modules (GC-llm-client-02); the neutral derive never
> doubles as the Anthropic wire shape.

### `Message`

| Field | Rust type | source |
|---|---|---|
| `role` | `MessageRole` | `Message.role` |
| `content` | `Vec<ContentBlock>` | `Message.content` |

Helper methods replacing Python `@property`s: `assistant_text() -> String`,
`reasoning_text() -> String`, `tool_uses() -> impl Iterator<Item = &ContentBlock>`
(filtering the `ToolUse` variant), `from_user_text(&str) -> Message`.
(`name-no-get-prefix`.)

### `LlmRequest`

Source: `types.py::MessageRequest`. Built via a `#[must_use]` builder
(`api-builder-pattern`) since several fields are optional.

| Field | Rust type | source | notes |
|---|---|---|---|
| `model` | `String` | `model` | `model_key` upstream; opaque here |
| `messages` | `Vec<Message>` | `messages` | |
| `system_prompt` | `Option<String>` | `system_prompt` | request field, never a `Message` (GC-llm-client-03) |
| `max_tokens` | `u32` | `max_tokens` (default 32768) | |
| `tools` | `Vec<ToolSpec>` | `tools: list[dict]` | now typed; built at agent spawn upstream |
| `tool_choice` | `Option<ToolChoice>` | `tool_choice: dict` | enum, see below |

### `ToolChoice` (enum replacing the raw `dict`, `type-no-stringly`)

| Variant | meaning |
|---|---|
| `Auto` | model decides |
| `Any` | must call some tool |
| `Tool { name: String }` | force a named tool |

### `UsageSnapshot`

| Field | Rust type | source |
|---|---|---|
| `input_tokens` | `u32` | `input_tokens` |
| `output_tokens` | `u32` | `output_tokens` |

`pub fn total_tokens(&self) -> u32 { self.input_tokens + self.output_tokens }`
(replaces the `total_tokens` property). Derives `Default` (`api-default-impl`).

### `LlmStreamEvent` (enum) + `StopReason`

The **only** normalized stream variants (plan §6 lists exactly these four).
`#[non_exhaustive]`.

| Variant | Fields | source event | notes |
|---|---|---|---|
| `AssistantTextDelta` | `text: String` | `AssistantTextDeltaEvent` | the first such delta is "visible output" for the retry gate (§8) |
| `ReasoningDelta` | `text: String` | `ThinkingDeltaEvent` (**renamed**) | GC-llm-client-01 |
| `ToolUseDelta` | `tool_use_id: ToolUseId`, `name: String`, `input: JsonObject` | `ToolUseDeltaEvent` | emitted at `content_block_stop` with fully-assembled args; visible output |
| `AssistantMessageComplete` | `message: Message`, `usage: UsageSnapshot`, `stop_reason: Option<StopReason>` | `AssistantMessageCompleteEvent` | terminal stream event; `agent_name`/`agent_run_id` dropped |

`StopReason` is a parsed enum (`EndTurn`, `MaxTokens`, `ToolUse`, `StopSequence`,
`Other(String)`) over the provider's `stop_reason` string (`api-parse-dont-validate`).

### `ProviderError` / `ProviderErrorKind`

Source: `errors.py` (`EphemeralOSApiError` + subclasses) and
`anthropic_native.py::_translate_error`/`_categorize`. One `thiserror` enum
carrying a kind (anchor §5, §8).

| Field | Rust type | source |
|---|---|---|
| `kind` | `ProviderErrorKind` | derived from status (`_translate_error`) |
| `status_code` | `Option<u16>` | `EphemeralOSApiError.status_code` |
| `request_id` | `Option<String>` | `EphemeralOSApiError.request_id` (provider HTTP `request-id` header, not the internal `RequestId`) |
| `message` | `String` | exception message |

```rust
#[non_exhaustive]
#[derive(Debug, Clone, PartialEq)]
pub enum ProviderErrorKind {
    Authentication, // 401/403  (AuthenticationFailure)
    RateLimit,      // 429       (RateLimitFailure)
    Server,         // 500/502/503/529
    Request,        // other HTTP / generic (RequestFailure)
    Transport,      // reqwest connect/timeout — None status
    Decode,         // SSE/JSON parse failure — None status
}

#[derive(Debug, Clone, PartialEq, thiserror::Error)]
#[error("{kind:?} provider error (status {status_code:?}, request {request_id:?}): {message}")]
#[non_exhaustive]
pub struct ProviderError {
    pub kind: ProviderErrorKind,
    pub status_code: Option<u16>,
    pub request_id: Option<String>,
    pub message: String,
}
```

Message is lowercase, no trailing punctuation (`err-lowercase-msg`). The
status→kind mapping combines `_translate_error` (401/403→`Authentication`,
429→`RateLimit`, all else→`Request`) with `_categorize`'s 5xx grouping
({500,502,503,529}→`Server`); `Transport` and `Decode` are **new** kinds for the
SDK-free path with no Python source. This is an **improvement**, not strict
parity: `_categorize`'s `content_filter_rejection` case (derived BY
message-substring matching on `content_filter`/`policy`) is intentionally
dropped along with the coding-plan logging, so `kind` lets callers branch
without string matching — new behavior, not preserved behavior.

### `Auth` (enum, explicit kind — replaces base-url heuristic)

| Variant | Header applied | source |
|---|---|---|
| `ApiKey(SecretString)` | `x-api-key: <key>` (Anthropic) | `_ApiKeyStrategy` default |
| `Bearer(SecretString)` | `Authorization: Bearer <key>` (OpenAI / non-Anthropic) | `use_auth_token` branch |

GC-llm-client-04: the choice is passed in explicitly by the caller; this crate
does **not** sniff `base_url`. Credentials are held in `secrecy::SecretString`
so `Debug` is redacted and they never log. The Anthropic client additionally
always sets the mandatory `anthropic-version` header; OpenAI uses
`Authorization: Bearer`.

## 7. Concurrency & State Ownership

Per anchor §7:

- **Runtime:** none created here. Methods are `async fn`/`&self`; the single
  Tokio multi-thread runtime is owned by `eos-runtime` (`async-tokio-runtime`).
  Retry backoff uses `tokio::time::sleep`.
- **Client state:** each concrete client (`AnthropicClient`, `OpenAiClient`)
  holds an owned `reqwest::Client` (internally `Arc`, cheap to clone), a
  `base_url: Url`, an `Auth`, and an `Arc<RetryConfig>` snapshot (shared
  immutable, `own-arc-shared`). All fields are immutable after construction — no
  locks, no interior mutability. There is **no refresh-on-401 token mutation**
  (dropped with the OAuth strategy), so no `Mutex`/`RwLock` is needed and the
  "never hold a lock across `.await`" rule is satisfied vacuously
  (`anti-lock-across-await`).
- **Streaming:** the response is modeled as
  `impl Stream<Item = Result<LlmStreamEvent, ProviderError>>`, produced by
  `async-stream::try_stream!`. The `reqwest::Response` body is consumed as a
  `Stream<Item = Result<Bytes, reqwest::Error>>`; `sse.rs` accumulates a
  `BytesMut`/`Vec<u8>` carry-over buffer across chunks and splits complete
  frames on the SSE blank-line boundary (incremental, no full-body buffering;
  zero-copy slicing where a frame lies within one chunk — `mem-zero-copy`).
  The stream is owned by the caller; no channel needed (single producer →
  single consumer linear pull). No `mpsc`/bounded channel is required for the
  happy path; backpressure is the consumer's pull rate over the `reqwest` body
  (`async-bounded-channel` rationale — we avoid an unbounded intermediate
  buffer by never `collect()`-ing the body).
- **Retry gate (`retry.rs`):** purely sequential within the single stream
  future. `retry.rs` exports
  `retry_stream(cfg: &RetryConfig, factory: impl FnMut() -> BoxFuture<Result<LlmStream, ProviderError>>) -> LlmStream`:
  an **outer** `try_stream!` generator that re-invokes the per-attempt
  `stream_once` factory across retries. A `bool emitted_visible` flag lives in
  the `retry_stream` generator frame; once a visible event
  (`AssistantTextDelta` / `ReasoningDelta` / `ToolUseDelta`) is forwarded, the
  flag is set and on any later error the loop forwards the error verbatim and
  does **not** re-invoke the factory. While `!emitted_visible`, a retryable
  error (§8) re-invokes the factory after backoff. No shared mutable state
  crosses tasks.
- **CPU-bound work:** none. SSE parsing is light; no `spawn_blocking`.

## 8. Behavior & Invariants

Cite plan §6 and §"API Client Layer" reliability rules.

1. **Retry only before any visible event (the central invariant).**
   `anthropic_native.py::stream_message` gates retries on `emitted_any`. In Rust
   the gate is `emitted_visible`, tracked by `retry_stream` (§7): a fresh attempt
   (re-invoking the `stream_once` factory) is allowed only while no
   `AssistantTextDelta`/`ReasoningDelta`/`ToolUseDelta` has been yielded. Once
   visible output is forwarded, any subsequent failure **fails fast** as an `Err`
   stream item, with no factory re-invocation (re-running
   would duplicate text deltas and double-dispatch `tool_use_id`s downstream —
   plan §"API Client Layer"). `AssistantMessageComplete` is the success terminus.
   Retryable only when `err.kind` ∈ {`RateLimit`, `Server`, `Transport`} **and**
   `status_code` ∈ `RetryConfig.status_codes` (for HTTP) **and** attempt count
   `< RetryConfig.max_retries`. Backoff = `min(base_delay_s * 2^attempt,
   max_delay_s)`. The Python refresh-on-401 retry is **dropped** (no OAuth
   strategy).

2. **Visible-output definition is explicit and total** over `LlmStreamEvent`:
   the three delta variants are visible; `AssistantMessageComplete` is the
   normal end. Tested by AC-llm-client-02.

3. **Provider projection lives only in provider modules** (plan §6 "Move
   provider projection out of message domain"):
   - Anthropic encode: `Message` → `/v1/messages` params; **drop `Reasoning`
     blocks** from the outgoing `messages` array (Anthropic manages reasoning
     internally — mirrors `to_api_param` excluding thinking); **drop
     `ToolSpec.output_schema`** (`_stream_once` strips `output_schema`); flatten
     `SystemNotification` to a `<system-reminder>`-wrapped `text` block
     (`serialize_content_block`).
   - OpenAI encode: map `ToolSpec` to Responses-API function tool entries,
     mapping `output_schema` when present; normalize
     `response.output_text.delta` → `AssistantTextDelta` and function-call
     argument deltas → a single `ToolUseDelta` per call at completion of its
     arguments.

4. **Anthropic SSE decode** parses `message_start`, `content_block_start`,
   `content_block_delta` (`text_delta`→`AssistantTextDelta`,
   `thinking_delta`→`ReasoningDelta`, `input_json_delta` accumulated),
   `content_block_stop` (for a `tool_use` block, emit `ToolUseDelta` with
   parsed args — **mid-stream**, matching the Python advantage), `message_delta`
   (stop_reason/usage), `message_stop` (emit `AssistantMessageComplete`).
   A malformed tool-args JSON yields `input = {}` (Python parity, not an error).

5. **OpenAI SSE decode** parses `response.output_text.delta`,
   function/tool-call argument delta events, and `response.completed` →
   `AssistantMessageComplete` with `UsageSnapshot` from the completion payload.

6. **`is_terminal` and `metadata` are engine-internal but neutrally-serialized**:
   both remain normal fields on the neutral `ToolResult` block (present in
   transcript/audit serialization, matching Python `model_dump`), and the
   **provider encoders omit BOTH `is_terminal` and `metadata`** from the outgoing
   wire body — `serialize_content_block` emits only
   `{type, tool_use_id, content, is_error}` for a `tool_result`, so leaving
   `metadata` in the Anthropic wire body would be rejected (400). Projection
   lives in the provider modules, not in a `#[serde(skip)]` on the neutral type —
   preserving both the "wire-irrelevant" comment and GC-llm-client-02.

7. **Logging discipline:** stream parse errors log via `tracing` with frame
   index/event-type context but **never** tool-call `input` JSON, `system_prompt`
   text, message content, or `Auth` material (plan reliability rule;
   `Auth`/`SecretString` `Debug` is redacted). Tested by AC-llm-client-05.

8. **Request id + status preserved** end-to-end into `ProviderError`
   (`_translate_error` parity; plan reliability rule). The Anthropic
   `request-id` response header / OpenAI `x-request-id` is captured even on the
   streaming error path.

Subtle risks called out by the plan: (a) duplicate deltas on naive retry — fixed
by the visible-output gate; (b) silent loss of `request_id` on the stream path —
fixed by capturing the header before consuming the body; (c) leaking secrets in
parse logs — fixed by redacted `Debug` + content-free log fields.

## 9. SOLID & Principles Applied

- **DIP + LSP** — `LlmClient` is the seam (anchor §6). `eos-engine` depends on
  the trait; `AnthropicClient`, `OpenAiClient`, and a test `MockLlmClient` are
  substitutable because every event is a neutral `LlmStreamEvent`. Wiring
  happens at `eos-runtime` (`Arc<dyn LlmClient>`).
- **OCP** — adding a provider = adding one module implementing `LlmClient` and
  registering it at the composition root; no `match` over provider strings in a
  shared dispatch path (the `class_path` importlib dispatch is removed).
- **SRP** — this crate only owns the neutral vocabulary + wire I/O. Tool
  registry, execution, event-fan-out, and lifecycle stay elsewhere.
- **ISP** — `LlmClient` has exactly one method (`stream_message`); no god-client.
- **Anti-type-erasure** — concrete clients return `impl Stream` internally via
  `try_stream!`; `Box`/`dyn` appears only at the `Arc<dyn LlmClient>` composition
  seam where heterogeneous storage genuinely requires it (`anti-type-erasure`,
  anchor §6).
- **KISS/YAGNI/DRY** — no provider-capability matrix, no streaming-vs-blocking
  toggle, no pluggable retry policy beyond `RetryConfig`. `LlmStreamEvent` holds
  exactly the four variants the plan names; engine events are not duplicated here
  (DRY across crates). Retry constants are not re-declared (single source =
  `RetryConfig`).
- **Non-goals respected:** no SDKs, no `class_path` dynamic import, no coding-plan
  clients, no Keychain OAuth, no base-url auth heuristic (anchor §2, plan §6).

## 10. Gap Closeouts (tracked requirements)

| ID | Requirement | Resolution |
|---|---|---|
| GC-llm-client-01 | Rename provider-neutral `Thinking*` → `Reasoning*` with a compat decode map while old JSONL transcripts exist. | `ContentBlock::Reasoning` / `LlmStreamEvent::ReasoningDelta`. A `pub(crate)` serde decode helper maps legacy `"thinking"` block type and the Anthropic `thinking_delta` event into the `Reasoning*` shape; encode always emits the new names. Proven by AC-llm-client-03. |
| GC-llm-client-02 | Move provider projection out of the message domain; Anthropic drops `output_schema`, OpenAI maps it when supported. | All encode/decode lives in `anthropic.rs`/`openai.rs`; `message.rs`/`types.rs` carry no provider serialization. Anthropic encode strips `ToolSpec.output_schema` and drops `Reasoning` blocks; OpenAI encode maps `output_schema`. Proven by AC-llm-client-06. |
| GC-llm-client-03 | Fix transcript mismatch where prompt-report JSONL can record a `system` role while `Message` only supports `user`/`assistant`. | `MessageRole` has only `User`/`Assistant`; the system prompt is the `LlmRequest.system_prompt` field, never a `Message`. There is no decode path that yields a `system`-role `Message`. Proven by AC-llm-client-07. |
| GC-llm-client-04 | Replace base-url auth heuristics with explicit auth kind. | `Auth { ApiKey, Bearer }` passed in by the caller; clients apply the matching header. No `base_url` inspection chooses the scheme. Proven by AC-llm-client-08. |
| GC-llm-client-05 | Do not port coding-plan provider clients. | `providers/clients/coding_plan/*`, `_ClaudeOAuthStrategy`, `class_path` importlib dispatch, and `_emit_coding_plan_mode_error` are absent from the crate. Enforced by absence + AC-llm-client-09 (no `coding_plan`/`class_path`/`keychain` symbol in the crate). |

## 11. Acceptance Criteria

TDD: write each test first, confirm it fails for the right reason, then
implement. Maps to anchor §11 "Tests to Port First": *Anthropic + OpenAI SSE
fixtures; retry-after-visible-output; error mapping (request id + status)*.

| ID | Assertion | Proving test (write first) |
|---|---|---|
| AC-llm-client-01 | Replaying a captured Anthropic `/v1/messages` SSE fixture yields, in order, `AssistantTextDelta`/`ReasoningDelta` then a mid-stream `ToolUseDelta` (parsed args) then `AssistantMessageComplete` with correct `UsageSnapshot` and `StopReason`. | `anthropic.rs` `#[tokio::test] fn decodes_anthropic_sse_fixture` (fixture under `tests/fixtures/anthropic/`). |
| AC-llm-client-02 | The outer `Result` is `Ok` in all streaming cases (errors are `Err` stream items). Fail-fast: after a visible delta, an injected transport error yields the delta then exactly **one** `Err` item and ends, factory invoked **once**. Retry-then-succeed: before any visible delta a `RateLimit`/`Server` error re-invokes the factory up to `max_retries` times internally, and the caller sees a clean delta stream with no `Err` item. | `retry.rs` `#[tokio::test] fn retries_only_before_visible_output` using a mock `stream_once` factory. |
| AC-llm-client-03 | Decoding a legacy JSONL transcript with a `"thinking"` block and an Anthropic `thinking_delta` event produces `ContentBlock::Reasoning` / `LlmStreamEvent::ReasoningDelta`; encode never emits `"thinking"`. | `message.rs` `#[test] fn reasoning_compat_decode_maps_thinking`. |
| AC-llm-client-04 | status→kind mapping: 401/403→`Authentication`, 429→`RateLimit`, {500,502,503,529}→`Server`, other HTTP→`Request`, connect/timeout→`Transport`; `status_code` and `request_id` are preserved from the response. | `error.rs` `#[test] fn maps_status_to_kind_preserving_request_id`. |
| AC-llm-client-05 | A forced SSE parse failure logs a `tracing` event whose fields contain no tool `input` JSON, no `system_prompt`, and no auth material. | `sse.rs` `#[test] fn parse_error_log_omits_secrets` (capture via `tracing-test`). |
| AC-llm-client-06 | Anthropic encode of an `LlmRequest` drops `ToolSpec.output_schema` and drops `Reasoning` content blocks from the outgoing `messages`; OpenAI encode retains `output_schema` in the function tool entry. | `anthropic.rs`/`openai.rs` `#[test] fn encode_projects_tools_per_provider`. |
| AC-llm-client-07 | `MessageRole` deserialization rejects `"system"`; a system prompt round-trips only through `LlmRequest.system_prompt`. | `message.rs` `#[test] fn message_role_has_no_system`. |
| AC-llm-client-08 | `Auth::ApiKey` applies `x-api-key`; `Auth::Bearer` applies `Authorization: Bearer`; no code path reads `base_url` to pick the scheme. | `auth.rs` `#[test] fn auth_kind_sets_expected_header`. |
| AC-llm-client-09 | The crate source contains no `coding_plan`, `class_path`, or `keychain` symbols and no provider-SDK dependency in `Cargo.toml`. | `tests/no_legacy_surface.rs` integration grep test. |
| AC-llm-client-10 | An OpenAI `/v1/responses` SSE fixture (text delta + function-call argument deltas + `response.completed`) decodes into the same `LlmStreamEvent` variant sequence as the Anthropic path (LSP substitutability). | `openai.rs` `#[tokio::test] fn decodes_openai_responses_fixture`. |
| AC-llm-client-11 | Property test: the `sse.rs` frame splitter, fed arbitrary chunk boundaries of a valid multi-frame byte buffer, reconstructs the identical ordered list of frames. | `sse.rs` `proptest! fn frame_split_is_boundary_invariant` (`test-proptest-properties`). |

## 12. Implementation Checklist

Ordered, small, verifiable steps (`small-incremental-changes`); each step lands
its failing test first.

1. Scaffold crate + workspace lints in `lib.rs`; add inherited deps (§2).
2. `error.rs`: `ProviderError`/`ProviderErrorKind` + status→kind mapping →
   AC-llm-client-04.
3. `types.rs`: `UsageSnapshot`, `ToolSpec`, `ToolChoice`, `LlmRequest` (+ builder).
4. `message.rs`: `MessageRole`, `ContentBlock` (with `Reasoning`), `Message`,
   helpers, compat decode map → AC-llm-client-03, 07.
5. `events.rs`: `LlmStreamEvent` (4 variants), `StopReason`.
6. `auth.rs`: `Auth` enum + header application + redacted `Debug` → AC-llm-client-08.
7. `sse.rs`: incremental zero-copy frame splitter → AC-llm-client-11, 05.
8. `client.rs`: `LlmClient` trait + `LlmStream` alias + `MockLlmClient` (test).
9. `retry.rs`: `retry_stream(cfg, factory) -> LlmStream` visible-output retry gate
   over `RetryConfig` (generic over the per-attempt `stream_once` factory, so it
   lands before the provider modules) → AC-llm-client-02.
10. `anthropic.rs`: encode `/v1/messages`, decode SSE, wire retry →
    AC-llm-client-01, 06.
11. `openai.rs`: encode `/v1/responses`, decode SSE → AC-llm-client-10, 06.
12. `tests/no_legacy_surface.rs` → AC-llm-client-09.
13. `cargo fmt --check` + `clippy -D warnings`; confirm all AC tests green.

---
**On completion:** update the Progress Tracker in `./overview.md` for row
`eos-llm-client` per spec-conventions.md §13. Do not edit other crates' rows.
