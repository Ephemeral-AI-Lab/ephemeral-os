//! `sandbox-daemon` and `eosd` binary entry: subcommand dispatch only.
//!
//! # Invariant this binary owns (`proj-lib-main-split`)
//!
//! `main.rs` holds NO domain logic. It parses argv, routes to one of three
//! subcommand adapters, and maps their typed errors to process exit codes:
//!
//! - `sandbox-daemon serve` -> the async RPC server in `sandbox_daemon`.
//! - `sandbox-daemon ns-runner` / `eosd ns-runner` -> the single-threaded
//!   namespace runner in `namespace_process::runner`.
//! - `sandbox-daemon ns-holder` / `eosd ns-holder` -> the single-threaded
//!   namespace holder in `namespace_process::holder`.
//!
//! Three real processes, one static binary. This is the launcher chain:
//! `serve` owns the RPC server, `ns-runner` owns setns command execution,
//! and `ns-holder` owns the persistent isolated namespace holder lifecycle.
//!
//! `anyhow` is allowed here (binary crate); library crates keep `thiserror`. A
//! tiny hand-rolled arg match is used instead of `clap` because the surface is
//! fixed subcommands plus `--version`.
//!
//! # Exit-code contract (preserved through this dispatcher)
//!
//! The library errors carry exit codes that MUST survive to the process exit
//! status; a blanket `anyhow` fallthrough would collapse them all to `1` and
//! silently drop the contract. The dispatcher therefore maps known codes via
//! [`std::process::exit`]:
//! - ns-holder: `1` (control pipe closed), `2` (unexpected token), `7` (test
//!   crash knob) —
//!   `namespace_process::holder::NsHolderError::{CONTROL_CLOSED_EXIT,
//!   UNEXPECTED_TOKEN_EXIT, TEST_CRASH_EXIT}`.
#![forbid(unsafe_code)]

mod holder;
mod runner;
mod serve;

use std::path::Path;

use anyhow::{anyhow, Result};

fn main() -> Result<()> {
    let mut args = std::env::args();
    let argv0 = args.next();
    let invocation = Invocation::from_argv0(argv0.as_deref());

    match args.next().as_deref() {
        Some("--version" | "-V") => {
            println!("{} {}", invocation.binary_name(), env!("CARGO_PKG_VERSION"));
            Ok(())
        }
        Some("serve") if invocation == Invocation::SandboxDaemon => serve::run(args),
        Some("ns-runner") => runner::run(args),
        Some("ns-holder") => holder::run(args),
        Some(other) => Err(anyhow!(
            "unknown subcommand {other:?}; expected {}",
            invocation.expected_subcommands()
        )),
        None => Err(anyhow!(
            "missing subcommand; expected {}",
            invocation.expected_subcommands()
        )),
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Invocation {
    SandboxDaemon,
    Eosd,
}

impl Invocation {
    fn from_argv0(argv0: Option<&str>) -> Self {
        let Some(argv0) = argv0 else {
            return Self::SandboxDaemon;
        };
        match Path::new(argv0).file_stem().and_then(|name| name.to_str()) {
            Some("eosd") => Self::Eosd,
            _ => Self::SandboxDaemon,
        }
    }

    const fn binary_name(self) -> &'static str {
        match self {
            Self::SandboxDaemon => "sandbox-daemon",
            Self::Eosd => "eosd",
        }
    }

    const fn expected_subcommands(self) -> &'static str {
        match self {
            Self::SandboxDaemon => "serve | ns-runner | ns-holder | --version",
            Self::Eosd => "ns-runner | ns-holder | --version",
        }
    }
}
