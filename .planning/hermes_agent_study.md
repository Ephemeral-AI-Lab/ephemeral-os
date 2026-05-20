# Hermes Agent — how it drives Claude Code & Codex as backends

Repo: `https://github.com/NousResearch/hermes-agent` (verified live, MIT, Python, default branch `main`). Single mono-repo, ~24k+ files visible. Topics listed by the repo itself: `claude-code`, `codex`, `clawdbot`, `openclaw`, `hermes-agent`.

Important upfront finding: Hermes does **NOT** spawn the Anthropic `claude` (Claude Code) CLI as a subprocess. It does spawn the OpenAI `codex` CLI *optionally*, and it spawns GitHub's `copilot` CLI for ACP. Anthropic plan-mode in Hermes is implemented by reusing Claude Code's *credential file* and pretending to be Claude Code against `api.anthropic.com` directly. Each of the three is a different mechanism. Detail below.

---

## 1. Architecture / process model

Three distinct backend types are relevant; only the second and third spawn an external CLI:

**(a) Anthropic via "Claude Code" plan-mode — NO subprocess.**
File: `plugins/model-providers/anthropic/__init__.py` (declarative profile only). Transport: `agent/transports/anthropic.py` and `agent/anthropic_adapter.py` (94 kB; speaks Anthropic Messages API directly). Hermes only borrows Claude Code's auth artifacts: it reads `CLAUDE_CODE_OAUTH_TOKEN` and Claude Code's credential store, then makes plain HTTPS calls to `https://api.anthropic.com`. Provider doc text: *"Hermes routes as Claude Code against your Anthropic account... prefers Claude Code's own credential store over copying the token into `~/.hermes/.env`."* (`website/docs/integrations/providers.md:182,204`). Aliases `claude` / `claude-code` are wired in `plugins/model-providers/anthropic/__init__.py:46`.

**(b) OpenAI Codex — two sub-modes, both wired through the `openai-codex` provider.**
Profile: `plugins/model-providers/openai-codex/__init__.py`. `api_mode="codex_responses"`, `base_url="https://chatgpt.com/backend-api/codex"`, `auth_type="oauth_external"`.
- **Default mode ("codex_responses")**: Hermes itself drives the OpenAI Responses API at the ChatGPT backend URL, owns the tool loop, no subprocess. Transport: `agent/transports/codex.py` + helpers `agent/codex_responses_adapter.py` (49 kB), `agent/codex_runtime.py::run_codex_stream`. OAuth tokens obtained via device-code flow stored in `~/.hermes/auth.json` (can also import from `~/.codex/auth.json`).
- **App-server mode (opt-in)**: Hermes spawns `codex app-server` as a long-lived subprocess and hands the **entire turn** to it. Speaks newline-delimited JSON-RPC 2.0 over stdio. Transport: `agent/transports/codex_app_server.py` (`CodexAppServerClient`) + `agent/transports/codex_app_server_session.py` + `agent/transports/codex_event_projector.py`. Activated by `model.openai_runtime: codex_app_server` in `~/.hermes/config.yaml`; toggled via slash command `/codex-runtime codex_app_server` (`hermes_cli/codex_runtime_switch.py`). Min `codex` version 0.125. Requires `npm i -g @openai/codex` and `codex login`.

**(c) GitHub Copilot via ACP — also a subprocess, but a different protocol.**
Profile: `plugins/model-providers/copilot-acp/__init__.py` (`api_mode="chat_completions"` formally but with `auth_type="external_process"`, `base_url="acp://copilot"`). Adapter: `agent/copilot_acp_client.py` (24 kB) — *"OpenAI-compatible shim that forwards Hermes requests to `copilot --acp`"*. Each request spawns a short-lived ACP session, sends the formatted transcript as one prompt, collects text chunks, parses back into OpenAI shape. Resolves command via `HERMES_COPILOT_ACP_COMMAND` / `COPILOT_CLI_PATH` or defaults to `copilot`. Default args `--acp --stdio`.

Hermes is **also an ACP server itself** (separate concern): `acp_adapter/server.py` (1948 lines) exposes Hermes as the *server* in Zed-style ACP. Not relevant to your question, but worth knowing in case you encounter the directory.

Spawn lifecycle:
- Codex app-server: one long-lived subprocess per `AIAgent` instance, lazy-spawned on first turn, reused across turns, closed at agent shutdown. Crash → session dropped and respawned next turn (`agent/codex_runtime.py:60-95`).
- Copilot ACP: spawned per request (short-lived session per prompt).
- Both use `subprocess.Popen` with `stdin=PIPE, stdout=PIPE, stderr=PIPE, bufsize=0`. Codex client uses **two daemon threads** (one stdout reader doing JSON-RPC dispatch into pending/notification/server-request queues, one stderr drain capped at 500 lines). Synchronous on the main thread; *"AIAgent.run_conversation() is synchronous... layering asyncio just to drive a stdio child creates surprising interrupt semantics"* (`codex_app_server.py:62-67`).

Prompt is passed:
- Codex app-server: structured JSON-RPC params on `thread/start` / `turn/start`.
- Copilot ACP: a formatted plaintext transcript (`_format_messages_as_prompt` in `copilot_acp_client.py:135-207`) sent as a single ACP prompt block.

Response harvested:
- Codex: stream of `item/*` notifications until `turn/completed`.
- Copilot ACP: text chunks accumulated, then regex-parsed for `<tool_call>{...}</tool_call>` blocks (`_TOOL_CALL_BLOCK_RE`, `_extract_tool_calls_from_text` at `copilot_acp_client.py:30,234`).

## 2. State / session management

**Tool calls.** Two strategies, both observed:
- *Let the CLI manage its own tool loop* (Codex app-server): Codex runs `shell`, `apply_patch`, `update_plan`, `view_image`, `web_search` natively inside its own sandbox. Hermes only *projects* the resulting `commandExecution` / `fileChange` / `mcpToolCall` events into synthetic `{role:"assistant", tool_calls:[...]}` + `{role:"tool"}` messages so memory/skill review keeps working (see `codex_event_projector.py` and the table at `docs/codex-app-server-runtime.md:192-198`).
- *Hermes interprets tool calls* (Copilot ACP): the adapter injects an explicit instruction *"you MUST output tool calls using `<tool_call>{...}</tool_call>` blocks with JSON exactly in OpenAI function-call shape"* (`copilot_acp_client.py:142-144`), then regex-extracts them. Tools then dispatch through Hermes' own `model_tools.handle_function_call()`.

**Conversation history.** Hermes owns the canonical message list (`AIAgent.messages`). For Codex app-server, after each turn the projector pushes structured messages into that list (`agent/codex_runtime.py:131-136`). For Copilot ACP, Hermes formats the whole history into the prompt text each request (stateless on Copilot's side).

**Workspace / cwd.** Codex app-server: `cwd = getattr(agent, "session_cwd", None) or os.getcwd()` (`codex_runtime.py:48`). Codex's own sandbox profile (`:workspace` by default) controls writable roots. Kanban workers explicitly pass `-c sandbox_mode="workspace-write"` and `sandbox_workspace_write.writable_roots=[...]` overrides (`codex_app_server.py:88-111`). HOME is preserved for child via `_resolve_home_dir()` in copilot client.

**Auth.**
- Codex: OAuth device code (`hermes auth add codex-oauth`), tokens at `~/.hermes/auth.json` (with import from `~/.codex/auth.json`). When the app-server subprocess is spawned, it reads `~/.codex/auth.json` itself — Hermes just spawns it and lets it self-auth via `CODEX_HOME` env (`codex_app_server.py:80-81`).
- Anthropic plan mode: env vars `ANTHROPIC_API_KEY`, `ANTHROPIC_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN` (in priority order). Hermes deliberately leaves the Claude Code credential file in place rather than copying it.
- Copilot ACP: delegated entirely to `copilot login` (Hermes only spawns the binary; no token handling in Hermes).

**Permission prompts.** Codex app-server has server-initiated JSON-RPC requests for approvals (e.g. exec / applyPatch). The client routes them into `_server_requests` queue; `CodexAppServerSession` bridges them to Hermes' standard approval flow via `tools.terminal_tool._get_approval_callback` (`codex_runtime.py:50-55`). Kanban dispatcher sets `default_permissions = ":workspace"` and `sandbox_workspace_write.network_access=false` to avoid prompting on workspace writes. Copilot ACP returns a `_permission_denied` cancelled-outcome response by default (`copilot_acp_client.py:122-132`).

## 3. Abstraction surface

The abstraction is **`ProviderProfile`** in `providers/base.py`. Every backend (API, OAuth, subprocess) registers a `ProviderProfile` via the plugin discovery in `providers/__init__.py`. Profiles live as plugins at `plugins/model-providers/<name>/{__init__.py, plugin.yaml}` and user overrides at `$HERMES_HOME/plugins/model-providers/<name>/`.

Profile carries a key field: `api_mode` ∈ `{chat_completions, anthropic_messages, codex_responses, codex_app_server, copilot_acp, ...}`. Downstream routing reads this to pick a transport in `agent/transports/`:
- `agent/transports/chat_completions.py` — OpenAI-compatible.
- `agent/transports/anthropic.py` + `agent/anthropic_adapter.py` — Anthropic Messages.
- `agent/transports/codex.py` + `agent/codex_responses_adapter.py` — Codex over Responses API.
- `agent/transports/codex_app_server*.py` — Codex subprocess.
- `agent/copilot_acp_client.py` — Copilot ACP subprocess (exposed as a chat-completions-shaped shim).

Resolution chain documented in `providers/README.md` (verbatim): `hermes_cli/runtime_provider.py` reads `profile.api_mode`; `agent/transports/chat_completions.py::_build_kwargs_from_profile()` calls `profile.prepare_messages()` / `profile.build_extra_body()` / `profile.build_api_kwargs_extras()`; `run_agent.py` passes `provider_profile=<ProviderProfile>` into the transport.

Mode toggles:
- `/model <provider>:<model>` slash command, or `hermes model` setup wizard.
- For Codex specifically, **`/codex-runtime auto|codex_app_server|on|off`** — persists `model.openai_runtime` in `~/.hermes/config.yaml`. Code: `hermes_cli/codex_runtime_switch.py`.
- Env var `CLAUDE_CODE_OAUTH_TOKEN` automatically pulls Claude Code's plan-mode token without further config.
- Subscription-plan providers are listed in the providers doc: `nous`, `openai-codex`, `copilot`, `copilot-acp`, `anthropic` (OAuth), `xai-oauth`, `minimax-oauth`, `qwen-oauth`, `kimi-coding`, `kimi-coding-cn`, `alibaba-coding-plan`, `google-gemini-cli`, plus pay-per-token providers all sitting behind the same `ProviderProfile` interface.

## 4. Concrete capabilities and limits

**Codex app-server tools** (model side, in Codex's runtime): `shell`, `apply_patch`, `update_plan`, `view_image`, `web_search` — built into the Codex binary. Plus auto-migrated Codex plugins (Linear, GitHub, Gmail, etc.) discovered via `plugin/list` RPC. Plus Hermes' own tools exposed as an MCP callback (`agent/transports/hermes_tools_mcp_server.py`): `web_search`, `web_extract`, `browser_*`, `vision_analyze`, `image_generate`, `skill_view`, `skills_list`, `text_to_speech`. **Not available** on app-server: `delegate_task`, `memory`, `session_search`, `todo` — require Hermes' in-process loop state, can't be reached via stateless MCP.

**Streaming.** Codex app-server is streaming end-to-end (notifications consumed inside the AIAgent turn loop, interleaved with interrupt checks at small timeouts). Copilot ACP collects chunks then returns once complete. Anthropic-as-Claude-Code is streaming (standard Messages API stream).

**Model identifiers / aliases.** Provider aliases (from each plugin's `aliases=`):
- `anthropic` → `claude`, `claude-oauth`, `claude-code`
- `openai-codex` → `codex`, `openai_codex`
- `copilot-acp` → `github-copilot-acp`, `copilot-acp-agent`
- `kimi-coding` → `kimi`, `moonshot`, `kimi-for-coding`
- `alibaba-coding-plan` → `alibaba_coding`

**Limits called out:**
- Anthropic OAuth ("plan mode") *"only works if you're on a Claude Max plan and have purchased extra usage credits. The base Max plan allowance... is not consumed by Hermes — only the extra/overage credits you've added on top are. Claude Pro subscribers cannot use this path."* (`providers.md:182`).
- Codex `--version` must be ≥ 0.125 for app-server (`codex_app_server.py:30`).
- App-server background review fork is "downgraded to `codex_responses`" because Hermes-loop tools aren't reachable through Codex's MCP callback (`codex-app-server-runtime.md:200-204`).
- Token refresh failure on Codex OAuth marks the refresh token "dead" and stops retrying until `hermes auth add codex-oauth` re-runs the device flow.

## 5. Key code references

- `providers/base.py:1-60` — `ProviderProfile` ABC; `OMIT_TEMPERATURE` sentinel.
- `providers/__init__.py:90-160` — plugin discovery + registry.
- `providers/README.md` — full architecture description (the doc text you should crib).
- `plugins/model-providers/anthropic/__init__.py:44-50` — Anthropic profile with `claude-code` alias and `CLAUDE_CODE_OAUTH_TOKEN` env var.
- `plugins/model-providers/openai-codex/__init__.py:6-15` — Codex profile; `api_mode="codex_responses"`, `auth_type="oauth_external"`, `base_url=https://chatgpt.com/backend-api/codex`.
- `plugins/model-providers/copilot-acp/__init__.py:8-30` — Copilot ACP profile; `auth_type="external_process"`.
- `agent/transports/codex_app_server.py:52-167` — `CodexAppServerClient` (the wire-level JSON-RPC speaker over stdio).
- `agent/transports/codex_app_server.py:113` — exact spawn: `cmd = [codex_bin, "app-server"] + app_server_args`.
- `agent/transports/codex_app_server_session.py` (810 lines) — turn driver, item projection, approval bridging.
- `agent/codex_runtime.py:28-160` — `run_codex_app_server_turn`: where AIAgent hands a turn to the subprocess.
- `agent/codex_runtime.py` (entire) — also has `run_codex_stream` for the Responses-API non-subprocess path.
- `agent/copilot_acp_client.py:1-300` — the Copilot ACP adapter (subprocess spawn, prompt formatting, regex tool-call extraction).
- `agent/anthropic_adapter.py` (94 kB) — Anthropic Messages API direct (this is the "Claude Code plan" path — no CLI spawn).
- `agent/codex_responses_adapter.py` (49 kB) — Codex Responses API direct (Hermes-driven loop, no CLI spawn).
- `hermes_cli/codex_runtime_switch.py:1-95` — `/codex-runtime` slash-command toggle persistence.
- `hermes_cli/runtime_provider.py` — runtime resolution; `_maybe_apply_codex_app_server_runtime()`.
- `website/docs/integrations/providers.md:140-280` — narrative on Codex / Copilot / Anthropic OAuth.
- `website/docs/user-guide/features/codex-app-server-runtime.md` (441 lines) — *the* doc you want to read end-to-end for app-server mode; covers tools, sandbox, kanban, `/goal`, prerequisites.

## What I'm inferring vs. what's in the repo

Verified by direct file read: all provider profiles, the Codex app-server JSON-RPC client (full stdin/stdout/stderr handling), the Copilot ACP subprocess shim with tool-call regex parsing, the slash-command toggle, the provider routing description in the README and providers doc.

Inferred (did not exhaustively read): exact event-projection schema in `codex_event_projector.py`; the approval-bridge code path; whether `agent/transports/codex.py` differs from `codex_app_server.py`. These are likely the next files to read if you want to mirror this design.

Not present / explicit non-feature: there is **no** `claude` CLI subprocess driver in Hermes. If you want the Claude-Code-plan billing semantics, the model here is "borrow `CLAUDE_CODE_OAUTH_TOKEN` and POST directly to api.anthropic.com." If you want to actually drive a `claude` CLI subprocess, no reference implementation exists in this repo — closest analog is the Codex app-server design, which you'd lift wholesale and re-point at `claude` if Anthropic ever ships a comparable JSON-RPC app-server mode.
