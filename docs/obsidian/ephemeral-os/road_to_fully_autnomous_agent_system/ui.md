# ephemeral-os — UI Design

Companion to [[spec]]. The brief: **user-friendly to view, fully autonomous underneath.** A live prototype lives in [`./ui/index.html`](./ui/index.html) — open it in a browser (only the agents' words/data are faked; the UI is real).

## The one idea

The friendly surface is a **team-chat workspace** (Slack + Linear). The machine underneath is the **fact-space** (spec §4–§12). Each is a **projection of the same Log**, so the friendly view can never disagree with the machine, and the machine runs whether or not anyone is watching.

```
                  ┌──────────── THE LOG (the only truth) ────────────┐
                  │  facts · provenance · timestamps                  │
                  └───┬──────┬───────┬────────┬────────┬─────────────┘
       project ↓      ▼      ▼       ▼        ▼        ▼
                  rooms   board   presence  why-trace  raw facts
                  (chat)  (tasks) (roster)  (forensic) (mechanical)
        ── humans pick a projection; the machine needs none of them to run ──
```

## Friendly ⇄ Mechanical mapping

This table is the whole design. Left is what a user sees; right is the spec concept it renders.

| Friendly surface | Mechanical (spec) |
|---|---|
| A **room** per project | a **Chatroom** = scoped World bound 1:1 to a user task (§11.2) |
| **Messages** | **Facts** asserted into that scoped World |
| **Presence dots** (● live, ⟳ working, ◌ idle) | **Seat** + **occupant** activation (live-idle / cached-idle, §9) |
| **@mention / DM** | **directed delivery** via the relevance router (§11.1) |
| **Tasks board** (blocked/ready/running/done) | the **Task DAG** + its state machine (§5.1) |
| "**assigned to** ◆w#7f" | the **binding fact** → an ephemeral worker |
| "**X is working…**" | an **occupant running** (its live frame) |
| **pinned ✦ Decision** | a **Decision fact** |
| **shared diff + Merge button** | a **ProposedDiff fact** + the **Merge Actuator** (§7.2) |
| **"why is this here?"** | a **provenance** walk |
| **typing a nudge / approve / pause** | the **Operator asserting a fact** (steering, §11/§13.7) |
| **unread badges** | **reactions fired** in your scope |
| **a domain panel** (tree / board / graph) | a **Domain Model** rendered by its `shape` (§4.4, seam 2) — the Idea Tree is one config |

## The views (in the prototype)

| View | What you see | Mechanically |
|---|---|---|
| **Chat** (default) | the selected room's transcript + a compose box | facts in a scoped World; compose = an Operator fact |
| **Friendly ⇄ Mechanical** toggle | flips the transcript between chat bubbles and raw `FACT Topic{…} #id ⤺from ⚡fires` rows | same Log, two projections — the signature demo |
| **Tasks** | a 4-column board (the state machine) + a dependency DAG | the Task Graph (§5) |
| **Agent** (click a seat) | presence, "now" (live frame), bound tasks, seat info | seat/occupant (§9); persistent seats vs ephemeral workers |
| **Why?** overlay | a readable causal trace back to your original sentence | the provenance DAG (§4.1) rendered as a story |
| **Inspector** (right) | the bound user task, rolled-up progress, steer buttons | the user-task + acceptance criteria |
| **Domain panel** | renders the coordinator's Domain Model by its declared shape | tree / board / graph over domain facts (§4.4) |

## The domain panel — one view, any structure

The console's center is **pluggable by `shape`** (Domain Model seam 2), not hardcoded to tasks:

| Domain Model `shape` | Friendly rendering | Example system |
|---|---|---|
| `dag` | the task board + dependency DAG | the default dev mission |
| `tree` | a search/idea tree with scored nodes, frontier, champion path | **Arbor** (research) |
| `set` | a champion / challenger leaderboard | optimization / tuning |
| `graph` | a knowledge graph / mind-map | research / knowledge work |

The same Friendly ⇄ Mechanical toggle applies: friendly = the structure (tree/board), mechanical = the raw domain facts (`IdeaNode{…}`, `Evidence{…}`). **There is no per-domain UI code** — the panel reads the shape and renders. The **Arbor variant** (`./ui/arbor.html`) is this exact panel set to `shape: tree`, proving the Idea Tree is a config, not a hardcoded view.

## Presence semantics (not cosmetic)

| Dot | UI meaning | Mechanical |
|---|---|---|
| ● live (green) | online now | occupant active, single-writer holds |
| ⟳ working (amber, pulsing) | doing something | occupant running a turn / workflow |
| ◌ idle (grey) | available | seat live-idle or cached-idle (still routable) |
| ✓ done (green) | finished | task done / goal reached |
| ~ waiting (blue) | parked | quiescent, waiting on a tick or dependency |

## Principles that keep it friendly *and* autonomous

- **Progressive disclosure.** Story first (chat). `Why?`, the DAG, and raw facts are one click away — never in your face.
- **Every view is derived.** No view holds state; all are projections of the Log, so they cannot lie or drift.
- **Steering is just talking.** Type in a room (→ Operator fact) or click approve/pause (→ a one-click fact). No separate control panel; the one gated action is **Merge** (touching real reality).
- **The window is a spectator's seat, not a driver's seat.** Close the tab and the machine keeps running (cron heartbeat, sensors, agents coordinating via facts). The chat is the literal coordination substrate — it just happens to read like a team talking.

## Running the prototype

```
open ui/index.html        # or double-click it
```

Try: switch rooms in the left rail · flip **Friendly ⇄ Mechanical** (top-right) · open **Tasks** · click an agent (e.g. Coord-API) for its live frame · click **Why is this happening?** in the inspector · type a steer like "make it monthly not weekly" and watch the faked agent respond.

**Arbor variant** — `open ui/arbor.html` — the same console with the Domain Model set to `shape: tree`: an Idea Tree of scored hypotheses, the arbor-cycle strip, ephemeral Executors in isolated worktrees, and the Friendly ⇄ Mechanical toggle (tree ⇄ `IdeaNode`/`Evidence` facts). It is *not* a new UI — it is the generic domain panel with a tree config.

**What's faked:** only `ui/data.js` (and `ui/arbor-data.js`) — the agents' words, presence, and domain data. In the real system every object there is a projection of the Log. **What's real:** `index.html` / `arbor.html`, `styles.css`, `app.js` / `arbor-app.js` — the layout, the projection toggle, presence rendering, the board/tree, provenance, and steering flow.
