#!/usr/bin/env node

// TurnCompleted trigger: when the turn count hits the given percentage of
// max_turns (argv[2], default 80), remind the model to wrap up and submit.
// Register one rule per percentage for a reminder ladder (e.g. 50, 80);
// each rule is stateless once-per-run via equality with its threshold
// turn, not `>=`. Percentages that round to the same turn both fire there.

const fs = require("node:fs");

const raw = process.argv[2] ?? "80";
const percent = Number(raw);
if (!Number.isFinite(percent) || percent <= 0 || percent > 100) {
  process.stderr.write(`budget-reminder: invalid percent argument "${raw}"`);
  process.exit(1);
}

const p = JSON.parse(fs.readFileSync(0, "utf8"));
const threshold = Math.ceil(p.facts.max_turns * (percent / 100));
if (p.event === "TurnCompleted" && p.facts.turn === threshold) {
  process.stdout.write(
    JSON.stringify({
      notification:
        `Turn ${p.facts.turn} of ${p.facts.max_turns} (${String(percent)}% of budget). ` +
        `Wrap up and submit via ${p.terminal_tool}.`,
    }),
  );
}
