//! `eos-sandbox-gateway` binary entry: top-level argv dispatch only.
#![forbid(unsafe_code)]

use anyhow::{bail, Result};

mod gateway;
mod serve;

#[cfg(test)]
#[path = "../tests/contract/mod.rs"]
mod contract;

fn main() -> Result<()> {
    let mut args = std::env::args();
    let _argv0 = args.next();
    match args.next().as_deref() {
        Some("--version" | "-V") => {
            println!("eos-sandbox-gateway {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
        Some("serve") => serve::run(args),
        Some(other) => bail!("unknown subcommand {other:?}; expected serve | --version"),
        None => bail!("missing subcommand; expected serve | --version"),
    }
}
