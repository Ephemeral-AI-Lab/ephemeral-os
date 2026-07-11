# Sandbox observability

This directory is a grouping-only namespace, not a Cargo package. It deliberately has no manifest or Rust source root.

| Directory | Cargo package | Rust crate | Responsibility |
| --- | --- | --- | --- |
| `telemetry/` | `sandbox-observability-telemetry` | `sandbox_observability_telemetry` | Records, sampling, collection, and reading primitives. |
| `query/` | `sandbox-observability-query` | `sandbox_observability_query` | Structured query selection and response construction through an input port. |

`query` depends on `telemetry`; the daemon composes the query package and implements its input port.
