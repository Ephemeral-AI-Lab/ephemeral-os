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

Runtime schema lives in crate-root `src/config.rs` files only. Child modules
consume typed sub-configs from their parent crate, for example
`crate::config::PluginRuntimeConfig`.

Local test `config/` folders are YAML-only override folders. They do not define
Rust schema.
