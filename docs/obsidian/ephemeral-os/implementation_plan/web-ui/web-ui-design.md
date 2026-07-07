# EphemeralOS Web Console — Design Draft

A web UI for sandbox management: the conversion of `sandbox-manager-cli` and
`sandbox-runtime-cli` to the browser. Every page below is grounded in an
operation that exists today in `sandbox-manager-operations`,
`sandbox-runtime-operations`, or `sandbox-observability-operations`.

## Architecture note (constrains everything below)

The gateway speaks newline-delimited JSON over raw TCP, so the browser talks
to a thin **`sandbox-console` HTTP server** — a client peer of the two CLIs,
built on `sandbox-cli-core`'s `GatewayClient` — that bridges RPC to the
gateway and reverse-proxies the per-sandbox `daemon_http` surface (`/health`
plus `/forward` port forwarding). Endpoint spec: [[http-server]]. Operations
map 1:1 — the bridge adds no vocabulary. Three realities shape the UI:

- **Command output is transcript-based, not raw PTY.** The exposed API is
  `exec_command` → `read_command_lines` (stable line offsets) →
  `write_command_stdin`. So the "terminal" should be a **command ledger** (one
  card per command with a live-tailing transcript), not an xterm.js raw PTY
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

Two real pages, with the detail page tabbed. A separate fleet-wide
observability page isn't warranted yet — `events`/`trace`/`cgroup` all require
`--sandbox-id`, so fleet-wide aggregation only exists for `snapshot`, which
the board already shows. There is no dedicated audit page: auditability is
file blame, and it surfaces as the `BlameGutter` inside the Files tab.

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
Persistent header, four tabs.

**`SandboxHeader`** (always visible): id, StateBadge, image, workspace root,
daemon endpoints (RPC + HTTP) with health dot, shared-base indicator, a
**`PortPreview`** launcher (enter a port + scope — shared, or an isolated
workspace session — and open the app in a new tab through the console's
`/s/:id/…` preview proxy), and the two sandbox-level actions (Squash,
Destroy). Backed by `inspect_sandbox`.

### Tab 1 — Overview

The web rendering of `inspect_sandbox` + per-sandbox `snapshot`.

- **`RecordPanel`** — full `SandboxRecord` fields.
- **`WorkspaceSessionList`** — live sessions with layer counts, network
  profile (shared/isolated), base revision (manifest_version, root_hash).
- **`InFlightExecutions`** — currently running commands with elapsed time;
  each links into the Terminal tab.
- **`ResourceSnapshot`** — latest cgroup sample per scope (sandbox +
  per-workspace).

### Tab 2 — Terminal

The core interactive surface. Two-pane layout:

```
┌───────────────┬──────────────────────────────────────────────────┐
│ SESSIONS      │  ▸ $ cargo build --release            ✓ exit 0   │
│ ─────────     │  ▾ $ python server.py                 ● running  │
│ ● implicit    │  ┌────────────────────────────────────────────┐  │
│ ● ws-1 shared │  │ Serving on 0.0.0.0:8000                    │  │
│   ws-2 isol.  │  │ GET /health 200                            │  │
│ [+ session]   │  │ ▂ (tailing… lines 1–214 of 214)            │  │
│               │  └────────────────────────────────────────────┘  │
│               │  [stdin ______________________] [↵] [^C] [^D]    │
│               ├──────────────────────────────────────────────────┤
│               │  $ run in: [ws-1 ▾]  timeout: [30s ▾]  ________ ↵│
└───────────────┴──────────────────────────────────────────────────┘
```

#### Components

- **`SessionSidebar`** — lists workspace sessions; create
  (`create_workspace_session` with a shared/isolated network-profile picker)
  and destroy (`destroy_workspace_session` with optional grace seconds).
  Destroy surfaces the API's refusal when the command ledger is non-empty,
  listing the blocking `active_command_session_ids` with jump-links.
- **`CommandComposer`** — the prompt line: command text, target session
  dropdown (or "implicit" — which per the API creates a one-shot session that
  captures + publishes on completion; the UI should label this
  "auto-publish"), optional timeout. Fires `exec_command`.
- **`CommandCard`** — one per command, collapsible. States: running (spinner,
  elapsed) / completed / failed / timed-out, exit code. Contains:
  - **`TranscriptViewer`** — virtualized log view tailing via
    `read_command_lines` (offset-windowed, ≤1000 lines/fetch — the stable
    line offsets make infinite scroll-back trivial).
  - **`StdinBar`** — text input plus Ctrl-C/Ctrl-D buttons, mapped to
    `write_command_stdin`; only rendered while status is running.
  - **`PortPreview`** ("open in browser") — for running commands that serve
    HTTP: pre-fills the command's session scope (shared vs. isolated ws-id),
    asks only for the port, and opens the app through the preview proxy. For
    isolated sessions it shows the bind hint inline: listen on `0.0.0.0` or
    the workspace IP, not `127.0.0.1` (isolated loopback relay is an
    explicitly skipped `daemon_http` feature).

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

- **`FileTree`** — ⚠️ **API gap**: the file family has read/write/edit/blame
  but **no directory-listing operation**. Options: (a) add a `file_list` op
  to `sandbox-runtime-operations` (recommended — cheap, spec-only crate makes
  it additive), or (b) back the tree with `exec_command find` (ugly: creates
  implicit sessions and pollutes the audit trail). The design assumes (a).
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
  This is the whole auditability surface of the console.
- **`FileEditor`** — edit mode; Save maps to `file_write`. (Exposing
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

- **`LayerStackViz`** — the stack as a vertical column of layers (root_hash,
  manifest_version, disk bytes, lease/booking counts per workspace), with
  squashable blocks bracketed, plus a workspace filter and stack-depth trend
  from the stack series.
- **`SquashButton`** — the natural home for `checkpoint_squash`, showing
  before/after layer counts — it turns an opaque CLI verb into something
  visual. Runs with `_stream_logs` into a `StreamLogPane`.

---

## Shared component library

`StateBadge` · `ResourceSparkline` · `TranscriptViewer` · `BlameGutter` ·
`ConfirmDestroyDialog` · `PortPreview` · `StreamLogPane` (renders
`_stream_logs` progress lines, streamed to the browser over SSE; used by
create/destroy/squash) · `ErrorToast` (renders the protocol's
`{kind, message, details}` error shape uniformly) · `PollController`
(per-page polling with fast/slow modes).

## API gaps to close before building

1. **`file_list`** operation (directory listing) — blocks the file tree.
2. **`sandbox-console` HTTP server** — blocks everything; six routes, spec in
   [[http-server]]. RPC and preview pass through 1:1 to the gateway protocol
   and `daemon_http` respectively; no other backend work needed — `/health`
   and `/forward` already exist on `daemon_http`.
3. Optional, nice-to-have: a fleet-wide event feed (currently `events` is
   per-sandbox only) and binary file transfer (`file_read`/`file_write` are
   UTF-8-text-only).
