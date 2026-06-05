# Sandbox Config

`prd.yml` is the single baseline config for the sandbox runtime and sandbox test
harness defaults.

Production code always loads:

```text
sandbox/config/prd.yml
```

Tests may load one local override in addition to `prd.yml`:

```text
sandbox/config/prd.yml
  -> crates/<crate>/tests/**/<name>.test.yml
```

Users do not choose config files through CLI flags or environment variables.
Users only choose which tests to run; test code chooses its local override file.

For Rust E2E tests, each integration-test crate points at one local
`config/default.test.yml`. The harness loads `prd.yml` plus that override,
derives Docker settings from the merged document, and uploads the same merged
YAML to the daemon's `prd.yml` location inside the container before starting
`eosd`.

Legacy Rust E2E selection through `e2e.toml`, `EOS_E2E_CONFIG`,
`EOS_E2E_PROFILE`, or `EOS_E2E_*` field overrides has been removed. Use normal
`cargo test` name filters to choose the suite or focused test.

## Merge Rules

- Objects merge recursively.
- Scalars replace the baseline value.
- Arrays replace the baseline array.
- Missing keys inherit from `prd.yml`.
- Unknown keys are errors.
- Wrong types are errors.
- `null` is allowed only for optional typed fields.

## Static Values

Do not move protocol op names, schema versions, file layout names, kernel
constants, netlink constants, nft constants, wire field names, namespace
handshake tokens, or plugin manifest contract defaults into YAML.

Runtime and test harness policy belongs in YAML. Static contracts belong in Rust
code near their owner.

## Schema Ownership

Runtime schema lives in `crates/eos-config/src/configs/<module-name>.rs`.
Multi-word crate modules use their crate-style filename, for example
`isolated-workspace.rs` exports the `isolated_workspace` Rust module. Runtime
crates may re-export those typed schemas through compatibility modules, but they
do not own duplicate `src/config.rs` schema files.

Local test `config/` folders are YAML-only override folders. They do not define
Rust schema.
