# backend-server resulting file/folder structure

```text
EphemeralOS/
|-- agent-core/
|   |-- Cargo.toml
|   `-- crates/
|       |-- eos-sandbox-port/
|       |   |-- Cargo.toml
|       |   `-- src/
|       |       |-- gateway.rs
|       |       |-- lib.rs
|       |       |-- provision.rs
|       |       |-- tool_api.rs
|       |       `-- transport.rs
|       |-- eos-runtime/
|       |   |-- Cargo.toml
|       |   `-- src/
|       |       |-- abort_cleanup.rs
|       |       |-- entry.rs
|       |       |-- lib.rs
|       |       |-- request_input.rs
|       |       |-- root_agent.rs
|       |       `-- runtime_services/
|       |           |-- builder.rs
|       |           |-- mod.rs
|       |           `-- sandbox.rs
|       |-- eos-state/
|       |   `-- src/
|       |       `-- store.rs
|       `-- eos-db/
|           `-- src/
|               `-- repositories/
|
|-- sandbox/
|   |-- Cargo.toml
|   `-- crates/
|       |-- eos-daemon/
|       |-- eos-protocol/
|       `-- eosd/
|
`-- backend-server/
    |-- Cargo.toml
    |-- README.md
    |-- SPEC.md
    |-- config/
    |   |-- backend.yml
    |   `-- local.yml
    `-- crates/
        |-- eos-sandbox-host/
        |   |-- Cargo.toml
        |   `-- src/
        |       |-- bootstrap_artifact.rs
        |       |-- config.rs
        |       |-- docker.rs
        |       |-- lib.rs
        |       |-- lifecycle.rs
        |       |-- provider.rs
        |       |-- provisioning.rs
        |       `-- registry.rs
        |
        |-- eos-obs-collector/
        |   |-- Cargo.toml
        |   `-- src/
        |       |-- gates.rs
        |       |-- lib.rs
        |       |-- normalization.rs
        |       `-- types.rs
        |
        |-- eos-backend-types/
        |   |-- Cargo.toml
        |   `-- src/
        |       |-- audit.rs
        |       |-- error.rs
        |       |-- events.rs
        |       |-- lib.rs
        |       |-- pagination.rs
        |       |-- requests.rs
        |       |-- sandboxes.rs
        |       `-- stats.rs
        |
        |-- eos-backend-config/
        |   |-- Cargo.toml
        |   |-- src/
        |   |   |-- lib.rs
        |   |   |-- loader.rs
        |   |   |-- obs.rs
        |   |   |-- sandbox.rs
        |   |   `-- server.rs
        |   `-- tests/
        |       `-- load_config.rs
        |
        |-- eos-backend-store/
        |   |-- Cargo.toml
        |   |-- migrations/
        |   |   `-- 0001_initial.sql
        |   |-- src/
        |   |   |-- audit_cursor.rs
        |   |   |-- db.rs
        |   |   |-- event_log.rs
        |   |   |-- lib.rs
        |   |   |-- obs.rs
        |   |   `-- run_meta.rs
        |   `-- tests/
        |       `-- store.rs
        |
        |-- eos-backend-runtime/
        |   |-- Cargo.toml
        |   |-- src/
        |   |   |-- event_bus.rs
        |   |   |-- host.rs
        |   |   |-- launcher.rs
        |   |   |-- lib.rs
        |   |   |-- reaper.rs
        |   |   |-- registry.rs
        |   |   |-- sandbox_manager.rs
        |   |   `-- state_reader.rs
        |   `-- tests/
        |       |-- launcher.rs
        |       `-- sandbox_manager.rs
        |
        |-- eos-backend-obs/
        |   |-- Cargo.toml
        |   |-- src/
        |   |   |-- ingestor.rs
        |   |   |-- lib.rs
        |   |   |-- sink.rs
        |   |   `-- stats.rs
        |   `-- tests/
        |       `-- obs.rs
        |
        |-- eos-backend-api/
        |   |-- Cargo.toml
        |   |-- src/
        |   |   |-- error.rs
        |   |   |-- handlers/
        |   |   |   |-- mod.rs
        |   |   |   |-- sandboxes.rs
        |   |   |   |-- stats.rs
        |   |   |   |-- stream.rs
        |   |   |   |-- tasks.rs
        |   |   |   `-- user_requests.rs
        |   |   |-- lib.rs
        |   |   |-- openapi.rs
        |   |   |-- router.rs
        |   |   `-- stream/
        |   |       |-- mod.rs
        |   |       |-- sse.rs
        |   |       `-- ws.rs
        |   `-- tests/
        |       |-- api_contract.rs
        |       `-- stream.rs
        |
        `-- eos-backend-main/
            |-- Cargo.toml
            |-- src/
            |   |-- app.rs
            |   `-- main.rs
            `-- tests/
                `-- live_e2e.rs
```
