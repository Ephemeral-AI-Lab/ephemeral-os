# Recovered Agent Runtime E2E Suite

This directory preserves the deleted agent-runtime E2E suite from:

- source commit: `13ad19839`
- source path: `eos-coding-agent/e2e/`
- original path before the split: `eos-agent-core/packages/agent-runtime/e2e/`

The suite is intentionally quarantined from the active SDK typecheck, lint, and
`pnpm run test:e2e` runner. These files still target the old host runtime
surface, including `createAgentRuntime`, profile loading, transcript/result
JSONL helpers, subagent tools, advisor hooks, and notification trigger config.
Those concepts moved out of `eos-agent-sdk` during the SDK/coding-agent split.

To reactivate these tests, port them to one of the current ownership targets:

- SDK-level cases should use `createAgentSdk`, SDK-relative imports, and the
  current `recordsDir` artifacts.
- Host-policy cases should move to `eos-coding-agent` and import the host's
  workflow/profile/tool modules there.

