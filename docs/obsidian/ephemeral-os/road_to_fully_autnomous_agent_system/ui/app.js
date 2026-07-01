/* app.js — the REAL ui. Renders the faked data (data.js) into the console.
   Everything here — projections, the friendly/mechanical toggle, presence,
   the task board, provenance — is real UI logic. */
(function () {
  "use strict";
  const D = window.DATA;
  const YOU = { id: "you", name: "You", role: "human", color: "#cbd5e1", initials: "YOU", presence: "live" };
  const ST2P = { done: "done", running: "working", ready: "waiting", blocked: "idle" };

  const S = { view: "chat", room: "ship-billing-v2", agent: null, mode: "friendly" };

  /* ---------- helpers ---------- */
  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const A = (id) => (id === "you" ? YOU : D.agents[id]);
  const dot = (p) => `<span class="presence ${p || "idle"}"></span>`;
  const av = (a, cls = "") => `<span class="avatar ${cls}" style="background:${a.color}">${a.initials}</span>`;
  const mentions = (s) => s.replace(/@([\w-]+)/g, '<span class="mention">@$1</span>');

  /* ---------- topbar ---------- */
  function renderTopbar() {
    const w = D.workspace;
    $("#topbar").innerHTML = `
      <div class="brand"><div class="brand-logo"></div>
        <div class="brand-name"><b>ephemeral-os</b> <span>· ${w.name}</span></div></div>
      <div class="budget" title="mission budget">
        <span>budget</span><div class="budget-bar"><div class="budget-fill" style="width:${w.budget}%"></div></div>
        <span>${w.budget}%</span></div>
      <div class="tabs">
        <div class="tab ${S.view !== "tasks" ? "active" : ""}" data-action="view" data-view="chat">Chat</div>
        <div class="tab ${S.view === "tasks" ? "active" : ""}" data-action="view" data-view="tasks">Tasks</div>
      </div>
      <div class="spacer"></div>
      <div class="toggle">
        <span class="toggle-label">view as</span>
        <div class="seg">
          <button class="${S.mode === "friendly" ? "active" : ""}" data-action="mode" data-mode="friendly">Friendly</button>
          <button class="mech ${S.mode === "mechanical" ? "active" : ""}" data-action="mode" data-mode="mechanical">Mechanical</button>
        </div>
      </div>`;
  }

  /* ---------- rail ---------- */
  function roomGlyph(r) {
    if (r.unread) return `<span class="badge">${r.unread}</span>`;
    if (r.status === "done") return `<span class="status-glyph">✓</span>`;
    if (r.status === "waiting") return `<span class="status-glyph">~</span>`;
    const running = r.tasks.filter((t) => t.state === "running").length;
    return running ? `<span class="badge run">⟳${running}</span>` : "";
  }
  function renderRail() {
    const rooms = D.roomOrder.map((id) => {
      const r = D.rooms[id];
      return `<div class="room ${r.status} ${S.room === id && S.view !== "agent" ? "active" : ""}" data-action="room" data-id="${id}">
        <span class="room-hash">#</span><span class="room-name">${r.name}</span>${roomGlyph(r)}</div>`;
    }).join("");

    const agentIds = ["orchestrator", "coord-api", "coord-ui", "research"];
    const agents = agentIds.map((id) => {
      const a = D.agents[id];
      return `<div class="agent-row ${S.agent === id ? "active" : ""}" data-action="agent" data-id="${id}">
        ${av(a)}<div class="agent-meta"><div class="agent-name">${a.name} ${dot(a.presence)}</div>
        <div class="agent-role">${a.role}</div></div></div>`;
    }).join("");

    $("#rail").innerHTML = `
      <div class="rail-section"><div class="rail-title">Rooms · one per user task</div>${rooms}</div>
      <div class="rail-section"><div class="rail-title">Agents · seats</div>${agents}
        <div class="ephemeral-note">~ ${D.workerCount} workers (ephemeral)</div></div>
      <div class="legend">
        <span>${dot("live")} live</span><span>${dot("working")} working</span>
        <span>${dot("idle")} idle</span><span>${dot("done")} done</span><span>${dot("waiting")} waiting</span>
      </div>`;
  }

  /* ---------- chat (friendly + mechanical) ---------- */
  function msgFriendly(m) {
    const a = A(m.author);
    const tag = m.kind === "decision" ? '<span class="decision-tag">✦ Decision</span>' : "";
    const status = m.status ? `<span class="msg-status">${dot("working")} working</span>` : "";
    return `<div class="msg ${m.kind === "decision" ? "decision" : ""}">
      <div class="msg-avatar">${av(a)}</div>
      <div class="msg-body">
        <div class="msg-head"><span class="msg-author">${a.name}</span>
          <span class="msg-role-tag">${a.role}</span><span class="msg-time">${m.time}</span>${status}</div>
        <div class="msg-text">${tag}${mentions(m.friendly)}</div>
      </div></div>`;
  }
  function msgMechanical(m) {
    const a = A(m.author);
    const prov = m.prov && m.prov.length ? `<div class="fact-prov">⤺ from <b>${m.prov.map((p) => "#" + p).join(", ")}</b></div>` : "";
    const fires = m.fires ? `<div class="fact-fires">⚡ fires <b>${m.fires}</b></div>` : "";
    return `<div class="fact">
      <div class="fact-main"><span class="fact-kw">FACT</span><span class="fact-topic">${m.topic}</span>
        <span class="fact-payload">${esc(m.payload)}</span><span class="fact-id">#${m.id}</span></div>
      <div class="fact-by">by ${a.name} · ${m.time}</div>${prov}${fires}</div>`;
  }
  function renderChat(r) {
    const render = S.mode === "friendly" ? msgFriendly : msgMechanical;
    let body = r.messages.map(render).join("");
    if (r._typing) {
      const a = A("coord-api");
      body += `<div class="msg"><div class="msg-avatar">${av(a)}</div><div class="msg-body">
        <div class="msg-head"><span class="msg-author">${a.name}</span><span class="msg-status">${dot("working")} working…</span></div></div></div>`;
    }
    const memberAvatars = r.members.map((id) => av(A(id))).join("");
    return `
      <div class="center-head">
        <div><div class="center-title"><span class="hash">#</span>${r.name}</div>
          <div class="center-sub">${r.members.length} members · bound to user-task “${r.userTask.title}”</div></div>
        <div class="members">${memberAvatars}</div>
      </div>
      <div class="scroll ${S.mode === "mechanical" ? "facts" : ""}">${body}</div>
      <div class="compose">
        <div class="compose-box">
          <textarea class="compose-input" rows="1" placeholder="Message #${r.name} — steer the machine…"></textarea>
          <button class="compose-send" data-action="send">Send</button>
        </div>
        <div class="compose-hint">Steering = asserting a fact through the front door · <b>Enter</b> to send</div>
      </div>`;
  }

  /* ---------- task board + dag ---------- */
  function miniAv(a) { return `<span class="mini-av" style="background:${a.color}">${a.initials}</span>`; }
  function card(t) {
    const who = t.assignee ? `${miniAv(A(t.assignee))} ${A(t.assignee).name}` : "unassigned";
    const prog = t.progress != null ? `<div class="progress"><span style="width:${t.progress}%"></span></div>` : "";
    const blk = t.blockedBy ? `<div class="card-blocked-note">⤷ ${t.blockedBy}</div>` : "";
    return `<div class="card" data-action="why" data-task="${t.id}">
      <div class="card-name">${t.name}</div><div class="card-sub">${who}</div>${prog}${blk}</div>`;
  }
  function column(r, state) {
    const items = r.tasks.filter((t) => t.state === state);
    return `<div class="col ${state}"><div class="col-head"><span class="dot"></span>${state}<span class="col-count">${items.length}</span></div>
      ${items.map(card).join("")}</div>`;
  }
  function renderDag(r) {
    const chain = r.dag.filter((x) => x === "->" || x.id);
    const branches = r.dag.filter((x) => x.branch);
    const nodes = chain.map((x) => x === "->" ? '<span class="dag-arrow">→</span>'
      : `<span class="dag-node ${x.state}">${dot(ST2P[x.state])}${x.id}</span>`).join("");
    const br = branches.map((b) => `<div class="dag-branch">└▶ ${esc(b.branch)}</div>`).join("");
    return `<div class="dag-wrap"><div class="dag-title">dependency dag</div><div class="dag">${nodes}</div>${br}</div>`;
  }
  function renderTasks(r) {
    return `
      <div class="center-head"><div><div class="center-title">Tasks · ${r.name}</div>
        <div class="center-sub">columns are the task state machine — blocked → ready → running → done</div></div></div>
      <div class="board-wrap">
        <div class="board">${["blocked", "ready", "running", "done"].map((s) => column(r, s)).join("")}</div>
        ${renderDag(r)}
      </div>`;
  }

  /* ---------- agent detail ---------- */
  function agentNow(a) {
    if (a.role === "coordinator" && a.presence === "working")
      return { line: "authoring a workflow for <b>api-tests</b>", frame: "flow().dag({ lint ∥ tests ∥ types }) → branch(verdict) → result" };
    if (a.role === "worker") return { line: "running its bound task", frame: "tests: pytest -q … 41 passed, 2 pending" };
    if (a.role === "orchestrator") return { line: "live at the front door — waiting for a request or a coordinator update", frame: null };
    if (a.role === "background") return { line: "idle (cached) — wakes on the next cron tick", frame: null };
    return { line: "idle", frame: null };
  }
  function tasksFor(id) {
    const out = [];
    D.roomOrder.forEach((rid) => D.rooms[rid].tasks.forEach((t) => { if (t.assignee === id) out.push(Object.assign({ room: rid }, t)); }));
    return out;
  }
  function renderAgent(id) {
    const a = D.agents[id];
    const now = agentNow(a);
    const frame = now.frame ? `<div class="ad-frame">${esc(now.frame)}</div>` : "";
    const tasks = tasksFor(id);
    const taskRows = tasks.length
      ? tasks.map((t) => `<div class="ad-task">${dot(ST2P[t.state])}<span class="tname">${t.name}</span><span class="tstate">${t.state} · #${t.room}</span></div>`).join("")
      : `<div class="ad-seat">no bound tasks right now</div>`;
    const lifetime = a.role === "worker" ? "ephemeral · one task" : "persistent seat";
    return `
      <div class="agent-detail">
        <div class="ad-head">${av(a, "ad-avatar")}
          <div><div class="ad-name">${a.name} ${dot(a.presence)}</div>
            <div class="ad-meta">${a.role} · ${lifetime}</div></div>
          <div class="ad-budget">budget<br><b style="color:var(--text)">$4.10 / $8.00</b></div></div>
        <div class="ad-card"><h4>now</h4><div class="ad-now">${dot(a.presence)} ${now.line}</div>${frame}</div>
        <div class="ad-card"><h4>tasks</h4>${taskRows}</div>
        <div class="ad-card"><h4>seat</h4>
          <div class="ad-seat">${a.role === "worker"
            ? "ephemeral occupant · spawned on assignment · discards its sandbox on completion · holds no truth"
            : "persistent · activated 9:02 · <b>single-writer ✓</b> · rehydrated from 47 facts · process released when idle"}</div></div>
      </div>`;
  }

  /* ---------- inspector ---------- */
  function renderInspector() {
    const r = D.rooms[S.room];
    const subs = r.rollup.map((s) => `<div class="subtask">${dot(ST2P[s.state])}<span class="nm">${s.name}</span><span class="st">${s.state}</span></div>`).join("");
    const owner = r.members.find((m) => D.agents[m] && D.agents[m].role === "coordinator");
    $("#inspector").innerHTML = `
      <div class="insp-section">
        <div class="insp-title">This user task</div>
        <div class="insp-task-name">${r.userTask.title}</div>
        <div class="insp-task-sub">accept: ${r.userTask.criteria}</div>
      </div>
      <div class="insp-section"><div class="insp-title">Progress</div>${subs}</div>
      <div class="insp-section"><div class="insp-title">Details</div>
        <div class="kv"><span class="k">owner</span><span class="v">${owner ? D.agents[owner].name : "—"}</span></div>
        <div class="kv"><span class="k">members</span><span class="v">${r.members.length}</span></div>
        <div class="kv"><span class="k">room status</span><span class="v">${r.status}</span></div>
        <div class="kv"><span class="k">facts</span><span class="v">${r.messages.length}</span></div>
      </div>
      <div class="insp-section"><div class="insp-title">Steer</div>
        <div class="insp-actions">
          <div class="btn why" data-action="why" data-task="${r.provenance.target}">⤺ Why is this happening?</div>
          <div class="btn">⏸ Pause this task</div>
          <div class="btn primary">⤴ Merge proposed diff</div>
        </div></div>`;
  }

  /* ---------- provenance overlay ---------- */
  function openWhy(target) {
    const r = D.rooms[S.room];
    const p = r.provenance;
    const lines = p.trace.map((t) => `
      <div class="trace-line ${t.cls || ""}"><div class="trace-dot"></div>
        <div class="trace-who">${t.who} <span class="trace-time">${t.time}</span></div>
        <div class="trace-what">${t.what}</div></div>`).join("");
    $("#overlay").innerHTML = `
      <div class="overlay-card">
        <div class="overlay-head"><h3>Why is <code>${esc(p.target)}</code> here?</h3>
          <button class="overlay-close" data-action="close-why">×</button></div>
        <div class="trace">${lines}</div>
        <div class="overlay-foot">Every line is a fact with a timestamp. Provenance is stored, not reconstructed.</div>
      </div>`;
    $("#overlay").hidden = false;
  }

  /* ---------- compose / fake the LLM ---------- */
  function scrollChat() { const s = $(".scroll"); if (s) s.scrollTop = s.scrollHeight; }
  function send() {
    const input = $(".compose-input");
    if (!input) return;
    const text = input.value.trim();
    if (!text) return;
    const room = D.rooms[S.room];
    room.messages.push({ id: "u" + Date.now(), author: "you", time: "now", kind: "message",
      friendly: esc(text), topic: "Operator", payload: "{steer}", prov: [], fires: "" });
    render(); scrollChat();
    /* ===== FAKE the agent (the only simulated bit) ===== */
    setTimeout(() => { room._typing = true; render(); scrollChat(); }, 250);
    setTimeout(() => {
      room._typing = false;
      room.messages.push(Object.assign({}, D.fakeReply.reply, { id: "r" + Date.now(), time: "now" }));
      render(); scrollChat();
    }, 1500);
  }

  /* ---------- main render ---------- */
  function renderCenter() {
    if (S.view === "agent" && S.agent) return ($("#center").innerHTML = renderAgent(S.agent));
    if (S.view === "tasks") return ($("#center").innerHTML = renderTasks(D.rooms[S.room]));
    $("#center").innerHTML = renderChat(D.rooms[S.room]);
    scrollChat();
  }
  function render() { renderTopbar(); renderRail(); renderCenter(); renderInspector(); }

  /* ---------- events ---------- */
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-action]");
    if (!t) return;
    const a = t.dataset.action;
    if (a === "room") { S.room = t.dataset.id; S.view = "chat"; S.agent = null; D.rooms[S.room].unread = 0; render(); }
    else if (a === "agent") { S.agent = t.dataset.id; S.view = "agent"; render(); }
    else if (a === "view") { S.view = t.dataset.view; S.agent = null; render(); }
    else if (a === "mode") { S.mode = t.dataset.mode; render(); }
    else if (a === "why") { openWhy(t.dataset.task); }
    else if (a === "close-why") { $("#overlay").hidden = true; }
    else if (a === "send") { send(); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.target.classList && e.target.classList.contains("compose-input") && e.key === "Enter" && !e.shiftKey) {
      e.preventDefault(); send();
    }
    if (e.key === "Escape") $("#overlay").hidden = true;
  });
  $("#overlay").addEventListener("click", (e) => { if (e.target.id === "overlay") $("#overlay").hidden = true; });

  render();
})();
