/* =====================================================================
   data.js — THE ONLY FAKED PART.
   In the real system every object below is a projection of the Log
   (facts, seats, tasks, provenance). Here it is static mock data so the
   UI can be viewed without a running machine. The LLM agents' words and
   activity are canned; the UI rendering (app.js) is real.
   ===================================================================== */
window.DATA = {
  workspace: { name: "Acme", budget: 62 },

  /* seats / agents — presence = occupant activation state */
  agents: {
    orchestrator: { id: "orchestrator", name: "Orchestrator", role: "orchestrator", presence: "live",    color: "#7c6cff", initials: "OR" },
    "coord-api":  { id: "coord-api",  name: "Coord-API",  role: "coordinator", presence: "working", color: "#22d3ee", initials: "API" },
    "coord-ui":   { id: "coord-ui",   name: "Coord-UI",   role: "coordinator", presence: "working", color: "#f472b6", initials: "UI" },
    research:     { id: "research",   name: "Research",   role: "background",  presence: "idle",    color: "#a3e635", initials: "RS" },
    "w7f":        { id: "w7f", name: "w#7f", role: "worker", presence: "working", color: "#fbbf24", initials: "7F" },
    "w9c":        { id: "w9c", name: "w#9c", role: "worker", presence: "working", color: "#fb923c", initials: "9C" },
    "w3a":        { id: "w3a", name: "w#3a", role: "worker", presence: "done",    color: "#34d399", initials: "3A" },
  },

  workerCount: 6,

  /* rooms = chatrooms, one per user task. order matters for the rail */
  roomOrder: ["ship-billing-v2", "fix-login-bug", "q3-research", "dep-watch"],

  rooms: {
    "ship-billing-v2": {
      id: "ship-billing-v2", name: "ship-billing-v2", status: "active", unread: 3,
      members: ["orchestrator", "coord-api", "coord-ui"],
      userTask: { title: "ship billing v2", criteria: "tests pass · UI ships currency" },
      /* messages double as facts: friendly text + mechanical fact */
      messages: [
        { id: "p01", author: "orchestrator", time: "9:01", kind: "message",
          friendly: 'Breaking this into three: schema, API, UI. @Coord-API take API, @Coord-UI take UI.',
          topic: "Plan", payload: "{tasks:[schema,api,ui]}", prov: ["req#1"], fires: "assign×2" },
        { id: "a91", author: "coord-api", time: "9:02", kind: "message", status: "working",
          friendly: 'API shape v1 is up. The line item needs a @Coord-UI <code>currency</code> field though — agree?',
          topic: "ApiShape", payload: "{v:1, by:coord-api}", prov: ["p01"], fires: "" },
        { id: "a92", author: "coord-ui", time: "9:03", kind: "message",
          friendly: 'Agreed — <code>currency</code> on the line item, or the UI can’t render totals.',
          topic: "Demand", payload: "{field:currency, scope:api}", prov: ["a91"], fires: "" },
        { id: "a94", author: "coord-api", time: "9:03", kind: "decision",
          friendly: 'Locking it: API shape v2 with <code>currency</code>.',
          topic: "Decision", payload: "{api-shape:v2}", prov: ["a91", "a92"], fires: "api-impl.ready" },
        { id: "d14", author: "coord-api", time: "9:14", kind: "message",
          friendly: 'api-impl is done — kicking off the test workflow.',
          topic: "TaskDone", payload: "{task:api-impl}", prov: ["a94"], fires: "api-tests.ready" },
      ],
      tasks: [
        { id: "schema-spec", name: "schema-spec", state: "done",    assignee: "w3a" },
        { id: "schema-mig",  name: "schema-mig",  state: "running", assignee: "w7f", progress: 41 },
        { id: "api-impl",    name: "api-impl",    state: "done",    assignee: "coord-api" },
        { id: "api-tests",   name: "api-tests",   state: "running", assignee: "w9c", progress: 67 },
        { id: "ui-spec",     name: "ui-spec",     state: "ready",   assignee: "coord-ui" },
        { id: "ui-render",   name: "ui-render",   state: "blocked", assignee: null, blockedBy: "needs api" },
      ],
      /* rolled-up view for the inspector */
      rollup: [
        { name: "schema", state: "done" },
        { name: "api",    state: "running" },
        { name: "ui",     state: "blocked" },
      ],
      dag: [
        { id: "schema-spec", state: "done" }, "->",
        { id: "schema-mig",  state: "running" }, "->",
        { id: "api-impl",    state: "done" }, "->",
        { id: "api-tests",   state: "running" },
        { branch: "api-impl  ->  ui-render  (blocked: needs api)" },
      ],
      provenance: {
        target: "api-tests",
        trace: [
          { who: "you", what: '"ship billing v2"', time: "9:00", cls: "you" },
          { who: "Orchestrator", what: "created user-task ship-billing-v2", time: "9:00" },
          { who: "Orchestrator", what: "decomposed → api slice → assigned Coord-API", time: "9:00" },
          { who: "Coord-API", what: "decomposed → api-impl ▶ api-tests", time: "9:01" },
          { who: "w#7f / Coord-API", what: "api-impl done ✓", time: "9:14", cls: "done" },
          { who: "kernel", what: "unblocked api-tests → ready → assigned w#9c", time: "9:14" },
          { who: "w#9c", what: "NOW running", time: "9:15" },
        ],
      },
    },

    "fix-login-bug": {
      id: "fix-login-bug", name: "fix-login-bug", status: "active", unread: 0,
      members: ["orchestrator", "coord-api"],
      userTask: { title: "fix the login redirect bug", criteria: "repro gone · regression test added" },
      messages: [
        { id: "l01", author: "orchestrator", time: "8:30", kind: "message",
          friendly: "Single slice — handing the whole thing to @Coord-API.",
          topic: "Plan", payload: "{tasks:[repro,fix,test]}", prov: ["req#2"], fires: "assign" },
        { id: "l02", author: "coord-api", time: "8:41", kind: "message",
          friendly: "Reproduced. It’s a stale redirect cookie. Patch + regression test in a worker now.",
          topic: "Finding", payload: "{cause:stale-cookie}", prov: ["l01"], fires: "fix.ready" },
      ],
      tasks: [
        { id: "repro", name: "reproduce", state: "done", assignee: "w3a" },
        { id: "fix", name: "patch", state: "running", assignee: "w7f", progress: 80 },
        { id: "regtest", name: "regression-test", state: "blocked", assignee: null, blockedBy: "needs patch" },
      ],
      rollup: [ { name: "reproduce", state: "done" }, { name: "patch", state: "running" }, { name: "test", state: "blocked" } ],
      dag: [ { id: "repro", state: "done" }, "->", { id: "fix", state: "running" }, "->", { id: "regtest", state: "blocked" } ],
      provenance: { target: "patch", trace: [
        { who: "you", what: '"the login redirect is broken"', time: "8:29", cls: "you" },
        { who: "Orchestrator", what: "created user-task fix-login-bug → Coord-API", time: "8:30" },
        { who: "w#3a", what: "reproduced the bug ✓", time: "8:40", cls: "done" },
        { who: "Coord-API", what: "asserted Finding{stale-cookie} → unblocked patch", time: "8:41" },
        { who: "w#7f", what: "NOW patching", time: "8:42" },
      ] },
    },

    "q3-research": {
      id: "q3-research", name: "q3-research", status: "done", unread: 0,
      members: ["orchestrator", "research"],
      userTask: { title: "summarize Q3 competitor moves", criteria: "brief delivered" },
      messages: [
        { id: "r01", author: "research", time: "Mon", kind: "message",
          friendly: "Brief delivered: 4 competitor launches, 1 pricing change. Pinned in the report.",
          topic: "Finding", payload: "{report:q3-brief}", prov: ["tick#mon"], fires: "goal" },
      ],
      tasks: [ { id: "gather", name: "gather", state: "done", assignee: "w3a" }, { id: "brief", name: "write-brief", state: "done", assignee: "research" } ],
      rollup: [ { name: "gather", state: "done" }, { name: "brief", state: "done" } ],
      dag: [ { id: "gather", state: "done" }, "->", { id: "brief", state: "done" } ],
      provenance: { target: "brief", trace: [
        { who: "you", what: '"summarize Q3 competitor moves"', time: "Mon", cls: "you" },
        { who: "Research", what: "gathered + wrote brief ✓", time: "Mon", cls: "done" },
        { who: "Arbiter", what: "goal reached → room quiesced", time: "Mon", cls: "done" },
      ] },
    },

    "dep-watch": {
      id: "dep-watch", name: "dep-watch", status: "waiting", unread: 0,
      members: ["orchestrator", "research"],
      userTask: { title: "watch dependencies for CVEs", criteria: "monitor — runs forever" },
      messages: [
        { id: "w01", author: "research", time: "7:00", kind: "message",
          friendly: "Heartbeat: scanned 212 deps, nothing new. Sleeping until the next tick.",
          topic: "Finding", payload: "{cves:0}", prov: ["tick#7"], fires: "" },
      ],
      tasks: [ { id: "scan", name: "scan-deps", state: "ready", assignee: "research" } ],
      rollup: [ { name: "scan", state: "ready" } ],
      dag: [ { id: "scan", state: "ready" } ],
      provenance: { target: "scan", trace: [
        { who: "Clock", what: "tick (cron 2m)", time: "7:00", cls: "you" },
        { who: "Research", what: "scanned deps → Finding{cves:0}", time: "7:00", cls: "done" },
        { who: "kernel", what: "quiescent — waits for next tick", time: "7:00" },
      ] },
    },
  },

  /* canned agent reply used to fake the LLM when you steer (app.js) */
  fakeReply: {
    working: { author: "coord-api", text: "" },
    reply: {
      author: "coord-api", time: "now", kind: "message",
      friendly: "Got it — switching the billing cadence to <b>monthly</b>. Revising <code>schema-mig</code> and re-running api-tests.",
      topic: "ReviseSpec", payload: "{cadence:monthly}", prov: ["steer"], fires: "schema-mig.rework",
    },
  },
};
