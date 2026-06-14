# daemon-config

## Overview

`daemon-config` owns the live E2E contract for non-default daemon config paths. It proves that `eosd` starts and restarts from `crates/e2e-test/tests/daemon-config/config/non-default-remote-config.test.yml`, including the copied remote config path used by the in-container daemon process. Module config: `crates/e2e-test/tests/daemon-config/config/non-default-remote-config.test.yml`. It exercises `sandbox.runtime.ready`.

## Checklist

- [ ] daemon-config-remote-config-path: The live pool copies a non-default daemon config path into the sandbox and the daemon reads that path on initial startup and after restart.

## Test Case

| Test name | Test description | Command to run | Checklist item |
|---|---|---|---|
| `daemon-config-remote-config-path` | Runs `daemon_starts_and_restarts_from_non_default_remote_config_path`, checking that the configured remote YAML exists, readiness succeeds, and readiness still succeeds after `lease.restart_daemon()`. | `cargo run -p e2e-test --bin e2e-runner -- --suites daemon-config --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4` | `daemon-config-remote-config-path` |
