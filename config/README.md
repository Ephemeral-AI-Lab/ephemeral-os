# Sandbox Config

`prd.yml` is the single baseline config for the sandbox runtime and sandbox test
harness defaults. The gateway owns production config selection and upload:
operators choose the local source YAML with
`sandbox-gateway host serve --config-yaml`, and the gateway copies that document
into each sandbox at `--remote-config` before starting `eosd`.

The host gateway also reads `gateway.default_image_profile` from this document
before upload. That profile is the approved default for `host.sandbox.acquire`;
operator-only commands such as `sandbox-gateway host containers start IMAGE` can
still choose an explicit image at command time.

The daemon loads the uploaded runtime config from:

```text
<remote-config path, defaulting to eos-sandbox/config/prd.yml in local dev>
```

Tests may load one local override in addition to `prd.yml`:

```text
eos-sandbox/config/prd.yml
  -> crates/<crate>/tests/**/<name>.test.yml
```

Outside the gateway host process, users do not choose daemon config files
through daemon CLI flags or environment variables. Users only choose which tests
to run; test code chooses its local override file.

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

Runtime schema lives in `crates/config/src/configs/<module-name>.rs`.
Multi-word crate modules use their crate-style filename, for example
`isolated-workspace.rs` exports the `isolated` Rust module. Runtime
crates may re-export those typed schemas through their public modules, but they
do not own duplicate `src/config.rs` schema files.

Local test `config/` folders are YAML-only override folders. They do not define
Rust schema.
