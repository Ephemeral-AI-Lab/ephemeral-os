/* =====================================================================
   arbor-data.js — THE ONLY FAKED PART (the Arbor variant).
   In the real system this is a coordinator-authored Domain Model
   { shape:tree, schema:IdeaNode, comparator, projection, rollup, cycle }
   projected from the World. Here it is static mock data. The UI
   (arbor-app.js) is real — it renders whatever shape the model declares.
   ===================================================================== */
window.ARBOR = {
  task: "improve dev-signal F1 on the benchmark",
  budget: 71,
  cycleStep: "DISPATCH", // OBSERVE | IDEATE | SELECT | DISPATCH | BACKPROP | DECIDE
  champion: "1.1.1",
  comparator: "argmax(score) · promote if held-out test beats trunk by +1.0",

  // The Domain Model itself, shown in the rail to prove it is a config, not a type.
  domainModel: [
    ["schema", "IdeaNode{ hypothesis, status, score, insight, code_ref }"],
    ["shape", "tree   → frontier + ancestors free"],
    ["ops", "add · update · prune · propagate"],
    ["compare", "argmax(score) by direction"],
    ["project", "constraints (shape · root-insight · pruned · won)"],
    ["rollup", "LLM insight-synthesis (backprop)"],
    ["cycle", "observe→ideate→select→dispatch→decide"],
    ["exec", "sandbox(isolated) → artifact = branch"],
  ],

  agents: {
    coordinator: { id: "coordinator", name: "Coordinator", role: "research director", presence: "working", color: "#7c6cff", initials: "CO" },
  },
  // executors = ephemeral workers, each in an isolated git worktree
  executors: [
    { id: "w-c2", name: "w#c2", presence: "working", color: "#fbbf24", initials: "C2", node: "1.1.2", worktree: "wt-7", progress: 62 },
    { id: "w-a4", name: "w#a4", presence: "done", color: "#34d399", initials: "A4", node: "1.1.1", worktree: "wt-5" },
  ],

  nodes: {
    "ROOT":  { id: "ROOT",  parent: null,  depth: 0, hypothesis: "baseline pipeline", status: "merged", score: 71.0, insight: "baseline established; data is the bottleneck", code_ref: "trunk" },
    "1":     { id: "1",     parent: "ROOT", depth: 1, hypothesis: "augment training data", status: "merged", score: 74.2, insight: "augmentation helps (+3.2)", code_ref: "idea/1" },
    "1.1":   { id: "1.1",   parent: "1",   depth: 2, hypothesis: "+ back-translation", status: "merged", score: 79.1, insight: "back-translation > basic aug (+4.9)", code_ref: "idea/1.1" },
    "1.1.1": { id: "1.1.1", parent: "1.1", depth: 3, hypothesis: "+ mixup regularization", status: "done", score: 82.4, insight: "mixup adds +3.3 — best so far", code_ref: "idea/1.1.1" },
    "1.1.2": { id: "1.1.2", parent: "1.1", depth: 3, hypothesis: "+ cutout regularization", status: "running", score: null, insight: "", code_ref: "idea/1.1.2", exec: "w#c2", worktree: "wt-7", progress: 62 },
    "2":     { id: "2",     parent: "ROOT", depth: 1, hypothesis: "swap optimizer → AdamW", status: "pruned", score: 68.0, insight: "AdamW underperforms SGD here (-3.0)", code_ref: "idea/2" },
    "3":     { id: "3",     parent: "ROOT", depth: 1, hypothesis: "curriculum learning schedule", status: "pending", score: null, insight: "", code_ref: null },
    "4":     { id: "4",     parent: "ROOT", depth: 1, hypothesis: "ensemble the top-2 branches", status: "pending", score: null, insight: "", code_ref: null },
  },

  evidence: [
    { node: "1.1.1", score: 82.4, split: "dev", by: "w#a4", insight: "mixup +3.3" },
    { node: "1.1",   score: 79.1, split: "test", by: "w#9b", insight: "BT verified on held-out" },
    { node: "1",     score: 74.2, split: "dev", by: "w#3a", insight: "aug +3.2" },
    { node: "2",     score: 68.0, split: "dev", by: "w#5c", insight: "AdamW -3.0" },
  ],
  decisions: [
    { kind: "merge", node: "1.1", detail: "held-out test 79.1 > trunk 74.2 (+4.9) → merged to trunk" },
    { kind: "prune", node: "2",  detail: "no improvement over 3 attempts → pruned" },
  ],

  // canned coordinator ideation when you steer it
  fakeIdeate: { hypothesis: "+ label smoothing", status: "pending", score: null, code_ref: null },
};
