//! `gateway` binary entry: top-level argv dispatch only.
#![forbid(unsafe_code)]

use anyhow::{bail, Result};

mod catalog;
mod client;
mod engine;
mod router;
mod serve;
mod transport;
mod wire;

#[cfg(test)]
pub(crate) use catalog::{Catalog, Route, Visibility};
#[cfg(test)]
pub(crate) use engine::Engine;
#[cfg(test)]
pub(crate) use router::{handle, Surface};
#[cfg(test)]
pub(crate) use transport::{handle_connection, operator_socket_path, serve_with_catalog};
#[cfg(test)]
pub(crate) use wire::{parse_request, ClientRequest};

#[cfg(test)]
#[path = "../tests/contract/mod.rs"]
mod contract;

fn main() -> Result<()> {
    let mut args = std::env::args();
    let _argv0 = args.next();
    match args.next().as_deref() {
        Some("--help" | "-h") => {
            client::print_usage();
            Ok(())
        }
        Some("--version" | "-V") => {
            println!("sandbox-gateway {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
        Some("host") => {
            let mut args = args.collect::<Vec<_>>();
            if args.first().is_some_and(|arg| arg == "serve") {
                args.remove(0);
                serve::run(args.into_iter())
            } else {
                client::run_host(args.into_iter())
            }
        }
        Some("daemon") => client::run_daemon(args),
        Some("serve") => serve::run(args),
        Some(
            command
            @ ("op" | "images" | "containers" | "sandboxes" | "image-profiles" | "profiles"),
        ) => client::run_legacy(command, args),
        Some(other) => bail!("unknown subcommand {other:?}; expected host | daemon | --version"),
        None => {
            client::print_usage();
            Ok(())
        }
    }
}
