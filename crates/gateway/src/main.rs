//! `gateway` binary entry: top-level argv dispatch only.
#![forbid(unsafe_code)]

use anyhow::{bail, Result};

mod client;
mod engine;
mod router;
mod serve;
mod transport;
mod wire;

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
        Some(other) => bail!("unknown subcommand {other:?}; expected host | daemon | --version"),
        None => {
            client::print_usage();
            Ok(())
        }
    }
}
