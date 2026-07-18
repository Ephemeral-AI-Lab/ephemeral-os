# AGENTS.md

Agent guidance for this repository. Follow `CLAUDE.md` for the full project
rules.

## Required rules

- Always work, commit, and push directly on `main`. Do not create side branches
  (including `agent/*`) or additional git worktrees.
- Rebuild the Docker sandbox gateway binary with
  `bin/start-sandbox-docker-gateway --rebuild-binary`.
- Use `sandbox-manager-cli`, `sandbox-runtime-cli`, and
  `sandbox-observability-cli` for manual sandbox operations.
