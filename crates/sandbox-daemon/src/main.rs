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
//! - `eosd daemon` -> compatibility alias for `sandbox-daemon serve`.
//!
//! Three real processes, one static binary. This is the launcher chain:
//! `daemon` owns the RPC server, `ns-runner` owns setns command execution,
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
//! - thin-client / daemon connect path: `97` (`CONNECT_FAILED`), `98`
//!   (`IO_FAILED`) — defined by the daemon serve adapter.
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
        Some("serve") if invocation == Invocation::SandboxDaemon => {
            serve::run(args, serve::ServeSubcommand::Serve)
        }
        Some("daemon") if invocation == Invocation::Eosd => {
            serve::run(args, serve::ServeSubcommand::Daemon)
        }
        Some("ns-runner") => runner::run(args),
        Some("ns-holder") => holder::run(args),
        Some(other) => Err(anyhow!(
            "unknown subcommand {other:?}; expected {} | ns-runner | ns-holder | --version",
            invocation.serve_subcommand()
        )),
        None => Err(anyhow!(
            "missing subcommand; expected {} | ns-runner | ns-holder | --version",
            invocation.serve_subcommand()
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

    const fn serve_subcommand(self) -> &'static str {
        match self {
            Self::SandboxDaemon => "serve",
            Self::Eosd => "daemon",
        }
    }
}
