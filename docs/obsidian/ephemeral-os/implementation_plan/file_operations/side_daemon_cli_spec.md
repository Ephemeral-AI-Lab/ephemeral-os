---
title: Side Daemon CLI Spec
tags:
  - ephemeral-os
  - sandbox
  - daemon
  - cli
  - file-operations
status: draft
updated: 2026-07-02
---

# Side Daemon CLI Spec

This is a naming cleanup proposal for the private `sandbox-daemon` child-process
CLI used by namespace setup and namespace execution.

## Target Shape

Keep `serve` unchanged:

```text
sandbox-daemon serve ...
```

Rename the namespace child subcommands and make all arguments named:

```text
sandbox-daemon namespace-runner --mode shell --request-fd FD --result-fd FD
sandbox-daemon namespace-runner --mode mount-overlay --request-fd FD --result-fd FD
sandbox-daemon namespace-runner --mode file-op --request-fd FD --result-fd FD

sandbox-daemon namespace-holder --readiness-fd FD --control-fd FD --network shared
sandbox-daemon namespace-holder --readiness-fd FD --control-fd FD --network isolated
```

## Why

- `namespace-runner` and `namespace-holder` are clearer than `ns-runner` and
  `ns-holder`.
- `--mode shell|mount-overlay|file-op` makes the runner alternatives explicit.
- `--readiness-fd`, `--control-fd`, and `--network` remove positional mystery
  from the holder command.
- `--request-fd` and `--result-fd` stay required. They are protocol channels,
  not duplicate stdio.

## Parser Contract

```text
sandbox-daemon namespace-runner --mode MODE --request-fd FD --result-fd FD
```

Rules:

- `MODE` must be exactly one of `shell`, `mount-overlay`, or `file-op`.
- `--request-fd` and `--result-fd` are required integer file descriptors.
- Unknown flags and positional args are errors.
- No implicit shell default.

```text
sandbox-daemon namespace-holder --readiness-fd FD --control-fd FD --network MODE
```

Rules:

- `MODE` must be exactly one of `shared` or `isolated`.
- `--readiness-fd` and `--control-fd` are required integer file descriptors.
- Unknown flags and positional args are errors.

## Rust Naming

Use readable names at the parser boundary:

```rust
enum NamespaceRunnerMode {
    Shell,
    MountOverlay,
    FileOp,
}

struct NamespaceRunnerCliConfig {
    mode: NamespaceRunnerMode,
    request_fd: RawFd,
    result_fd: RawFd,
}

struct NamespaceHolderCliConfig {
    readiness_fd: RawFd,
    control_fd: RawFd,
    network: NamespaceNetwork,
}
```

Do not add a `RunnerWait`, `OneShot`, or compatibility enum.

## Migration

Current:

```text
sandbox-daemon ns-runner [--mount-overlay] --request-fd FD --result-fd FD
sandbox-daemon ns-holder readiness_fd control_fd shared|isolated
```

Target:

```text
sandbox-daemon namespace-runner --mode shell|mount-overlay|file-op --request-fd FD --result-fd FD
sandbox-daemon namespace-holder --readiness-fd FD --control-fd FD --network shared|isolated
```

Because `sandbox-daemon` is private internal plumbing, do not add legacy aliases
unless an actual external caller requires them. Update the launch sites and unit
tests in the same change.

## Non-Goals

- Do not change `sandbox-daemon serve`.
- Do not rename source files only for naming symmetry.
- Do not add nested commands such as `sandbox-daemon namespace runner`; they add
  parser surface without improving the private call path.
