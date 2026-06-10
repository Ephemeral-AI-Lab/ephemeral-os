# EOS Agent Core Rust to TypeScript Migration - Phase 02.5 Provider Composition and Live E2E

Status: Proposed
Date: 2026-06-10
Owner: eos-agent-core
Migration direction: Rust -> TypeScript
Project path: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core`
Depends on: `phase-02-llm-client_SPEC.md` (Completed)
Rust semantics reference: `agent-core/crates/eos-llm-client/src/auth.rs`,
`src/clients/claude_coding_plan.rs`, `src/clients/codex_coding_plan.rs`,
`src/clients/{anthropic_api_client,openai_api_client}.rs` (coding-plan
constructor paths)

## 1. Intent

Phase 02 shipped two vendor-named client classes. That shape conflates three
independent axes — vendor, wire protocol, credential scheme — and fails the
extension test: a provider like GitHub Copilot (GitHub vendor, OpenAI-ish
chat-completions wire, token-exchange auth, multi-vendor models behind one
URL) has no honest home in a `providers/<vendor>/` taxonomy.

Phase 02.5 recomposes the package around the two real axes and adds the live
end-to-end harness:

- **`wires/`** — protocol codecs (request encode + stream decode + SDK call).
  A wire knows nothing about vendors or credentials.
- **`access/`** — credential schemes (base URL + auth + per-attempt
  headers). An access scheme knows nothing about wire protocols.
- **`stream-client.ts`** — one generic `LlmStreamClient` composing
  wire x access with the existing retry gate and idle guard.
- **`profiles.ts`** — the only vendor-aware file: a registry of named
  profiles binding `{ connection schema, wire, wire options, access,
  default base_url }`.
- **`e2e/`** — live provider tests, separate from unit tests, that replay
  the unit-mocked scenarios against real endpoints; the Codex coding-plan
  suite loads credentials automatically from the local Codex CLI cache
  (`~/.codex/auth.json`).

Design rules of this phase:

1. `wires/*` never imports `access/*` and vice versa; they meet only in
   `stream-client.ts`/`factory.ts`. Vendor-specific encode quirks travel as
   **data** (`WireOptions` on the profile), never as subclasses.
2. The model key stays on `LlmRequest.model` (per request). A connection
   carries only *where* (`base_url`) and *as-whom* (credentials), so one
   connection serves every model its endpoint hosts.
3. "Compatible endpoint" support is the `api` access scheme with a custom
   `base_url` — the codecs do not change per endpoint.
4. The `LlmClient` interface, the 4-variant event union, `ProviderError`,
   the retry gate, and the idle guard are behaviorally unchanged from
   Phase 02. Codec bodies move verbatim.

This phase is additive against `agent-core/` (the Rust tree is untouched)
and restructuring within `eos-agent-core/packages/llm-client` only.

## 2. Scope

In scope:

- `wires/wire.ts` contracts plus the two codecs extracted verbatim from
  `providers/anthropic.ts` / `providers/openai.ts`,
- `access/access.ts` contract plus three schemes: `api-key.ts`,
  `claude-coding-plan.ts`, `codex-coding-plan.ts` (including the ported
  `codex_access_token_from_jwt` claim parsing),
- `stream-client.ts` (absorbs `providers/attempt.ts`), `profiles.ts`,
  `factory.ts` (`createLlmClient`),
- removal of the `AnthropicApiClient` / `OpenAiResponsesClient` classes
  (zero consumers outside this package's tests; the factory replaces them),
- the shared client-contract kit
  (`tests/contract/llm-client-contract.ts`): the iteration-contract
  scenarios written once over the bare `LlmClient` interface and consumed
  by **both** the unit fixture bindings (with golden `exact` pinning) and
  the live suite (structural mode) — §6.3,
- the live e2e workspace: `vitest.e2e.config.ts`, `test:e2e` script,
  `packages/llm-client/e2e/` with the Codex auth loader and
  `codex-coding-plan.e2e.ts` — the **only** live suite this phase,
- migration `index.md` row.

Out of scope (named seams, additive later):

- `wires/openai-chat.ts` (Chat Completions codec — the first
  chat-completions consumer pays for it; most third-party
  "OpenAI-compatible" gateways speak this, the Responses wire covers
  first-party/Azure/LiteLLM/vLLM),
- `access/copilot.ts` (GitHub token exchange + expiry cache; the
  per-attempt `headers()` hook in the Access contract is the seam it plugs
  into),
- live suites for `anthropic_api`, `openai_api`, and `claude_coding_plan` —
  no credentials for those approaches are available today. The contract kit
  stays profile-agnostic so each future suite is one env-gated binding file
  (§6.4); none is created in this phase,
- OAuth refresh for either coding plan (the e2e loader detects an expired
  token and skips with an actionable message; refreshing is the Codex/Claude
  CLI's job),
- the runtime `ProvidersConfig` envelope with per-provider model
  registrations (`models:` sections) — a later runtime phase pairs a
  connection with model defaults; this phase's connection objects are the
  factory input only,
- reasoning replay, engine/loop, persistence (unchanged Phase 02 seams),
- any edit under `agent-core/`.

## 3. Resulting File and Folder Structure

```
packages/llm-client/
├── package.json
├── src/
│   ├── client.ts                      # LlmClient, LlmStreamOptions            (unchanged)
│   ├── types.ts                       # LlmRequest, UsageSnapshot, ...         (unchanged)
│   ├── events.ts                      # LlmStreamEvent, StopReason             (unchanged)
│   ├── errors.ts                      # ProviderError + sdk mapping            (unchanged)
│   ├── secret.ts                      # SecretString                           (unchanged)
│   ├── retry.ts                       # visible-output retry gate              (unchanged)
│   ├── config.ts                      # cross-cutting only: RetryConfig,
│   │                                  #   StreamGuardConfig, ProviderClientOptions
│   ├── stream-client.ts               # LlmStreamClient: access.headers() per attempt
│   │                                  #   -> wire.open -> idle guard -> decoder -> retryStream
│   │                                  #   (absorbs providers/attempt.ts)
│   ├── wires/
│   │   ├── wire.ts                    # Wire/WireFactory + StreamDecoder + WireOptions,
│   │   │                              #   parseToolArgs
│   │   ├── anthropic-messages.ts      # Messages encode (+systemPrefix) + decoder + sdk open
│   │   └── openai-responses.ts        # Responses encode (+dialect) + decoder + sdk open
│   ├── access/
│   │   ├── access.ts                  # Access contract: baseUrl, credential, headers()
│   │   ├── api-key.ts                 # static key (first-party or compatible url)
│   │   ├── claude-coding-plan.ts      # oauth bearer + anthropic-beta/identity headers
│   │   └── codex-coding-plan.ts       # jwt claim parse -> chatgpt-account-id (+fedramp)
│   ├── profiles.ts                    # the only vendor-aware file: provider id ->
│   │                                  #   { connection schema, wire, wireOptions, access,
│   │                                  #     default base_url }
│   ├── factory.ts                     # createLlmClient(connection, options) -> LlmClient
│   └── index.ts                       # factory, connection types, contracts re-exports
├── tests/                             # unit tests: no network, run by `pnpm run check`
│   ├── support.ts                     # fetch doubles, collect helpers         (unchanged)
│   ├── contract/
│   │   └── llm-client-contract.ts     # the base: describeLlmClientContract(binding) —
│   │                                  #   iteration-contract scenarios + structural
│   │                                  #   assertions over the bare LlmClient; optional
│   │                                  #   `exact` block for golden pinning (§6.3)
│   ├── contract.test.ts               # unit bindings: fixture-fetch clients for both
│   │                                  #   wires with `exact` golden values (absorbs the
│   │                                  #   happy-path decode tests)
│   ├── retry.test.ts / errors.test.ts / secret-config.test.ts        (unchanged)
│   ├── wires/
│   │   ├── anthropic-messages.test.ts # encode column + failure modes    (moved; happy
│   │   └── openai-responses.test.ts   #   path lives in contract.test.ts)
│   ├── access/
│   │   └── coding-plan.test.ts        # header shapes, jwt claims, fedramp flag       (new)
│   ├── factory.test.ts                # profile selection, compatible-url override,
│   │                                  #   plan-fixture substitutability                (new)
│   └── fixtures/{anthropic,openai}/*.sse                              (unchanged)
└── e2e/                               # live tests: network + real credentials,
    │                                  #   excluded from `pnpm run check`
    ├── support/
    │   └── codex-auth.ts              # ~/.codex/auth.json loader (§6.2)
    └── codex-coding-plan.e2e.ts       # the only live suite this phase; binds the same
                                       #   contract kit to the live codex profile
                                       #   (structural mode, no `exact`); auto-loads
                                       #   laptop credentials (others deferred, §6.4)

eos-agent-core/ (root)
├── vitest.config.ts                   # unchanged (unit tests)
├── vitest.e2e.config.ts               # include packages/*/e2e/**/*.e2e.ts, long timeouts,
│                                      #   no file parallelism
└── package.json                       # + "test:e2e": "vitest run --config vitest.e2e.config.ts"
                                       #   ("check" unchanged: typecheck + lint + unit tests)
```

Phase 02 file mapping (moves, not rewrites):

| Phase 02 file | Becomes | Change |
| --- | --- | --- |
| `providers/anthropic.ts` | `wires/anthropic-messages.ts` + one `profiles.ts` entry | split; encode/decoder bodies verbatim |
| `providers/openai.ts` | `wires/openai-responses.ts` + one `profiles.ts` entry | split; bodies verbatim |
| `providers/attempt.ts` | runner + idle guard -> `stream-client.ts`; `StreamDecoder`/`parseToolArgs` -> `wires/wire.ts` | relocation |
| `AnthropicApiConfigSchema`/`OpenAiApiConfigSchema` in `config.ts` | per-profile connection schemas in `profiles.ts` | relocation |
| `AnthropicApiClient`/`OpenAiResponsesClient` classes | `createLlmClient` + profiles | removed (no consumers; Phase 03 is unstarted and specs against `MockLlmClient`) |

## 4. Owned Contracts

### 4.1 Wire contract (`wires/wire.ts`)

```ts
interface WireOptions {
  /** Identity text prepended as the first system block (claude coding plan). */
  systemPrefix?: string;
  /** Request-body dialect for the responses wire. */
  dialect?: "public" | "codex";
}

interface WireTransport {
  baseUrl: string;
  credential: AccessCredential;            // from the access scheme (§4.2)
  headers(): Promise<Record<string, string>>; // per-attempt extra headers
  fetch?: typeof globalThis.fetch;          // injectable for unit tests
}

interface Wire {
  open(
    request: LlmRequest,
    options: WireOptions,
    signal: AbortSignal,
  ): Promise<{ stream: AsyncIterable<unknown>; requestId?: string }>;
  decoder(requestId: string | undefined): StreamDecoder<unknown>;
}

type WireFactory = (transport: WireTransport) => Wire;
```

- Each wire module exports its `WireFactory` plus the **pure encode
  function** (`encodeAnthropicRequest(request, options)`,
  `encodeOpenAiRequest(request, options)`) so the Phase 02 encode-projection
  tests keep testing without HTTP.
- The factory constructs its official SDK client once per connection
  (`maxRetries: 0`, `logLevel: "off"`, explicit credentials, injected
  `fetch`), mapping the credential onto SDK options: Anthropic wire maps
  `api_key -> apiKey` (x-api-key) and `bearer -> authToken`; Responses wire
  maps both kinds onto `apiKey` (Authorization: Bearer). `headers()` output
  is passed as per-request header options on each attempt.
- `systemPrefix` encode (Anthropic wire): `system` becomes
  `[{type:"text",text:prefix},{type:"text",text:request.system_prompt?}]`
  (the Rust `encode_anthropic_body_with_options(_, true)` shape).
- `dialect: "codex"` encode (Responses wire): omit `max_output_tokens`;
  forced `{ tool }` tool_choice clamps to `"required"`. Everything else
  matches the Phase 02 §5 column.

### 4.2 Access contract (`access/access.ts`)

```ts
interface AccessCredential {
  kind: "api_key" | "bearer";
  secret: SecretString;
}

interface Access {
  baseUrl: string;
  credential: AccessCredential;
  /** Called once per attempt; static schemes return a constant. */
  headers(): Promise<Record<string, string>>;
}
```

`headers()` is deliberately async and per-attempt: it is the seam a future
`access/copilot.ts` (token exchange + expiry cache) plugs into without
touching the client, wires, or retry gate. The three schemes of this phase:

| Scheme | Credential | `headers()` | Construction-time logic |
| --- | --- | --- | --- |
| `api-key.ts` | `api_key` | `{}` | none |
| `claude-coding-plan.ts` | `bearer` (oauth token) | `anthropic-beta: claude-code-20250219,oauth-2025-04-20`, `anthropic-dangerous-direct-browser-access: true`, `user-agent: claude-cli/2.1.75`, `x-app: cli` | none |
| `codex-coding-plan.ts` | `bearer` (access token) | `chatgpt-account-id: <claim>` plus `x-openai-fedramp: true` when the claim is set | parse the JWT (§4.4); failures are `ProviderError` kind `request` |

### 4.3 Profiles, connections, factory (`profiles.ts`, `factory.ts`)

The connection is a `provider`-discriminated union; each profile owns its
Zod schema (secrets wrapped via the Phase 02 `secretString` transform,
`base_url` defaulted per profile):

```ts
type ProviderConnection =
  | { provider: "anthropic_api";      base_url?: string; api_key: string | SecretString }
  | { provider: "openai_api";         base_url?: string; api_key: string | SecretString }
  | { provider: "claude_coding_plan"; base_url?: string; access_token: string | SecretString }
  | { provider: "codex_coding_plan";  base_url?: string; access_token: string | SecretString };

function createLlmClient(
  connection: ProviderConnection,
  options?: ProviderClientOptions,   // { retry?, streamGuard?, fetch? } — unchanged
): LlmClient;
```

Normative profile table (the §5 compatibility table of this phase):

| Profile | Wire | Wire options | Access | Default `base_url` |
| --- | --- | --- | --- | --- |
| `anthropic_api` | `anthropic-messages` | — | `api-key` | `https://api.anthropic.com` |
| `openai_api` | `openai-responses` | `dialect: "public"` | `api-key` | `https://api.openai.com/v1` |
| `claude_coding_plan` | `anthropic-messages` | `systemPrefix: "You are Claude Code, Anthropic's official CLI for Claude."` | `claude-coding-plan` | `https://api.anthropic.com` |
| `codex_coding_plan` | `openai-responses` | `dialect: "codex"` | `codex-coding-plan` | `https://chatgpt.com/backend-api/codex` |

A custom `base_url` on the `api` profiles is the compatible-endpoint path
(gateways, proxies, self-hosted). `profiles.ts` is the only file that may
name vendors; adding a provider is one access module and/or one wire module
plus one registry entry.

### 4.4 Codex access-token claims (port of `auth.rs::codex_access_token_from_jwt`)

- Token shape: JWT; payload is the second `.`-separated segment, base64url
  (no padding), JSON.
- Required claim: `"https://api.openai.com/auth"` object with non-blank
  `chatgpt_account_id`. Optional boolean `chatgpt_account_is_fedramp`
  (default false) drives the `x-openai-fedramp: true` header.
- Failures (not JWT-shaped, payload not base64url/JSON, missing claim or
  account id) throw `ProviderError` kind `request` with the Rust crate's
  lowercase messages.
- No dependency is added: decoding is `Buffer.from(payload, "base64url")` +
  `JSON.parse`.

### 4.5 Behavior preservation

- `LlmStreamClient` implements the Phase 02 §4.5 iteration contract
  verbatim (single pass, exactly one terminus, truncated-stream decode,
  abort rethrown as-is, retry gate semantics, idle guard).
- Every Phase 02 assertion survives. Happy-path golden decode assertions
  relocate into the contract kit's unit bindings (`exact` mode, §6.3);
  encode-projection, retry, error-mapping, truncation, idle, and
  `maxRetries: 0` tests survive as **moves** (import paths only). No
  assertion is deleted or weakened.
- New substitutability proofs in `factory.test.ts`:
  - `claude_coding_plan` replaying the Anthropic fixtures yields the
    identical event sequence as `anthropic_api`,
  - `codex_coding_plan` replaying the OpenAI fixture yields the identical
    event sequence as `openai_api`,
  - encode deltas between plan and api profiles are exactly the §4.1 wire
    options (system prefix shape; codex dialect fields) and nothing else.

## 5. Validation Strategy

| Surface | Mechanism |
| --- | --- |
| Connection union (external input) | Zod, `provider`-discriminated, per-profile defaults; secrets wrapped at parse |
| Codex JWT claims (external input) | parse-don't-validate into a typed claims object; failures -> `ProviderError` `request` |
| Wire/access composition (in-process) | plain TS interfaces; no runtime validation |
| Live responses (e2e) | structural assertions on the normalized event union (§6.3), never on model prose |

## 6. Live E2E Harness

### 6.1 Separation from unit tests

| Property | Unit (`tests/`) | Live e2e (`e2e/`) |
| --- | --- | --- |
| File pattern | `*.test.ts` | `*.e2e.ts` (invisible to the default vitest include) |
| Runner | `pnpm run test` (inside `check`) | `pnpm run test:e2e` (`vitest.e2e.config.ts`) |
| Network | none (injected fetch) | real provider endpoints |
| Credentials | none | local Codex cache / env vars |
| CI | always | never (manual, laptop) |
| Timeouts | vitest defaults | `testTimeout: 60_000`, `fileParallelism: false`, `retry: 0` |

`pnpm run check` is byte-for-byte unaffected. The e2e sources still
type-check under the root `tsconfig.json` include (compile coverage without
execution).

### 6.2 Codex credential loader (`e2e/support/codex-auth.ts`)

The script that makes the codex suite self-configuring on a laptop where
the Codex CLI is logged in:

1. **Path resolution**: `$CODEX_AUTH_PATH` ?? `$CODEX_HOME/auth.json` ??
   `~/.codex/auth.json`.
2. **Shape** (observed on the development machine): top-level
   `{ OPENAI_API_KEY, auth_mode, last_refresh, tokens }` with
   `tokens: { access_token, account_id, id_token, refresh_token }`. The
   loader consumes `tokens.access_token` only.
3. **Freshness check**: decode the JWT payload locally; require the
   `https://api.openai.com/auth` claim with `chatgpt_account_id` and
   `exp > now + 60s`.
4. **Outcome**: `{ available: true, accessToken: SecretString }` or
   `{ available: false, reason }`. The suite uses `describe.skipIf` on the
   result and prints the reason once — e.g.
   `codex e2e skipped: ~/.codex/auth.json not found (run "codex login")` or
   `... access token expired (run codex to refresh)`. Missing credentials
   are a **skip**, never a failure.
5. **Hygiene**: the raw token goes straight into `SecretString`; the loader
   never logs token material, and no credential is written anywhere.
   Refreshing tokens is out of scope (§2) — the CLI owns its cache.

Model selection for live runs: `$CODEX_E2E_MODEL` overrides a default model
key pinned in `e2e/codex-coding-plan.e2e.ts` (set at implementation time to
the Codex CLI's current default; the spec deliberately does not hardcode
one).

### 6.3 The client-contract kit (one suite body, two bindings)

The behaviors that are meaningful both mocked and live are exactly the
`LlmClient` iteration contract (Phase 02 §4.5): delta grammar, exactly one
terminus, tool-call assembly, abort classification, error kinds. Those are
written **once** in `tests/contract/llm-client-contract.ts`:

```ts
export function describeLlmClientContract(binding: {
  name: string;
  scenarios: {
    // each returns { client, request }; bindings decide how the behavior
    // is induced (fixture-fetch client vs live profile + eliciting prompt)
    text: () => Scenario;
    toolCall: () => Scenario;          // offers an `echo` tool
    toolRoundTrip?: () => Scenario;    // history with tool_use + tool_result
    reasoning?: () => Scenario;
    abort?: () => Scenario;
    authFailure?: () => Scenario;
  };
  /** golden pinning for deterministic bindings; omitted by live bindings */
  exact?: { text?: string; reasoning?: string; toolInput?: JsonObject;
            usage?: UsageSnapshot };
}): void;
```

- Structural assertions always run (event ordering, exactly one
  `assistant_message_complete`, assembled object `input`, `stop_reason`
  kind, single-pass iteration, abort classified by `signal.aborted`).
- The `exact` block adds byte-precise golden assertions; only
  deterministic bindings supply it. There is no `if (live)` branching
  inside scenario bodies — strictness is data.
- Unit binding (`tests/contract.test.ts`, runs in `pnpm run check`):
  fixture-fetch clients for both wires with `exact` set to the Phase 02
  golden values. The Phase 02 happy-path decode tests collapse into this
  binding with their assertions preserved.
- Live binding (`e2e/codex-coding-plan.e2e.ts`): the same contract bound to
  `createLlmClient({ provider: "codex_coding_plan", ... })`, no `exact`.

Decision recorded: plugging a real client into the **whole** unit suite was
considered and rejected. Golden decode tests pin exact bytes-to-events
mappings a live model never reproduces; encode tests are pure functions;
retry/error/secret tests involve no HTTP; truncation/idle/malformed/backoff
tests force transport states a healthy live endpoint cannot produce.
Sharing is therefore scoped to the contract kit, where the same assertions
are honest in both modes.

Scenario matrix:

| Scenario (unit counterpart) | Live assertion | Codex live? |
| --- | --- | --- |
| Plain text stream (golden full fixture) | >= 1 `assistant_text_delta`, then exactly one `assistant_message_complete`; `usage.input_tokens/output_tokens > 0`; `stop_reason === "end_turn"`; iteration ends | yes |
| Tool call (tool fixture; encode column) | offer one `echo` tool with `tool_choice: "any"`; expect `tool_use_delta` with provider-assigned id and object `input`; `stop_reason === "tool_use"`; tool block present in the completed message | yes |
| Tool round trip (encode of `function_call`/`function_call_output` history) | send the assistant tool-use message + a `tool_result` back; expect a normal text completion — live proof the history projection is accepted by the backend | yes |
| Reasoning effort + reasoning deltas | request with `reasoning_effort: "low"`; assert zero-or-more `reasoning_delta` then a clean terminus (reasoning emission is model-dependent; structural only) | yes |
| Abort mid-stream (abort rethrown as-is) | abort after the first delta; iteration rejects promptly; classified by `signal.aborted`, not error type | yes |
| Authentication mapping (401 unit table) | same profile with a deliberately corrupted bearer token; expect `ProviderError` kind `authentication` with `status_code: 401` | yes |
| Cache usage fields | structural: when the provider reports cache fields they land on `UsageSnapshot` (soft assertion) | observe-only |
| Truncated stream / idle timeout / malformed frame / retry-after / backoff / fedramp header / `max_tokens` stop | not forcible against a live healthy endpoint (and the codex dialect sends no token cap); these remain unit-only with injected fetch | unit-only |

Budget guard: the codex suite issues at most ~6 small requests per run,
with one-word-answer prompts; assertions are structural (event shapes,
kinds, ids), never on response text.

### 6.4 Deferred profiles (named seam, no code this phase)

Only the codex coding plan has usable credentials today, so
`codex-coding-plan.e2e.ts` is the only live suite created. Because the
contract kit is written against the bare `LlmClient` interface, when
credentials for another approach become available its suite is one new
~10-line binding file behind a skip-if-absent env credential:
`ANTHROPIC_API_KEY` (`anthropic_api`), `OPENAI_API_KEY` (`openai_api`),
`CLAUDE_CODE_OAUTH_TOKEN` (`claude_coding_plan`).

## 7. Dependencies and Workspace Changes

- No new runtime or dev dependencies (JWT payload decode is
  `Buffer`-native; the e2e harness is vitest with a second config file).
- Root `package.json`: add `"test:e2e"`; `"check"` unchanged.
- New root file `vitest.e2e.config.ts`.

## 8. Migration Steps

1. Mechanical split: `providers/*` -> `wires/*` + `stream-client.ts`;
   re-point tests -> verify: `pnpm run check` green with zero assertion
   changes.
2. `access/` contract + `api-key.ts`; thread `WireTransport.headers()`
   through both wires -> verify: existing tests still green; header
   passthrough unit test.
3. `WireOptions` encode paths (system prefix, codex dialect) -> verify:
   new encode tests pin both shapes.
4. `claude-coding-plan.ts` + `codex-coding-plan.ts` (JWT claims) ->
   verify: `tests/access/coding-plan.test.ts` (header shapes, claim
   parsing, fedramp flag, error kinds).
5. `profiles.ts` + `factory.ts` + class removal + `index.ts` exports ->
   verify: `factory.test.ts` (selection, custom `base_url`, fixture
   substitutability per §4.5); `pnpm run check` green.
6. Contract kit: extract `tests/contract/llm-client-contract.ts` from the
   wires' happy-path tests; `tests/contract.test.ts` binds fixture-fetch
   clients for both wires with `exact` golden values -> verify:
   `pnpm run check` green; the Phase 02 golden assertions all present in
   the unit bindings.
7. E2E harness: `vitest.e2e.config.ts`, `test:e2e` script,
   `codex-auth.ts`, `codex-coding-plan.e2e.ts` binding the same contract
   in structural mode -> verify: `pnpm run test:e2e` runs the codex suite
   live on this machine, and skips cleanly when
   `CODEX_AUTH_PATH=/nonexistent`.
8. Update the migration `index.md` row for this phase.

## 9. Coexistence and Rollback

- Coexistence: the Rust crates remain the live implementation; nothing
  under `agent-core/` changes. Within `eos-agent-core`, Phase 03 has not
  started, so the class-to-factory change has no downstream consumers.
- Rollback: `git revert` of the restructure commit restores the Phase 02
  `providers/` layout; delete `e2e/`, `vitest.e2e.config.ts`, the
  `test:e2e` script, and the index row. No other surface is affected.

## 10. Verification

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core
pnpm run check          # typecheck + lint + unit tests; no network I/O
pnpm run test:e2e       # live; codex suite auto-loads ~/.codex/auth.json,
                        # other suites skip without env credentials
CODEX_AUTH_PATH=/nonexistent pnpm run test:e2e   # proves clean skip path
git -C .. diff --stat -- agent-core              # stays empty
git -C .. diff --check -- docs/plans/agent-core-rust-to-typescript-migration eos-agent-core
```

## 11. Acceptance Criteria

Phase 02.5 is accepted when:

- `wires/` and `access/` are mutually import-free; `profiles.ts` is the
  only vendor-aware module; `createLlmClient` is the construction surface
  and returns the unchanged `LlmClient` contract,
- the four profiles of §4.3 work per the normative table, including custom
  `base_url` on the api profiles,
- codex JWT claim parsing matches §4.4 including the fedramp header and
  `request`-kind failures,
- every Phase 02 assertion survives (happy-path golden values inside the
  contract kit's unit bindings, the rest as moves), and the §4.5
  substitutability proofs pass,
- the contract kit is the single source of the shared scenarios: the live
  suite contains no assertion logic of its own, only the binding,
- `pnpm run check` is green with no network I/O; `*.e2e.ts` files are
  excluded from it but still type-checked,
- `pnpm run test:e2e` on a machine with a logged-in Codex CLI runs the §6.3
  live battery against the codex coding plan without any manual
  configuration, and skips (not fails) everywhere credentials are absent,
- the copilot access scheme and openai-chat wire exist as named seams in
  this spec, not as code,
- the Rust `agent-core/` tree is byte-for-byte unchanged,
- and `index.md` lists Phase 02.5 with status and verification.

## 12. Progress Tracker

| Step | Status | Required proof |
| --- | --- | --- |
| Mechanical wire/stream-client split | Pending | `pnpm run check` green with zero assertion changes |
| Access contract + api-key + header threading | Pending | Header passthrough test green |
| Wire options (system prefix, codex dialect) | Pending | Encode tests pin both shapes |
| Coding-plan access schemes | Pending | Claim parsing + header shape tests green |
| Profiles + factory + class removal | Pending | Factory + substitutability tests green |
| Contract kit + unit bindings | Pending | `pnpm run check` green; golden assertions present in `exact` bindings |
| Live e2e harness + codex auth loader | Pending | Codex suite (contract in structural mode) live-green locally; clean-skip proof |
| Index updated | Pending | Phase 02.5 row present in `index.md` |
