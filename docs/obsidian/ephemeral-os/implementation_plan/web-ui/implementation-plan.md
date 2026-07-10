# Web Console — Implementation Plan & Progress Tracker

Execution plan for [[web-ui-design]] (pages/components) and [[http-server]]
(`sandbox-console` HTTP surface). Phases are ordered so the two product
priorities land earliest: (1) workspace sessions + exec/stdin/transcript in
the browser, (2) embedded preview of anything serving HTTP in the sandbox.

## Progress tracker

Update the Status column and the phase checkboxes as work lands. Statuses:
`not started` · `in progress` · `blocked (<on what>)` · `done (<date>)`.

| Phase | Title | Depends on | Status |
|---|---|---|---|
| 0 | Decisions & scaffolding | — | done (2026-07-07) |
| 1 | `sandbox-console` HTTP server v0 | 0 | done (2026-07-07) |
| 2 | SPA shell & shared components | 1 | done (2026-07-07) |
| 3 | Fleet Board | 2 | done (2026-07-07) |
| 4 | Detail shell & Overview tab | 2 | done (2026-07-07) |
| 5 | Terminal tab | 4 | done (2026-07-07) |
| 6 | Preview tab | 4 | done (2026-07-07) |
| 7 | `file_list` op + Files tab | 4 | done (2026-07-07) |
| 8 | Observability tab | 4 | done (2026-07-07) |
| 9 | Hardening & docs | 3–8 | done (2026-07-07) |

Milestones:

- **M1 — bridge up** (end of Phase 1): every gateway op curl-able through
  `/api/rpc`; preview proxy forwards to a live sandbox.
- **M2 — fleet manageable** (end of Phase 3): create/inspect/squash/destroy
  sandboxes entirely from the browser.
- **M3 — product priorities met** (end of Phase 6): sessions, exec, stdin,
  live transcript, and embedded port preview all usable.
- **M4 — spec complete** (end of Phase 8): every page in [[web-ui-design]]
  built.

## Standing constraints (apply to every phase)

- `sandbox-console` is a client peer built on
  `sandbox_cli::core::GatewayClient`. It must never define operation
  vocabulary, contact the daemon RPC endpoint directly, or expose the gateway
  auth token to the browser ([[http-server]] · Position).
- Repo rules from `CLAUDE.md`: SRP, no inline comments in `src/`, tests under
  `tests/` only, workspace-level dependency declarations, `cargo clippy
  --all-targets` clean, work directly on `main`.

## Expected footprint

### New folders

`crates/sandbox-console/` (Phases 0–1) — module split is the implementer's
call (SRP per module); the responsibilities are:

```
crates/sandbox-console/
├── Cargo.toml
├── src/
│   ├── main.rs / lib.rs
│   ├── config.rs          gateway endpoint, auth token, loopback bind
│   ├── router.rs          the six public routes
│   ├── rpc.rs             /api/rpc one-shot + SSE bridge over GatewayClient
│   ├── catalog.rs         /api/catalog
│   ├── health.rs          /api/sandboxes/:id/health probe
│   ├── proxy.rs           /s/:id preview proxy + endpoint-resolution cache
│   └── assets.rs          static SPA serving with route fallback
└── tests/                 integration tests + fake-gateway / fake-daemon_http
```

`web/console/` (Phases 0, 2–8) — the SPA:

```
web/console/
├── package.json, vite.config.ts, tsconfig.json, index.html
└── src/
    ├── api/               rpc client (two error paths), SSE, catalog forms
    ├── components/        StateBadge, ResourceSparkline, StreamLogPane,
    │                      ErrorToast, ConfirmDestroyDialog, PortPreview,
    │                      PollController
    └── pages/
        ├── fleet/         FleetSummaryBar, SandboxCard, CreateSandboxModal
        └── sandbox/       SandboxHeader + tabs: overview/, terminal/,
                           files/, observability/, preview/
```

### Existing files changed

| File | Change | Phase |
|---|---|---|
| `Cargo.toml` (root) | workspace member + HTTP-stack deps in `[workspace.dependencies]` | 0 |
| `README.md` | component-table row for `sandbox-console` with its boundary law | 0 |
| `xtask/src/main.rs` | packaging verb: build SPA, stage assets for the console | 0 |
| `.gitignore` | `web/console/node_modules`, SPA build output | 0 |
| `bin/` | optional `start-sandbox-console` launcher script | 0 |
| `crates/sandbox-operations/runtime/src/file.rs` | add `FILE_LIST_SPEC` + args | 7 |
| `crates/sandbox-operations/runtime/src/lib.rs` | export + register in family/catalog arrays | 7 |
| `crates/sandbox-runtime/operation/src/file/service/impls/` | new `list.rs` beside read/write/edit/blame, plus `mod.rs` and the service trait | 7 |
| `crates/sandbox-runtime/operation/src/cli_definition/file_operations.rs` | `FILE_LIST` `OperationEntry` + `dispatch_file_list` in `OPERATIONS` | 7 |
| `crates/sandbox-runtime/layerstack` | as-built addition: public `LayerStack::list_dir` (merged one-level listing) — the snapshot scope needs a merged-view walk only this crate can do | 7 |
| `crates/sandbox-runtime/namespace-process` + `workspace` re-export | as-built addition: `FileRunnerOp::ListDir` runner op — the live-session scope must list inside the mount namespace | 7 |
| `crates/sandbox-runtime/.../tests/` (and e2e suites as applicable) | `file_list` coverage | 7 |
| this file + the two specs | tracker updates per phase; specs corrected against as-built | all / 9 |

### Deliberately untouched

`sandbox-gateway`, `sandbox-daemon` (its `/health` and `/forward` already
exist), `sandbox-protocol` (the console adds zero vocabulary),
`sandbox-manager*`, `sandbox-observability*`, and `sandbox-runtime-cli`
(dispatch lives in `sandbox-runtime/operation`; the CLI and `/api/catalog`
pick `file_list` up from the spec for free). Any diff touching these during
console work is a boundary-law violation to catch in review.

---

## Phase 0 — Decisions & scaffolding

Goal: everything later phases assume, decided and committed.

- [x] Adopt the stack fixed in [[design]]: React 19 + TypeScript + Vite
      under `web/console/`, Tailwind tokens for the light theme, and the
      package set listed there.
- [x] Decide asset packaging: `cargo run -p xtask -- package-console` builds
      the SPA (`web/console/dist`) and stages it into `dist/console` for
      `sandbox-console` to serve.
- [x] Scaffold `sandbox-console` bin crate: config (gateway endpoint, auth
      token, loopback bind), `GatewayClient` wiring, HTTP listener, serves a
      placeholder SPA.
- [x] Add the crate to `README.md`'s component table with its boundary law.

Exit: `cargo run -p sandbox-console` serves a hello page on loopback;
workspace builds and clippy passes.

## Phase 1 — `sandbox-console` HTTP server v0

Goal: the full backend surface of [[http-server]]; no UI work.

- [x] `POST /api/rpc` one-shot: inject `request_id` + auth, pass through
      verbatim; protocol errors in body with HTTP 200, transport errors as
      400/502/504.
- [x] `POST /api/rpc` SSE variant (`Accept: text/event-stream`): sets
      `_stream_logs: true`, emits `log` events then one `result` event.
- [x] `GET /api/catalog`: manager + runtime + observability catalogs.
- [x] `GET /api/sandboxes/:id/health`: resolve record → probe `daemon_http`
      `/health` with short timeout → `{status: ok|unreachable}`.
- [x] `/s/:id/...` preview proxy: prefix swap to `/forward/...`, preserve
      method/headers/body/query, stream bodies, tunnel WebSocket/upgrades,
      append `X-Forwarded-*`, short-TTL endpoint-resolution cache.
- [x] Console error mapping (400/404/503/502) with `daemon_http` errors
      passed through verbatim.
- [x] Static SPA serving with client-route fallback.
- [x] Integration tests in `tests/`: fake gateway for RPC/SSE/catalog, fake
      `daemon_http` for health/proxy (including an upgrade round-trip).

Exit: all six routes pass integration tests; manual smoke against a real
sandbox — `list_sandboxes` via curl, a Vite dev server visible through
`/s/<id>/shared/<port>/`.

## Phase 2 — SPA shell & shared components

Goal: the app skeleton every page plugs into.

- [x] Router with the full route map **including deep links**: observability
      sub-views, `traces/:trace-id`, `terminal#cmd-<id>`, `files?path=…&
      session=…`, `preview?scope=…&port=…&path=…`.
- [x] RPC client over `/api/rpc` (one-shot + SSE) with the two error paths
      (protocol-in-body vs transport-in-status).
- [x] Catalog-driven form/validation helper backed by `/api/catalog` (used
      by CreateSandboxModal and any op form; keeps UI arguments drift-free).
- [x] `PollController` (per-page polling: ~300–500ms fast / ~2s slow, idle
      decay with instant recovery, interaction nudges, pause when hidden,
      immediate catch-up refetch on return from last cursor).
- [x] `ErrorToast` rendering the protocol `{kind, message, details}` shape.
- [x] `StateBadge`, `ResourceSparkline`, `StreamLogPane` (SSE),
      `ConfirmDestroyDialog`, `PortPreview` launcher (navigates to Preview
      routes; target tab lands in Phase 6).

Exit: shell loads through the console, routes resolve, an intentionally bad
RPC renders an `ErrorToast`.

## Phase 3 — Fleet Board (`/`)

- [x] Poll `list_sandboxes` + no-arg `snapshot`; fast cadence for
      `Creating`/`Stopping` cards.
- [x] `FleetSummaryBar` — **client-side aggregation** (the no-arg snapshot
      returns `{sandboxes: [...]}`, per-sandbox; nothing pre-aggregated).
- [x] `SandboxCard` with state-dependent layouts: Ready (sparkline, counts,
      actions), Creating (max-height scrolling `StreamLogPane`), Failed
      (error + Inspect).
- [x] Health dots — posture decided: no batch endpoint; each card probes on
      its own slower 10s cadence (accepted-risk option; revisit at fleet
      scale in Phase 9).
- [x] `CreateSandboxModal` mirroring `create_sandbox` args (image,
      workspace-bind-root, count) with SSE progress.
- [x] Squash action; Destroy behind `ConfirmDestroyDialog` (type-the-id).

Exit: **M2** — full lifecycle from the browser against a real gateway.

## Phase 4 — Detail shell & Overview tab (`/sandboxes/:id`)

- [x] `SandboxHeader` from `inspect_sandbox`: badges, endpoints + health dot,
      shared-base indicator, `PortPreview` launcher, Squash/Destroy.
- [x] Five-tab scaffold with routed tabs (Overview is the index route).
- [x] Overview panels: `RecordPanel`, `WorkspaceSessionList`,
      `InFlightExecutions` (links use `terminal#cmd-<id>`),
      `ResourceSnapshot`.

Exit: detail page renders live data; in-flight execution links land on the
Terminal tab route (cards arrive in Phase 5).

## Phase 5 — Terminal tab

The first product priority: sessions + exec + stdin + live transcript.

- [x] `SessionSidebar`: list under an **all** entry (selection filters the
      ledger and pre-fills the composer; **all** = unfiltered), create with
      network-profile picker, destroy with grace seconds and the
      refusal path listing `active_command_session_ids` as `#cmd-` jump
      links.
- [x] `CommandComposer`: command text, target session or "auto-publish"
      (implicit `publish_then_destroy`), optional timeout (user-visible;
      `yield_time_ms` is agent-only, pinned to 0 and hidden); submit opens
      the new command's terminal expanded and focused.
- [x] `CommandCard`: a terminal frame when expanded (integrated input line,
      tail-pinned autoscroll), a one-line ledger row when collapsed; session
      chip, running/completed/failed/timed-out states, addressable as
      `#cmd-<command-session-id>`. Line discipline only — no PTY/raw mode.
- [x] `TranscriptViewer`: offset-tracked tail via `read_command_lines`
      (≤1000 lines/fetch), infinite scroll-back, **plus the inline path** —
      a command that beats the initial wait returns terminal output with no
      `command_session_id`, and the card renders it without polling.
- [x] `StdinBar`: the terminal's input line while running — Enter →
      `write_command_stdin` (yield 0) + an immediate `read_command_lines`
      poll nudge for instant reaction; Ctrl-C/Ctrl-D captured as keystrokes
      when the frame is focused, explicit buttons kept for discoverability.
- [x] Ledger persistence + catch-up: known command ids (text, timestamps)
      in `localStorage` per sandbox; reload rebuilds the ledger from
      storage plus snapshot in-flight executions; on return to the tab,
      transcripts resume from their last offset with a catch-up nudge.

Exit: run a server, watch it log, poke it with stdin, Ctrl-C it, watch the
implicit-session publish appear — all without leaving the tab.

## Phase 6 — Preview tab

The second product priority: see anything serving HTTP, any port.

- [x] `WebPreviewPane`: iframe over `/s/:id/<scope>/<port>/<path>`; scope
      picker with the isolated bind hint (`0.0.0.0` / workspace IP),
      free-form port, editable path, refresh, open-in-new-tab.
- [x] Blocked-embed detection (`X-Frame-Options` / `frame-ancestors`) with
      new-tab fallback.
- [x] Wire every `PortPreview` launcher (header + running command cards) to
      deep-link here pre-filled.

Exit: **M3** — start a dev server in the Terminal tab, click preview on its
card, see the site embedded; WebSocket HMR works through the proxy.

## Phase 7 — `file_list` op + Files tab

- [x] Backend: `file_list` spec in `sandbox-runtime-operations` (path,
      optional `workspace_session_id` for the dual scope, entries with
      kind/size), implementation in `sandbox-runtime/operation`
      (`file/service/impls/list.rs` plus a dispatch entry in
      `cli_definition/file_operations.rs`), tests in `tests/`. The CLI picks
      it up from the spec for free. As-built: also a public
      `LayerStack::list_dir` (snapshot merge) and a `FileRunnerOp::ListDir`
      runner op (in-namespace session listing) — see the footprint table.
- [x] `FileTree` over `file_list`; `SessionScopePicker` (published snapshot
      vs live session) driving tree + viewer.
- [x] `FileViewer`: 2000-line windows, offset paging, truncation indicator.
- [x] `BlameGutter`: owner coloring + legend + click-through (session →
      Terminal, operation → trace deep link); **enabled only in published
      scope** — `file_blame` takes no session id; disabled with hint in
      live-session scope.
- [x] `FileEditor`: edit mode pages `file_read` to the end first (whole-file
      buffer — `file_write` replaces everything), size threshold →
      read-only, save guard: re-read + compare, refuse with reload prompt on
      concurrent change.

Exit: browse both scopes, blame a published file into its trace, edit and
save with the conflict guard demonstrably firing under a concurrent write.

## Phase 8 — Observability tab

- [x] Sub-nav routes (`resources` default / `traces` / `events` /
      `layerstack`).
- [x] Resources: CPU/mem/IO/disk charts, counters rendered as deltas
      (`_counters` flag), scope picker, window capped at 600s, auto-refresh.
- [x] Traces: `TraceList` (default `last`; as-built the list derives from
      recent events — no enumeration op), `TraceWaterfall` (nested bars,
      status colors, ⚑ events pinned), `SpanAttrsDrawer`;
      `traces/:trace-id` deep link resolves.
- [x] Events: filterable table (name / since / last-N), live-tail via
      polling, trace cells linking to waterfalls.
- [x] LayerStack: `LayerStackViz` (per-layer id/bytes/lease+booking counts,
      squashable bracket, client-side depth trend; per-layer workspace ids
      aren't in the view result so the workspace filter was dropped),
      `SquashButton` with `StreamLogPane`; verified: a pre-run "est. after"
      count is **not derivable** — before-count only, after from the
      refetch.

Exit: **M4** — every view in [[web-ui-design]] renders live data; all
cross-tab deep links resolve end to end.

## Phase 9 — Hardening & docs

- [x] Empty states for every list surface (no sandboxes, no sessions, no
      commands, no traces, empty directory).
- [x] Error-path sweep: gateway down, sandbox not ready, daemon unreachable,
      preview 403 (isolated without reachable IP) — each renders its mapped
      error, not a blank pane.
- [x] Poll audit: cadences, tab-hidden pauses, health fan-out posture
      revisited at fleet scale (kept: independent 10s per-card probes; a
      batch endpoint stays the escape hatch if fleets grow).
- [x] Manual e2e journey scripted in the repo docs ([[e2e-journey]]):
      create → exec → stdin → preview → blame → squash → destroy.
- [x] Docs: crate README (`crates/sandbox-console/README.md`), `README.md`
      component-table row finalized, both specs re-read and corrected
      against as-built behavior.

Exit: journey script passes clean against a fresh gateway (verified
2026-07-07); tracker above fully `done`.

---

## Risks & open questions (resolutions as-built)

- **SPA stack** — held as fixed in [[design]]; no package failed. (One
  deviation: npm resolves react-router to v8 by default now — the console
  pins `react-router@^7` per the design doc.)
- **`sandbox_cli::core` reuse** (Phase 1): consumed as-is; `send_with_logs`
  and the config surface were public enough — no visibility change needed.
- **Health fan-out** (Phase 3): resolved by accepting the slower cadence —
  each card probes independently every 10s; no batch endpoint added.
- **Squash estimate** (Phase 8): verified **not derivable** — the UI shows
  the before-count and reports the after-count from the post-squash
  refetch; the result carries `squashed_blocks`.
- **Frame-blocked apps** (Phase 6): as accepted — header-probe detection
  (`X-Frame-Options` / `frame-ancestors`) with the new-tab fallback.
- **Binary / non-UTF-8 files** (Phase 7): as accepted — the Files tab shows
  a "binary file" placeholder in v0.
- **Command history is per-browser** (Phase 5): shipped as designed;
  `localStorage` rebuilds the ledger and a fresh browser sees only
  in-flight commands. `list_command_sessions` remains a candidate op if
  shared history matters.
