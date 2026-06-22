# Phase 6: Log Export And Trace Correlation

## Goal

Add an explicit observability log signal and Grafana trace-to-logs correlation
after traces, metrics, dashboards, and runner context propagation are stable.
This is the first phase that introduces Loki. Loki is not required to view
spans or trace events from Phase 3.

## Scope

- Add log export only for allowlisted observability records that are useful
  outside the trace timeline.
- Reuse the existing single OTLP sink endpoint. Do not add a second production
  endpoint or a fallback sink list.
- Add an OpenTelemetry Collector logs pipeline that forwards logs to Loki.
- Add Grafana provisioning for Tempo-to-Loki trace-to-logs and Loki-to-Tempo
  derived-field links.
- Include trace correlation fields on exported log records: `trace_id`,
  `span_id`, `service.name`, and `sandbox.id`.
- Keep `transcript.log` as functional command output. Do not ingest command
  transcripts as observability logs in this phase.
- Do not duplicate every trace event into Loki. Select only bounded,
  low-cardinality operational records that benefit from log-style search.

## File And Folder Structure Changes

```text
Cargo.toml
  [workspace.dependencies]
    # Select exact compatible OpenTelemetry log crates/features in the
    # implementation Cargo change. Do not mix OTel crate generations.

crates/sandbox-daemon/
  Cargo.toml
  src/
    telemetry.rs
    telemetry/
      logs.rs                 # daemon-owned log exporter/layer setup, if split
  tests/
    unit/
      telemetry_logs.rs

crates/sandbox-config/src/configs/
  daemon.rs

crates/sandbox-config/tests/unit/configs/
  daemon.rs

observability/
  docker-compose.yml          # extend with loki
  otel-collector.yaml         # extend with logs pipeline
  loki.yaml                   # add log backend config
  tempo.yaml                  # update only if trace-to-logs needs it
  grafana/
    provisioning/
      datasources/
        tempo.yaml            # update with tracesToLogsV2
        loki.yaml             # add derived fields
```

Runtime crates still emit inline `tracing` spans/events only. They do not own
log exporters, Loki clients, or collector configuration.

Phase 6 extends the shared `observability/` tree created in Phase 3 and updated
in Phase 4a. It must not introduce phase-specific observability directories.

## Struct/Class And Field Changes

Reuse the existing telemetry sink. Log export is an additional signal on the
same configured OTLP path, not a separate sink.

```rust
pub struct TelemetryConfig {
    pub enabled: bool,
    pub service_name: String,
    pub level: String,
    pub sink: Option<TelemetrySink>,
    pub export_logs: bool,
}
```

Validation rules:

- `export_logs = true` requires `sink = Otlp { ... }`.
- `export_logs = true` is rejected for `LocalJson` sinks.
- `export_logs = true` does not allow a second endpoint, file sink, stdout
  sink, stderr sink, or fallback sink list.
- The collector must receive logs through OTLP and forward to Loki.

## Log Rules

- Log attributes must be allowlisted. Allowed attributes include bounded values
  such as operation name, status, bounded reason, bounded error kind,
  `sandbox.id`, `service.name`, `trace_id`, and `span_id`.
- Do not export raw command text, stdin, stdout/stderr, command output,
  environment values, auth tokens, raw request args, raw host paths, raw
  workspace roots, raw cgroup paths, raw layer paths, raw upper/work dirs,
  transcript/artifact paths, raw PIDs, raw root hashes, raw DTO `Debug`, raw
  response payloads, or raw `Display` error strings.
- Do not use high-cardinality labels in Loki. Keep high-cardinality correlation
  fields such as `trace_id` as structured metadata or derived fields according
  to the chosen Loki/Grafana configuration, not broad stream labels.
- Keep trace spans/events in Tempo. Loki is for exported log records and
  correlation, not the primary trace event store.
- Grafana trace-to-logs requires both sides: Tempo data source query settings
  and Loki data source derived fields.

## LOC Estimate

| Area | Net LOC |
| --- | ---: |
| OTel log dependencies and feature selection | 8 to 24 |
| Config field, validation, and tests | 70 to 140 |
| Daemon log exporter/layer wiring | 160 to 300 |
| Safe-field log allowlist and sentinel tests | 140 to 260 |
| Collector/Loki/Grafana provisioning | 190 to 340 |
| Trace-to-logs integration tests or smoke scripts | 60 to 140 |
| Docs/config examples | 12 to 56 |
| Total | 640 to 1,160 |

## Acceptance Criteria

- [ ] Loki is introduced only in this phase.
- [ ] Log export reuses the existing single OTLP sink endpoint and does not add
      fallback sinks.
- [ ] `export_logs = true` is rejected unless the active sink is OTLP.
- [ ] Tempo stores traces and trace events; Loki stores only explicit exported
      log records.
- [ ] Grafana Tempo data source has trace-to-logs configured.
- [ ] Grafana Loki data source has derived fields configured so log lines with
      trace IDs link back to Tempo.
- [ ] Exported logs include `trace_id`, `span_id`, `service.name`, and
      `sandbox.id` when a trace context exists.
- [ ] Exported logs exclude raw command text, stdin, stdout/stderr, command
      output, environment values, auth tokens, raw request args, raw host paths,
      raw workspace roots, raw cgroup paths, raw layer paths, raw upper/work
      dirs, transcript/artifact paths, raw PIDs, raw root hashes, raw DTO
      `Debug`, raw response payloads, and raw `Display` error strings.
- [ ] Loki labels are allowlisted and low-cardinality.
- [ ] `transcript.log` behavior is unchanged and command transcripts are not
      ingested into Loki.
- [ ] Phase 3 trace validation and Phase 4a dashboards still pass without Loki
      when log export is disabled.
- [ ] `cargo test -p sandbox-daemon -p sandbox-config` passes.
