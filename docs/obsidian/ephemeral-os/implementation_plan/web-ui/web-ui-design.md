# EphemeralOS Web Console — Design Draft

A web UI for sandbox management: the conversion of `sandbox-manager-cli` and
`sandbox-runtime-cli` to the browser. Every page below is grounded in an
operation that exists today in `sandbox-manager-operations`,
`sandbox-runtime-operations`, or `sandbox-observability-operations`.

## Architecture note (constrains everything below)

The gateway speaks newline-delimited JSON over raw TCP, so the browser talks
to a thin **`sandbox-console` HTTP server** — a client peer of the three CLI
executables, built on `sandbox_cli::core::GatewayClient` — that bridges RPC
to the gateway and reverse-proxies the per-sandbox `daemon_http` surface
(`/health` plus `/forward` port forwarding and exact `/files/list`). Endpoint
spec: [[http-server]]. Operations map 1:1 — the bridge adds no vocabulary.
Three realities shape the UI:

- **Command output is transcript-based, not raw PTY.** The exposed API is
  `exec_command` → `read_command_lines` (stable line offsets) →
  `write_command_stdin`. So the "terminal" is a **ledger of per-command
  terminals** — each `exec_command` opens its own terminal frame with a
  live-tailing transcript and integrated stdin — not an xterm.js raw PTY
  emulation. This fits the product — it's how agents use the sandbox too.
- **Observability is pull-based NDJSON** (spans/events/samples), so charts and
  waterfalls poll on an interval rather than subscribing.
- **App preview rides `daemon_http`.** Servers started inside the sandbox are
  reachable through `/forward/shared/<port>/…` or
  `/forward/isolated=<ws-id>/<port>/…`. The console proxies these same-origin
  (`daemon_http` publishes on host loopback, so the browser can't be assumed
  to reach it directly). Isolated-session servers must bind `0.0.0.0` or the
  workspace IP — the UI surfaces this hint wherever preview appears.

## Page map

| Route | Page | Backing operations |
|---|---|---|
| `/` | Fleet Board | `list_sandboxes`, `observability snapshot` (aggregate) |
| `/sandboxes/:id` | Sandbox Detail (tabbed) | everything below |
| `/sandboxes/:id/terminal` | Terminal tab | `exec_command`, `read_command_lines`, `write_command_stdin`, workspace session ops |
| `/sandboxes/:id/files` | Files tab | `file_read`, `file_write`, `file_edit`, `file_blame` |
| `/sandboxes/:id/observability` | Observability tab | `snapshot`, `cgroup`, `trace`, `events`, `layerstack` |
| `/sandboxes/:id/preview` | Preview tab | `daemon_http` `/forward` via the console `/s/:id` proxy |

Two real pages, with the detail page tabbed. A separate fleet-wide
observability page isn't warranted yet — `events`/`trace`/`cgroup` all require
`--sandbox-id`, so fleet-wide aggregation only exists for `snapshot`, which
the board already shows. There is no dedicated audit page: auditability is
file blame, and it surfaces as the `BlameGutter` inside the Files tab.

Cross-links between tabs are first-class — blame owners jump to a trace,
event rows to the waterfall, in-flight executions to a command card — so
those targets get addresses now rather than after the router exists:

- `/sandboxes/:id/observability/:view` — sub-view (`resources` / `traces` /
  `events` / `layerstack`); bare `/observability` opens `resources`.
- `/sandboxes/:id/observability/traces/:trace-id` — one trace's waterfall;
  target of `EventStream` trace cells and `BlameGutter` operation owners.
- `/sandboxes/:id/terminal#cmd-<command-session-id>` — scrolls to and expands
  one `CommandCard`; target of `InFlightExecutions` rows, destroy-refusal
  jump-links, and `BlameGutter` session owners.
- `/sandboxes/:id/files?path=<path>&session=<ws-id>` — one file in one scope;
  no `session` query means the published snapshot.
- `/sandboxes/:id/preview?scope=<shared|ws-id>&port=<port>&path=<path>` — the
  embedded web viewer; target of every `PortPreview` launcher.

---

## Page 1: Fleet Board (`/`)

The conversion of `sandbox-manager-cli`. A grid of sandbox cards with
lifecycle actions.

```
┌──────────────────────────────────────────────────────────────────┐
│  EphemeralOS          ⌕ filter by id/state        [+ New Sandbox] │
├──────────────────────────────────────────────────────────────────┤
│  Fleet: 4 sandboxes · 3 ready · 1 failed · Σ mem 2.1G · Σ 47 layers
├──────────────────────────────────────────────────────────────────┤
│  ┌────────────────────┐  ┌────────────────────┐  ┌─────────────┐ │
│  │ eos-abc     ●Ready │  │ eos-def   ●Creating│  │ eos-ghi     │ │
│  │ img: ubuntu-24     │  │                    │  │  ●Failed    │ │
│  │ ~/ws/abc           │  │ progress log…      │  │             │ │
│  │ 2 sessions · 1 cmd │  │ (_stream_logs)     │  │ error msg   │ │
│  │ cpu ▁▂▅▃  mem ▃▃▄▅ │  │                    │  │             │ │
│  │ 12 layers          │  │                    │  │             │ │
│  │ [Open] [Squash] [✕]│  │        [✕]         │  │ [Inspect][✕]│ │
│  └────────────────────┘  └────────────────────┘  └─────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### Components

- **`FleetSummaryBar`** — aggregate counts from the no-arg `observability
  snapshot` (which already aggregates across ready sandboxes): sandbox count
  by state, total in-flight executions, total layers.
- **`SandboxCard`** — one per `SandboxRecord`. Shows: id, `StateBadge`
  (Creating/Ready/Stopping/Stopped/Failed — the exact `SandboxState` enum),
  workspace root, daemon endpoint health dot (backed by
  `GET /api/sandboxes/:id/health` → `daemon_http` `/health`),
  workspace-session count + in-flight execution count (from snapshot),
  `ResourceSparkline` (latest cgroup sample), layer count. Actions: Open → detail page, Squash
  (`checkpoint_squash`), Destroy (`destroy_sandbox`, confirm dialog).
- **`CreateSandboxModal`** — mirrors `create_sandbox` args exactly: image
  (required), workspace-bind-root (required), count (optional, ≥1 → creates N
  cards). Uses `_stream_logs: true` so the card shows the streamed progress
  log while in `Creating` state — the web equivalent of the CLI's
  `--progress` flag.
- **`ConfirmDestroyDialog`** — destroy is the one irreversible action;
  requires typing the sandbox id or explicit confirm.
- **`StateBadge`**, **`ResourceSparkline`** — shared, reused everywhere.

Polling: `list_sandboxes` + `snapshot` every few seconds; cards in
`Creating`/`Stopping` poll faster.

---

## Page 2: Sandbox Detail (`/sandboxes/:id`)

The conversion of `sandbox-runtime-cli` plus per-sandbox observability.
Persistent header, five tabs.

**`SandboxHeader`** (always visible): id, StateBadge, workspace root (the
record carries no image field — the image is a create-time argument only),
daemon endpoints (RPC + HTTP) with health dot, shared-base indicator, a
**`PortPreview`** launcher (enter a port + scope — shared, or an isolated
workspace session — and open the app in the Preview tab's embedded viewer),
and the two sandbox-level actions (Squash, Destroy). Backed by
`inspect_sandbox`.

### Tab 1 — Overview

The web rendering of `inspect_sandbox` + per-sandbox `snapshot`.

- **`RecordPanel`** — full `SandboxRecord` fields.
- **`WorkspaceSessionList`** — live sessions with layer counts, network
  profile (shared/isolated), and base root hash (the snapshot exposes
  `layers.base_root_hash` + `layer_count`; no manifest_version).
- **`InFlightExecutions`** — currently running commands; each links into
  the Terminal tab. The snapshot entry is `{namespace_execution_id,
  operation, lifecycle_state}` — no start time, so elapsed time only
  renders for commands the browser's own ledger knows.
- **`ResourceSnapshot`** — latest cgroup sample per scope (sandbox +
  per-workspace).

### Tab 2 — Terminal

The core interactive surface: a **ledger of per-command terminals**.
`exec_command` opens a terminal, `write_command_stdin` types into it — and
because every command owns its own transcript and stdin stream, several
terminals can run at once (a server tailing in one, a REPL in another),
which a single shared PTY could never offer. Two-pane layout:

```
┌───────────────┬──────────────────────────────────────────────────┐
│ SESSIONS      │  ▸ $ cargo build --release     ws-1   ✓ exit 0   │
│ ─────────     │  ▾ $ python server.py    ws-1         ● running  │
│ ◉ all         │  ┌────────────────────────────────────────────┐  │
│ ● implicit    │  │ Serving on 0.0.0.0:8000                    │  │
│ ● ws-1 shared │  │ GET /health 200                            │  │
│   ws-2 isol.  │  │ ▂ (tailing… lines 1–214 of 214)            │  │
│ [+ session]   │  └────────────────────────────────────────────┘  │
│               │  [stdin ______________________] [↵] [^C] [^D]    │
│               ├──────────────────────────────────────────────────┤
│               │  $ run in: [ws-1 ▾]  timeout: [30s ▾]  ________ ↵│
└───────────────┴──────────────────────────────────────────────────┘
```

#### Components

- **`SessionSidebar`** — lists workspace sessions under an **all** entry.
  Selection has one meaning, fixed here: picking a session filters the ledger
  to that session's commands and pre-fills the composer target; **all** (the
  default) leaves the ledger unfiltered. Create
  (`create_workspace_session` with a shared/isolated network-profile picker)
  and destroy (`destroy_workspace_session` with optional grace seconds).
  Destroy surfaces the API's refusal when the command ledger is non-empty,
  listing the blocking `active_command_session_ids` with jump-links.
- **`CommandComposer`** — the prompt line: command text, target session
  dropdown (or "implicit" — which per the API creates a one-shot session that
  captures + publishes on completion; the UI should label this
  "auto-publish"), optional timeout. Fires `exec_command` with
  `yield_time_ms: 0` and opens the new command's terminal expanded and
  focused. The two protocol knobs serve two audiences: `timeout_ms` is
  semantic (how long the command may live) and stays user-visible here;
  `yield_time_ms` is an agent-tool affordance ("block this RPC up to N ms
  before returning") — a browser is a polling client, so the console pins
  it to 0 on every call and never exposes it.
- **`CommandCard`** — one terminal per command. Expanded, it is a terminal
  frame: transcript filling the pane, input line integrated at the bottom,
  autoscroll pinned to the tail unless the user scrolls up. Collapsed, it is
  a one-line ledger history row. Always carries a session chip naming its
  owning session so the unfiltered ledger stays readable. States: running
  (spinner, elapsed) / completed / failed / timed-out, exit code. A terminal
  frame is not a PTY: line discipline only — REPLs and Ctrl-C-able servers
  work naturally; full-screen TUIs (vim, htop) will not render. Contains:
  - **`TranscriptViewer`** — virtualized log view tailing via
    `read_command_lines` (offset-windowed, ≤1000 lines/fetch — the stable
    line offsets make infinite scroll-back trivial). One API nuance: the
    `command_session_id` it polls with exists only when `exec_command`
    answered "still running after the initial wait"; a command that beats
    the wait returns terminal with its output inline, and the card renders
    that transcript directly with nothing to poll. `yield_time_ms: 0` makes
    the polling path the norm, but the inline path must still render.
    Leaving the tab never loses output — the transcript is
    server-authoritative, so on return the viewer catches up from its last
    stored offset (a burst of ≤1000-line pages if much happened) and
    re-pins to the tail.
  - **`StdinBar`** — the terminal's input line, rendered inside the frame
    only while status is running. Enter sends the line via
    `write_command_stdin` (yield pinned to 0) followed by an immediate
    `read_command_lines` poll nudge, so the program's reaction renders
    without waiting for the next interval tick. Ctrl-C/Ctrl-D are captured
    as keystrokes while the frame is focused; the explicit buttons remain
    for discoverability.
  - **`PortPreview`** ("open in browser") — for running commands that serve
    HTTP: pre-fills the command's session scope (shared vs. isolated ws-id),
    asks only for the port, and opens the Preview tab with scope and port
    pre-filled. For
    isolated sessions it shows the bind hint inline: listen on `0.0.0.0` or
    the workspace IP, not `127.0.0.1` (isolated loopback relay is an
    explicitly skipped `daemon_http` feature).

The ledger itself is client-remembered: the command family has no listing
operation, so known command ids (with command text and timestamps) persist
in `localStorage` per sandbox. A reload rebuilds the ledger from storage
plus the snapshot's in-flight executions — running commands always survive
a reload; completed ones survive on the browser that ran them. `#cmd-` deep
links share this dependency: they resolve for in-flight or locally-known
ids; a cold browser can still read a known id's transcript, just without
ledger context.

### Tab 3 — Files

```
┌───────────────┬──────────────────────────────────────────────────┐
│ FILE TREE     │  src/main.rs      session: [published ▾] [blame] │
│ ▾ src/        │ ┌─────┬──────────────────────────────────────────┐│
│    main.rs    │ │ 1   │ fn main() {          ░ ws-1              ││
│    lib.rs     │ │ 2   │     run();           ░ ws-1              ││
│ ▸ tests/      │ │ 3   │ }                    ▒ original          ││
│   Cargo.toml  │ └─────┴──────────────────────────────────────────┘│
│               │                            [Edit] [Save]          │
└───────────────┴──────────────────────────────────────────────────┘
```

#### Components

- **`FileTree`** — backed by the `file_list` op (closed API gap; option (a)
  as designed). As-built note: the spec-only addition wasn't sufficient by
  itself — snapshot listings needed a merged-view walk in
  `sandbox-runtime-layerstack` (`LayerStack::list_dir`) and live-session
  listings a `FileRunnerOp::ListDir` namespace runner op, both additive.
- **`SessionScopePicker`** — the most important control on this tab: view
  either the **latest published snapshot** (no session id) or a **live
  session's mounted workspace** (with session id). This mirrors the API's
  dual mode exactly and makes the layer model visible to the user.
- **`FileViewer`** — windowed text view (`file_read`, 2000-line windows with
  offset paging, truncation indicator).
- **`BlameGutter`** — toggle that colors line ranges by owner from
  `file_blame`: `workspace_session:<id>` / `operation:<id>` / `original` /
  `unknown`, with a legend and click-through (session owners link to
  Terminal, operation owners link to their trace in the Observability tab).
  `file_blame` is published-snapshot-only — it takes no session id — so the
  toggle is enabled only when `SessionScopePicker` is on **published**; in
  live-session scope it renders disabled with a hint, never pairing snapshot
  ownership with live content. This is the whole auditability surface of the
  console.
- **`FileEditor`** — edit mode; Save maps to `file_write`. Because
  `file_write` replaces the whole file, entering edit mode first pages
  `file_read` to the end so the buffer always holds the complete file —
  saving from a truncated window would destroy everything outside it; files
  past a size threshold open read-only. Save guards against concurrent
  writers (agents share the workspace): re-read, compare with the load-time
  content, and refuse with a reload prompt when it changed. (Exposing
  `file_edit`'s exact-string-replacement JSON in a UI isn't useful for humans
  — that op stays agent-only.)

### Tab 4 — Observability

Sub-navigation across the four per-sandbox views, mirroring the observability
catalog exactly.

#### Resources (`cgroup`)

```
┌──────────────────────────────────────────────────────────────────┐
│ [Resources] [Traces] [Events] [LayerStack]                       │
├──────────────────────────────────────────────────────────────────┤
│ scope: [sandbox ▾]      window: [60s ▾ (max 600s)]   ⟳ auto 5s   │
├────────────────────────────────┬─────────────────────────────────┤
│ CPU (Δ cpu_usec)               │ Memory (mem_cur)                │
│   ▁▂▃▅▆▅▃▂▁▂▃▅▆▇▆▅            │   ▃▃▄▄▅▅▅▆▆▆▆▆▇▇▇▇             │
├────────────────────────────────┼─────────────────────────────────┤
│ IO (Δ read/write bytes)        │ Disk upperdir (bytes · files)   │
│   ▁▁▂▁▁▅▁▁▁▂▁▁▁▁▁▁            │   ▂▂▂▃▃▃▃▄▄▄▄▄▄▅▅▅             │
└────────────────────────────────┴─────────────────────────────────┘
```

- **`ResourceCharts`** — CPU / memory / IO / disk-upperdir time series.
  Counters rendered as deltas (the sample format flags monotonic
  `_counters`).
- **`ScopePicker`** — sandbox vs. per-workspace-id, mirroring the op's
  `--scope` arg; window selector caps at the API's 600s max.

#### Traces (`trace`)

```
┌──────────────────────────────────────────────────────────────────┐
│ [Resources] [Traces] [Events] [LayerStack]                       │
├───────────────┬──────────────────────────────────────────────────┤
│ TRACES        │ trace req-7f3                     total 1048ms   │
│ ● req-7f3 1.0s│                                                  │
│   req-7f2 0.3s│ daemon.dispatch   █████████████████████████ ✓    │
│   req-7f1 2.1s│  command.exec        ████████████████ 890ms ✓    │
│   (last ▾)    │   namespace.exec.run_shell  ████████ 620ms ✓     │
│               │    ⚑ lease.acquired                              │
│               │   layerstack.publish             ███ 120ms ✓     │
│               │    ⚑ lease.released                              │
│               ├──────────────────────────────────────────────────┤
│               │ ▸ command.exec attrs: exit_code=0,               │
│               │   finalize_policy=publish_then_destroy, …        │
└───────────────┴──────────────────────────────────────────────────┘
```

- **`TraceList`** — picks the trace; defaults to the op's `last` selector.
  As-built: no trace-enumeration op exists, so the list shows `last` plus
  trace ids discovered from recent `events` (and any id navigated to via a
  deep link).
- **`TraceWaterfall`** — nested span bars offset by start time, colored by
  status (completed/error/cancelled/timed_out), events (⚑) pinned inline at
  their timestamp. Span names come from the fixed vocabulary
  (`daemon.dispatch`, `command.exec`, `layerstack.squash`, …) so they get
  stable icons/colors.
- **`SpanAttrsDrawer`** — expandable per-span `attrs` key/value panel.

#### Events (`events`)

```
┌──────────────────────────────────────────────────────────────────┐
│ [Resources] [Traces] [Events] [LayerStack]                       │
├──────────────────────────────────────────────────────────────────┤
│ name: [lease.* ____]  since: [15m ▾]  last-N: [200]  [● tail]    │
├──────────┬─────────────────┬─────────┬───────────────────────────┤
│ ts       │ name            │ trace   │ attrs                     │
│ 12:00:05 │ lease.released  │ req-7f3 │ {layer: a3f9…}        ▸   │
│ 12:00:04 │ lease.acquired  │ req-7f3 │ {layer: a3f9…}        ▸   │
│ 11:58:11 │ lease.acquired  │ req-7f1 │ {layer: 77c2…}        ▸   │
└──────────┴─────────────────┴─────────┴───────────────────────────┘
```

- **`EventStream`** — newest-first table with the API's exact filters (name,
  since-ms, last-N) plus a live-tail toggle. The trace cell links into the
  waterfall view via `trace` id.

#### LayerStack (`layerstack`)

```
┌──────────────────────────────────────────────────────────────────┐
│ [Resources] [Traces] [Events] [LayerStack]                       │
├──────────────────────────────────────────────────────────────────┤
│ workspace: [all ▾]                     [Squash ▸] 12 → est. 4    │
├──────────────────────────────────┬───────────────────────────────┤
│ L12  a3f9…  v12    4.2MB  ws-1   │  stack depth (window)         │
│ L11  77c2…  v11    1.1MB  —   ┐  │  ▂▂▃▃▃▅▅▅▆▆ 12               │
│ L10  b1e0…  v10   12.8MB  —   │s │                               │
│  …           …       …    …   │q │  disk by layer                │
│ L4   09dd…  v4     0.3MB  —   ┘  │  ▇▂▁▅▁▁▂▁▃▁▁▂                │
│ L0   base  v0    812MB  ws-1,ws-2│                               │
└──────────────────────────────────┴───────────────────────────────┘
```

- **`LayerStackViz`** — the stack as a vertical column of layers. As-built,
  the view returns `layer_id`, `bytes`, `leased_by_workspaces` (a count, not
  workspace ids), and `booked_by` per layer plus stack-level
  manifest_version/root_hash — so the per-layer workspace chips and filter
  from the mockup aren't derivable; squashable runs are bracketed
  (contiguous unleased, unbooked layers) and the depth trend accumulates
  client-side across polls.
- **`SquashButton`** — the natural home for `checkpoint_squash`. A pre-run
  "est. after" count is **not derivable** (risk confirmed): the header shows
  the before-count and the after-count comes from the post-squash refetch;
  the result body reports `squashed_blocks`. Runs with `_stream_logs` into a
  `StreamLogPane`.

### Tab 5 — Preview

The embedded web viewer for anything serving HTTP inside the sandbox, on any
port. Purely client-side: it renders the console's existing `/s/:id/…`
preview proxy in an iframe — no new server surface.

```
┌──────────────────────────────────────────────────────────────────┐
│ scope: [shared ▾]   port: [5173]   path: [/dashboard]  ⟳  [↗ tab]│
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│         <iframe src="/s/eos-abc/shared/5173/dashboard">          │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

- **`WebPreviewPane`** — iframe over `/s/:id/<scope>/<port>/<path>`.
  Controls: scope picker (shared, or an isolated workspace session — showing
  the bind hint: listen on `0.0.0.0` or the workspace IP), free-form port,
  editable path, refresh, open-in-new-tab. The proxy makes the app
  same-origin, so embedding needs no CORS work, and the proxy's WebSocket
  tunneling means HMR dev servers work embedded.
- Every `PortPreview` launcher (`SandboxHeader`, running `CommandCard`s)
  deep-links here with scope and port pre-filled instead of opening a bare
  new tab; new-tab stays one click away for full-window use.
- Embedding caveat: an app that sends `X-Frame-Options` / CSP
  `frame-ancestors` won't render in an iframe — the pane detects the blocked
  load and offers the new-tab fallback. Apps emitting absolute URLs break
  exactly as they do under `daemon_http` v0; same answer, don't fix in v0.

---

## Shared component library

`StateBadge` · `ResourceSparkline` · `TranscriptViewer` · `BlameGutter` ·
`ConfirmDestroyDialog` · `PortPreview` · `StreamLogPane` (renders
`_stream_logs` progress lines, streamed to the browser over SSE; used by
create/destroy/squash) · `ErrorToast` (renders the protocol's
`{kind, message, details}` error shape uniformly) · `PollController`
(per-page polling: fast ~300–500ms for visible running surfaces, ~2s for
backgrounded ones, idle decay with instant recovery on new output, and
interaction nudges that fire an immediate read after `exec_command` /
`write_command_stdin`; pauses on hidden tabs or unmounted views and fires an
immediate catch-up refetch on return — every consumer resumes from its last
cursor, never from scratch).

## API gaps to close before building

1. **`file_list`** operation (directory listing) — blocks the file tree.
2. **`sandbox-console` HTTP server** — blocks everything; six routes, spec in
   [[http-server]]. RPC and preview pass through 1:1 to the gateway protocol
   and `daemon_http` respectively; no other backend work needed — `/health`
   and `/forward` already exist on `daemon_http`.
3. Optional, nice-to-have: `list_command_sessions` (the command family
   can't enumerate past commands — the web ledger works around it with
   `localStorage`, but a fresh browser sees only in-flight commands), a
   fleet-wide event feed (currently `events` is per-sandbox only), and
   binary file transfer (`file_read`/`file_write` are UTF-8-text-only).
