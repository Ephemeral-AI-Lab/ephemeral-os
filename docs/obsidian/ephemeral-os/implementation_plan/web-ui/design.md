# Web Console — UI Guidance

Companion to [[web-ui-design]] (pages, components, API grounding) and
[[implementation-plan]] (phases). That doc says *what* to build; this one
fixes *how it looks, how it navigates, and what it's built with*. Where they
overlap, [[web-ui-design]] wins on behavior, this doc wins on presentation.

## 1. Theme — light, single, token-based

One theme in v0: **light**. No toggle, no dark islands. All colors are
defined once as CSS variables (design tokens), so a dark theme later is a
token swap, not a rework.

### Surfaces & text

- App background near-white (`#f7f8fa`-class neutral), cards and panes pure
  white, separated by **1px neutral borders — not drop shadows**. Hover =
  slight background tint, never elevation changes.
- Primary text near-black (`#1a1d21`-class), secondary text mid-gray.
  Contrast ≥ WCAG AA (4.5:1) for all text, including on colored badges.
- One accent color (a saturated blue) reserved for **interactive** things:
  links, primary buttons, active tab, focus rings. If it isn't clickable, it
  isn't accent-colored.

### State colors

Used by `StateBadge`, health dots, and `CommandCard` states — one shared
scale, always **paired with a text label, never color alone**:

| Meaning | Tone |
|---|---|
| Ready / ok / exit 0 | green |
| Creating / running (in progress) | blue |
| Stopping / timed-out | amber |
| Stopped / idle | neutral gray |
| Failed / error / unreachable | red |

`BlameGutter` owners get a categorical palette legible on white: stable hue
per workspace-session id (hash → hue), neutral for `original`, hatched gray
for `unknown`. The legend is mandatory, per [[web-ui-design]].

### Typography & density

- UI sans: `Inter` (fallback `system-ui`). Data mono: `ui-monospace` /
  `JetBrains Mono` for **everything machine-flavored** — ids, hashes, paths,
  transcripts, attrs, ports. The mono/sans split is the visual grammar of
  the console: mono means "this came from the sandbox".
- This is a dense operator console: 13–14px base, ~32px table/ledger rows,
  compact paddings. Generosity is reserved for empty states.
- Transcript panes stay light like everything else. If ANSI color survives
  the daemon's rendering, map it to a light-safe palette (readable dims on
  white); otherwise plain text.
- Focus is always visible: 2px accent outline, no `outline: none` anywhere.

## 2. Navigation

### The URL is the single source of truth

Every view, tab, sub-view, scope, and filter from the route map in
[[web-ui-design]] lives in the path or query string. Refresh restores the
exact view; Back always works; every deep link resolves from a cold load.
Corollary state rule: **if it survives refresh, it's in the URL; if it's
server data, it's in query cache; anything else is local component state.**

### Structure

- **Top bar** (every page): product mark → `/` (Fleet Board), then a
  breadcrumb — `Fleet / eos-abc / Terminal`. Nothing on the right in v0
  (no auth, no user menu).
- **Detail page**: persistent `SandboxHeader`, routed tab bar beneath it
  (Overview is the index route). Observability's four views are a routed
  second-level sub-nav. Tabs and sub-tabs are real links, not local state.
- **Cross-linking rule** — the console's most important navigation feature:
  every id rendered anywhere (session chip, command id, trace id, layer
  hash) is a link to that id's home surface, styled mono with accent on
  hover. This is how users learn the system's model; it must be universal,
  not case-by-case.
- **Keyboard**, modest in v0: `/` focuses the fleet filter, `1–5` switch
  detail tabs, `Esc` closes dialogs. Nothing that shadows browser defaults.

### Motion, loading, failure

- First load of any pane: skeleton placeholder, not a spinner.
- Poll refreshes render **in place with zero layout shift**: fixed row
  heights, fixed sparkline widths, keep-previous-data during refetch —
  a polling console that flickers is unusable.
- On error: pane keeps its last good data with a subtle stale indicator;
  `ErrorToast` (bottom-right) carries the protocol `{kind, message,
  details}`. Never blank a pane because one poll failed.
- Every list surface has a designed empty state: one line of explanation
  plus the primary action (no sessions → `[+ session]`, no sandboxes →
  `[+ New Sandbox]`).

## 3. Stack — yes React, and a deliberately small package set

**React 19 + TypeScript + Vite.** The deciding factor isn't React itself —
it's that this UI lives or dies on three hard primitives (virtualized
transcript tailing, nested deep-linkable routing, poll-cached server state),
and the React ecosystem has the most mature implementation of each. Solid or
Svelte would save bundle kilobytes and cost maturity exactly where this
console can't afford it.

| Need | Package | Why this one |
|---|---|---|
| Routing | `react-router` v7 | the spec's route map — nested tabs, `#cmd-` anchors, query-param scopes — maps 1:1 |
| Server state / polling | TanStack Query v5 | `refetchInterval` + visibility pause *is* `PollController`; keep-previous-data gives flicker-free polls |
| Virtualization | TanStack Virtual | `TranscriptViewer`, `FileViewer`, `EventStream` |
| Headless primitives | Radix UI, vendored via the shadcn pattern (see below) | dialogs/tabs/dropdowns/toasts accessible out of the box, zero imposed look |
| Styling | Tailwind CSS v4 | tokens from §1 as CSS variables; no runtime cost |
| File view/edit | CodeMirror 6 | its gutter extension is exactly `BlameGutter`; solid read-only + large-document behavior |
| Time-series charts | uPlot | tiny and fast for the Resources tab; sparklines are plain inline SVG |
| Icons | `lucide-react` | consistent stroke set, tree-shakeable |

### shadcn: a scaffolding technique, not a design system

"Using shadcn" here means one specific thing. shadcn/ui is not a library we
depend on — its CLI copies component source (Radix primitives + Tailwind
classes) into `web/console/src/components/`, and from that point the code is
ours: versionless, no upstream to track, restyled to the §1 tokens rather
than shadcn's default look. The only `package.json` entries it produces are
the underlying Radix primitives.

Its scope is the chrome only: dialog, tabs, dropdown, select, toast,
tooltip. The surfaces that make this console what it is — `CommandCard`,
`TranscriptViewer`, `SessionSidebar`, `BlameGutter`, `TraceWaterfall`,
`LayerStackViz`, sparklines — exist in no component catalog and are always
custom-built, never shadcn-generated.

### Hand-rolled by design

`TraceWaterfall` and `LayerStackViz` are **custom SVG/DOM** — no charting
library models trace waterfalls or layer stacks well, and wrestling one is
worse than drawing rectangles. Sparklines are plain inline SVG for the same
reason: uPlot is overkill at that size.

Explicitly **not** used:

- **No component kit** (MUI / Ant / Chakra): theme lock-in and comfortable
  defaults fight the dense ledger/terminal layouts this console is made of.
- **No xterm.js** — ledger, not PTY ([[web-ui-design]] architecture note).
- **No Redux/Zustand/MobX** in v0 — the state rule above leaves nothing for
  a global store to own.
- **No CSS-in-JS runtime**, no animation library (CSS transitions suffice).

Adding any dependency beyond this table needs a reason written into this
file — the repo's "prefer less" rule applies to `package.json` too.
