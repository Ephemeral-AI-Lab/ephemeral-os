//! `eosd` binary entry: subcommand dispatch ONLY.
//!
//! # Invariant this binary owns (`proj-lib-main-split`)
//!
//! `main.rs` holds NO domain logic. It parses argv, routes to one of three
//! library entry points, and maps their typed errors to process exit codes:
//!
//! - `eosd daemon`     -> the async RPC server in `eos-daemon`.
//! - `eosd ns-runner`  -> the single-threaded namespace runner in `eos-runner`.
//! - `eosd ns-holder`  -> the single-threaded namespace holder in `eos-ns-holder`.
//!
//! Three real processes, one static binary — this replaces the Python launcher
//! chain (`daemon/scripts/launch_daemon.sh` spawns `python -m <module>`, and the
//! isolated-workspace control plane spawns `ns_holder.py` / `setns_exec.py` as
//! separate interpreters). In Rust they collapse into `eosd <subcommand>`.
//!
//! `anyhow` is allowed here (binary crate); library crates keep `thiserror`. A
//! tiny hand-rolled arg match is used instead of `clap` — the surface is three
//! fixed subcommands plus `--version`.
//!
//! # Exit-code contract (preserved through this dispatcher)
//!
//! The library errors carry exit codes that MUST survive to the process exit
//! status; a blanket `anyhow` fallthrough would collapse them all to `1` and
//! silently drop the contract. The dispatcher therefore maps known codes via
//! [`std::process::exit`]:
//! - ns-holder: `1` (control pipe closed), `2` (unexpected token), `7` (test
//!   crash knob) — `eos_ns_holder::NsHolderError::{CONTROL_CLOSED_EXIT,
//!   UNEXPECTED_TOKEN_EXIT, TEST_CRASH_EXIT}`.
//! - thin-client / daemon connect path: `97` (`CONNECT_FAILED`), `98`
//!   (`IO_FAILED`) — `eos_protocol::{CONNECT_FAILED, IO_FAILED}`.
//!
//! PORT backend/src/sandbox/daemon/scripts/launch_daemon.sh + backend/src/sandbox/host/daemon_client.py — the launcher + thin-client this binary replaces.
#![forbid(unsafe_code)]

use std::os::fd::RawFd;

use anyhow::{anyhow, Context, Result};

fn main() -> Result<()> {
    let mut args = std::env::args();
    let _argv0 = args.next();

    match args.next().as_deref() {
        Some("--version") | Some("-V") => {
            println!("eosd {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
        Some("daemon") => run_daemon(args),
        Some("ns-runner") => run_ns_runner(args),
        Some("ns-holder") => run_ns_holder(args),
        Some(other) => Err(anyhow!(
            "unknown subcommand {other:?}; expected daemon | ns-runner | ns-holder | --version"
        )),
        None => Err(anyhow!(
            "missing subcommand; expected daemon | ns-runner | ns-holder | --version"
        )),
    }
}

/// `eosd daemon [--socket <path>] [--pid-file <path>]` — start the async RPC
/// server that owns the runtime.
///
/// Thin call into `eos-daemon`. The real entry will parse the `--socket` /
/// `--pid-file` flags into a `eos_daemon::ServerConfig`, build the daemon via
/// `DaemonServer::new`, and drive its `serve` loop, reproducing the launcher's
/// `python -m <module> --socket <sock> --pid-file <pid>` spawn; connect/IO
/// failures on the client recovery path map to
/// `eos_protocol::{CONNECT_FAILED, IO_FAILED}` exit codes. The daemon's only
/// entry, `DaemonServer::serve`, is `async`, but `eosd` has no direct `tokio`
/// dependency (it inherits tokio only transitively — contract row 11), so this
/// arm stays a `todo!()` until either `eos-daemon` exposes a synchronous entry
/// wrapper that owns the runtime, or `eosd` gains a direct runtime + flag parse.
// PORT backend/src/sandbox/daemon/scripts/launch_daemon.sh:78-80 — nohup python -m <MODULE> --socket <SOCK> --pid-file <PID>; daemon serve loop entry to be added to eos-daemon
fn run_daemon(_args: std::env::Args) -> Result<()> {
    todo!("PORT launch_daemon.sh:78-80 — call eos_daemon serve entry with --socket/--pid-file once it exists")
}

/// `eosd ns-runner` — execute one tool call inside a namespace (fresh-ns or
/// setns), reading the resolved `RunRequest` payload and emitting the
/// `RunResult` JSON, the way `namespace_entrypoint.py` / `setns_exec.py` run as
/// child interpreters today.
///
/// This is a thin call into `eos-runner`. The runner exposes `run(&RunRequest,
/// &dyn KernelMountPort)`, but a runner CLI entry (read the request payload from
/// the inherited fd/stdin, construct the overlay `KernelMountPort`, call `run`,
/// write the result) does not exist as a library function yet, and the mount
/// port impl lives in a sibling crate being written concurrently — so this arm
/// stays a `todo!()` rather than reconstructing that logic in `main`.
// PORT backend/src/sandbox/overlay/namespace_entrypoint.py:1 + backend/src/sandbox/isolated_workspace/scripts/setns_exec.py:1 — child-interpreter entry; call eos_runner::run once a runner CLI entry exists
fn run_ns_runner(_args: std::env::Args) -> Result<()> {
    todo!("PORT namespace_entrypoint.py + setns_exec.py — call eos_runner::run via a runner CLI entry once it exists")
}

/// `eosd ns-holder <readiness_fd> <control_fd>` — become the single-threaded
/// child that creates and pins the isolated workspace's namespace stack and
/// runs the readiness handshake, then `pause()`s until `SIGTERM`.
///
/// Real thin call: `eos-ns-holder` already exposes `run(readiness_fd,
/// control_fd)`, and its lib doc sanctions keeping the argv -> FD parsing here.
/// We parse the two positional FD ints and dispatch; the holder's typed errors
/// carry exit codes (`1` / `2` / `7`) that we map onto the process status so the
/// daemon-side crash-recovery sees the same codes as the Python holder.
// PORT backend/src/sandbox/isolated_workspace/scripts/ns_holder.py:89-91 — readiness_fd = int(argv[1]); control_fd = int(argv[2])
fn run_ns_holder(mut args: std::env::Args) -> Result<()> {
    let readiness_fd = parse_fd(args.next(), "readiness_fd")?;
    let control_fd = parse_fd(args.next(), "control_fd")?;

    match eos_ns_holder::run(readiness_fd, control_fd) {
        Ok(()) => Ok(()),
        Err(err) => {
            let code = match &err {
                eos_ns_holder::NsHolderError::ControlPipeClosed => {
                    eos_ns_holder::NsHolderError::CONTROL_CLOSED_EXIT
                }
                eos_ns_holder::NsHolderError::UnexpectedToken => {
                    eos_ns_holder::NsHolderError::UNEXPECTED_TOKEN_EXIT
                }
                // Unshare / pipe-i/o failures have no dedicated Python exit code;
                // surface the message and fall through to the generic status.
                _ => return Err(anyhow::Error::new(err).context("ns-holder failed")),
            };
            // The holder reached a defined non-zero terminal state; reproduce the
            // exact Python exit code (1 / 2) instead of anyhow's generic 1.
            std::process::exit(code);
        }
    }
}

/// Parse a positional file-descriptor argument shared by the ns-holder arm.
// PORT backend/src/sandbox/isolated_workspace/scripts/ns_holder.py:90-91 — int(argv[n])
fn parse_fd(value: Option<String>, name: &str) -> Result<RawFd> {
    value
        .ok_or_else(|| anyhow!("missing {name} argument for ns-holder"))?
        .parse::<RawFd>()
        .with_context(|| format!("{name} must be an integer file descriptor"))
}
