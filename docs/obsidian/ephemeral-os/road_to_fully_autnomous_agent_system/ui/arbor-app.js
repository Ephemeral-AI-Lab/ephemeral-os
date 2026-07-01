/* arbor-app.js — the REAL ui for the Arbor variant. Renders the faked
   Domain Model (arbor-data.js) whose shape is `tree`. Nothing here knows
   the word "Arbor": it renders a tree of scored nodes, a frontier, a
   champion, and the cycle — i.e. whatever the DomainModel declares. */
(function () {
  "use strict";
  const M = window.ARBOR;
  const NODES = M.nodes;
  const CYCLE = ["OBSERVE", "IDEATE", "SELECT", "DISPATCH", "BACKPROP", "DECIDE"];
  const S = { mode: "friendly", selected: M.champion };

  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const dot = (p) => `<span class="presence ${p || "idle"}"></span>`;
  const av = (a, cls = "") => `<span class="avatar ${cls}" style="background:${a.color}">${a.initials}</span>`;
  const scorePct = (s) => Math.max(4, Math.min(100, ((s - 65) / 20) * 100));
  const children = (id) => Object.values(NODES).filter((n) => n.parent === id).map((n) => n.id).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  function treeOrder() { const out = []; (function walk(id) { out.push(id); children(id).forEach(walk); })("ROOT"); return out; }
  function ancestors(id) { const out = []; let c = id; while (c) { out.unshift(c); c = NODES[c].parent; } return out; }
  const CHAMP = new Set(ancestors(M.champion));

  /* ---------- topbar ---------- */
  function renderTopbar() {
    const cyc = CYCLE.map((s) => `<span class="cycle-step ${s === M.cycleStep ? "active" : ""}">${s}</span>`).join('<span class="cycle-sep">▸</span>');
    $("#topbar").innerHTML = `
      <div class="brand"><div class="brand-logo"></div><div class="brand-name"><b>ephemeral-os</b> <span>· research</span></div></div>
      <span class="dm-tag">Domain Model · <b>Idea Tree</b> · shape: tree</span>
      <div class="spacer"></div>
      <div class="cycle">${cyc}</div>
      <div class="budget"><span>budget</span><div class="budget-bar"><div class="budget-fill" style="width:${M.budget}%"></div></div><span>${M.budget}%</span></div>
      <div class="toggle"><span class="toggle-label">view as</span>
        <div class="seg">
          <button class="${S.mode === "friendly" ? "active" : ""}" data-action="mode" data-mode="friendly">Tree</button>
          <button class="mech ${S.mode === "mechanical" ? "active" : ""}" data-action="mode" data-mode="mechanical">Facts</button>
        </div></div>`;
  }

  /* ---------- rail ---------- */
  function renderRail() {
    const co = M.agents.coordinator;
    const coRow = `<div class="agent-row" data-action="node" data-node="ROOT">${av(co)}<div class="agent-meta"><div class="agent-name">${co.name} ${dot(co.presence)}</div><div class="agent-role">${co.role}</div></div></div>`;
    const execs = M.executors.map((e) => `
      <div class="exec-row ${S.selected === e.node ? "sel" : ""}" data-action="node" data-node="${e.node}">
        ${av(e)}<div class="exec-meta"><div class="exec-name">${e.name} ${dot(e.presence)}</div>
        <div class="exec-sub">${e.presence === "working" ? "testing" : "tested"} <b>#${e.node}</b> · <code>${e.worktree}</code></div></div></div>`).join("");
    const seams = M.domainModel.map(([k, v]) => `<div class="dm-seam"><span class="k">${k}</span><span class="v">${esc(v)}</span></div>`).join("");
    $("#rail").innerHTML = `
      <div class="rail-section"><div class="rail-title">Agents · seats</div>${coRow}${execs}</div>
      <div class="rail-section"><div class="rail-title">Domain Model · a config, not a type</div><div class="dm-seams">${seams}</div></div>`;
  }

  /* ---------- center: tree (friendly) ---------- */
  function nodeCard(id) {
    const n = NODES[id];
    const champ = CHAMP.has(id);
    let metric;
    if (n.score != null) metric = `<span class="node-score">${n.score.toFixed(1)}</span><span class="score-bar"><span style="width:${scorePct(n.score)}%"></span></span>`;
    else if (n.status === "running") metric = `<span class="run-note">${dot("working")} ${n.exec || "executor"} testing <code>${n.worktree || ""}</code></span>`;
    else metric = `<span class="frontier-note">◌ frontier (pending)</span>`;
    return `<div class="tree-row depth-${n.depth} ${champ ? "champ" : ""} ${id === S.selected ? "sel" : ""}" data-action="node" data-node="${id}">
      <span class="tree-id">${id}</span>
      <div class="node-card status-${n.status}">
        <div class="node-top"><span class="node-hyp">${esc(n.hypothesis)}</span>${id === M.champion ? '<span class="star">★</span>' : ""}</div>
        <div class="node-bot"><span class="status-pill ${n.status}">${n.status}</span>${metric}</div>
      </div></div>`;
  }
  function renderTree() { return treeOrder().map(nodeCard).join(""); }

  /* ---------- center: facts (mechanical) ---------- */
  function factRow(cls, topic, payload, id, prov, fires) {
    const p = prov ? `<div class="fact-prov">⤺ from <b>${prov}</b></div>` : "";
    const f = fires ? `<div class="fact-fires">⚡ fires <b>${fires}</b></div>` : "";
    return `<div class="fact ${cls}"><div class="fact-main"><span class="fact-kw">FACT</span><span class="fact-topic">${topic}</span>
      <span class="fact-payload">${esc(payload)}</span><span class="fact-id">#${id}</span></div>${p}${f}</div>`;
  }
  function renderFacts() {
    let h = '<div class="facts">';
    h += '<div class="fact-group">IdeaNode facts · shape: tree (parent links)</div>';
    treeOrder().forEach((id) => {
      const n = NODES[id];
      const pay = `{status:${n.status}${n.score != null ? `, score:${n.score}` : ""}${n.code_ref ? `, code_ref:${n.code_ref}` : ""}}`;
      h += factRow("", "IdeaNode", pay, id, n.parent ? "#" + n.parent : "", "");
    });
    h += '<div class="fact-group">Evidence facts · from executors</div>';
    M.evidence.forEach((e) => h += factRow("ev", "Evidence", `{score:${e.score}, split:${e.split}, by:${e.by}}`, "ev:" + e.node, "#" + e.node, "rollup (backprop)"));
    h += '<div class="fact-group">Decision facts · the comparator at work</div>';
    M.decisions.forEach((d) => h += factRow("dec", "Decision", `{${d.kind}: ${d.node}}`, "dec:" + d.node, "#" + d.node, d.kind === "merge" ? "Merge Actuator" : ""));
    h += "</div>";
    return h;
  }

  function renderCenter() {
    const legend = S.mode === "friendly"
      ? `<div class="tree-legend"><b>status:</b><span>merged = in trunk</span><span>done = tested</span><span>running = in a worktree</span><span>pending = frontier</span><span>pruned = dead</span> · <b>★ champion</b></div>`
      : "";
    $("#center").innerHTML = `
      <div class="center-head"><div>
        <div class="center-title">Idea Tree <span style="color:var(--faint);font-weight:500">· domain panel</span></div>
        <div class="center-sub">task: ${esc(M.task)} · champion #${M.champion} (${NODES[M.champion].score.toFixed(1)}) · ${M.cycleStep}</div></div></div>
      <div class="tree-wrap">${S.mode === "friendly" ? renderTree() : renderFacts()}</div>
      ${legend}
      <div class="compose"><div class="compose-box">
        <textarea class="compose-input" rows="1" placeholder="Steer the Coordinator — e.g. 'try label smoothing'…"></textarea>
        <button class="compose-send" data-action="steer">Send</button></div>
        <div class="compose-hint">Steering = an Operator fact the Coordinator reads on its next OBSERVE · <b>Enter</b> to send</div></div>`;
  }

  /* ---------- inspector ---------- */
  function renderInspector() {
    const n = NODES[S.selected] || NODES[M.champion];
    const anc = ancestors(n.id).map((a, i, arr) => `<span class="${a === n.id ? "cur" : ""}">${a}</span>${i < arr.length - 1 ? '<span class="sep">→</span>' : ""}`).join("");
    const evs = M.evidence.filter((e) => e.node === n.id);
    const evHtml = evs.length ? evs.map((e) => `<div class="ev-row"><span>${e.split} · ${e.by} · ${esc(e.insight)}</span><span class="s">${e.score.toFixed(1)}</span></div>`).join("") : "";
    $("#inspector").innerHTML = `
      <div class="insp-section"><div class="insp-title">Idea node #${n.id}</div>
        <div class="insp-task-name">${esc(n.hypothesis)}</div>
        <div class="insp-task-sub">${n.status}${n.score != null ? ` · score ${n.score.toFixed(1)}` : ""}</div></div>
      ${n.insight ? `<div class="insp-section"><div class="insp-title">Insight (rolls up to ancestors)</div><div class="insp-insight">${esc(n.insight)}</div></div>` : ""}
      <div class="insp-section"><div class="insp-title">Ancestry · free from shape:tree</div><div class="anc">${anc}</div></div>
      ${evHtml ? `<div class="insp-section"><div class="insp-title">Evidence</div>${evHtml}</div>` : ""}
      <div class="insp-section"><div class="insp-title">Details</div>
        <div class="kv"><span class="k">artifact</span><span class="v insp-branch">${n.code_ref || "—"}</span></div>
        <div class="kv"><span class="k">status</span><span class="v">${n.status}</span></div>
        <div class="kv"><span class="k">depth</span><span class="v">${n.depth}</span></div></div>
      <div class="insp-section"><div class="insp-title">Comparator (seam 4)</div><div class="insp-task-sub">${esc(M.comparator)}</div></div>
      <div class="insp-section"><div class="insp-title">Decide</div><div class="insp-actions">
        <div class="btn why" data-action="why" data-node="${n.id}">⤺ Why this node?</div>
        <div class="btn" data-action="branch" data-node="${n.id}">⎇ Branch a child idea</div>
        <div class="btn primary" data-action="promote" data-node="${n.id}">★ Promote to champion</div>
        <div class="btn" data-action="prune" data-node="${n.id}">✕ Prune</div>
      </div></div>`;
  }

  /* ---------- provenance overlay ---------- */
  function openWhy(id) {
    const lines = [{ who: "task", what: `"${esc(M.task)}"`, cls: "you" }];
    ancestors(id).forEach((a) => {
      const n = NODES[a];
      lines.push({ who: "#" + a, what: `${esc(n.hypothesis)}${n.score != null ? ` — score ${n.score.toFixed(1)}` : ` — ${n.status}`}`, cls: (n.status === "done" || n.status === "merged") ? "done" : "" });
    });
    M.evidence.filter((e) => e.node === id).forEach((e) => lines.push({ who: "Evidence", what: `${e.split} ${e.score.toFixed(1)} (${e.by}) → backprop up the tree`, cls: "done" }));
    const body = lines.map((t) => `<div class="trace-line ${t.cls || ""}"><div class="trace-dot"></div>
      <div class="trace-who">${t.who} <span class="trace-time"></span></div><div class="trace-what">${t.what}</div></div>`).join("");
    $("#overlay").innerHTML = `<div class="overlay-card">
      <div class="overlay-head"><h3>Why is <code>#${id}</code> here?</h3><button class="overlay-close" data-action="close-why">×</button></div>
      <div class="trace">${body}</div>
      <div class="overlay-foot">Each line is a fact. The ancestry is free from <code>shape: tree</code>; the evidence is what executors reported.</div></div>`;
    $("#overlay").hidden = false;
  }

  /* ---------- steer (fake the coordinator ideating) ---------- */
  function steer() {
    const input = $(".compose-input"); if (!input) return;
    const t = input.value.trim(); if (!t) return;
    const parent = NODES[S.selected] && NODES[S.selected].depth < 3 ? S.selected : "1.1";
    const newId = parent + "." + (children(parent).length + 1);
    NODES[newId] = { id: newId, parent: parent, depth: NODES[parent].depth + 1, hypothesis: t, status: "pending", score: null, insight: "", code_ref: null };
    M.cycleStep = "IDEATE"; S.selected = newId; input.value = "";
    render();
  }

  function render() { renderTopbar(); renderRail(); renderCenter(); renderInspector(); }

  /* ---------- events ---------- */
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-action]"); if (!t) return;
    const a = t.dataset.action, node = t.dataset.node;
    if (a === "mode") { S.mode = t.dataset.mode; render(); }
    else if (a === "node") { if (node) S.selected = node; render(); }
    else if (a === "why") { openWhy(node); }
    else if (a === "close-why") { $("#overlay").hidden = true; }
    else if (a === "steer") { steer(); }
    else if (a === "branch") { S.selected = node; const i = $(".compose-input"); if (i) i.value = M.fakeIdeate.hypothesis; steer(); }
    else if (a === "promote") { M.champion = node; if (NODES[node].status === "pending") NODES[node].status = "done"; CHAMP.clear(); ancestors(node).forEach((x) => CHAMP.add(x)); render(); }
    else if (a === "prune") { NODES[node].status = "pruned"; render(); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.target.classList && e.target.classList.contains("compose-input") && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); steer(); }
    if (e.key === "Escape") $("#overlay").hidden = true;
  });
  $("#overlay").addEventListener("click", (e) => { if (e.target.id === "overlay") $("#overlay").hidden = true; });

  render();
})();
