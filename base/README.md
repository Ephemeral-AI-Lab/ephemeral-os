# EphemeralOS base

`base/` holds tiny shared contracts that can be consumed by both `agent-core/`
and `sandbox/` without pulling either workspace into the other.

Current crates:

- `eos-obs-contract`: normalized audit/observability row types for collectors and
  reports.

Keep this workspace contract-only. Runtime sinks, daemon rings, tracing setup,
plugin wrappers, and report builders stay in their owning workspaces.
