# eos-coding-agent

Host project for composing `eos-agent-sdk` into the coding-agent product.

The root package is the application. Keep all implementation code under `src/`;
there is no internal `packages/` workspace.

| Location | Owner |
|---|---|
| `src/bootstrap.ts` | composition root over SDK, config, tools, and workflow providers |
| `src/config/` | `.eos-agents` config, profile, hook, LLM-client, and workflow loaders |
| `src/agents/` | concrete `buildAgentFactory` and advisory support |
| `src/tools/` | model-visible tool implementations |
| `src/workflows/core/` | `WorkflowHub`, provider contracts, and generic workflow registry code |
| `src/workflows/pursuit/` | pursuit provider, context-script wiring, domain contracts, state, DB, context projection, and service |
| `src/scripts/` | subprocess JSON command runner |
| `tests/testkit/` | `.eos-agents` fixture building |

Run package-manager commands from this directory with `pnpm`.
