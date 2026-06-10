//! `eos-api` binary entry: argv parsing and subcommand dispatch ONLY.
//!
//! - `eos-api serve --listen <socket> --image <img> [engine flags]` — serve
//!   the client socket plus the operator socket beside it (`<socket>.admin`).
//! - `eos-api admin <op> …` — operator CLI against the operator socket.
#![forbid(unsafe_code)]

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use anyhow::{bail, Context, Result};

use eos_api::{admin, public, server};
use eos_sandbox_host::{HostConfig, SandboxHost};

fn main() -> Result<()> {
    let mut args = std::env::args();
    let _argv0 = args.next();
    match args.next().as_deref() {
        Some("--version" | "-V") => {
            println!("eos-api {}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
        Some("serve") => serve(args),
        Some("admin") => admin::run(&admin::AdminArgs::parse(args)?),
        Some(other) => bail!("unknown subcommand {other:?}; expected serve | admin | --version"),
        None => bail!("missing subcommand; expected serve | admin | --version"),
    }
}

fn serve(argv: std::env::Args) -> Result<()> {
    let config = ServeArgs::parse(argv)?;
    let catalog = Arc::new(public::Catalog::load_builtin()?);
    let host = SandboxHost::open(config.host)?;
    server::serve(&config.listen, catalog, Arc::new(host))
}

struct ServeArgs {
    listen: PathBuf,
    host: HostConfig,
}

impl ServeArgs {
    fn parse(mut argv: std::env::Args) -> Result<Self> {
        // Compile-time workspace root: eos-api and eosd are built from the
        // same workspace, so the daemon's compiled-in config path and the
        // packaged binary location are derivable defaults.
        let workspace = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(2)
            .map(std::path::Path::to_path_buf)
            .context("derive workspace root")?;
        let mut listen = PathBuf::from("/tmp/eos-api.sock");
        let mut image = None;
        let mut platform = None;
        let mut eosd_path = workspace.join("dist").join("eosd-linux-amd64");
        let mut config_yaml_path = workspace.join("config").join("prd.yml");
        let mut remote_config_path = workspace.join("config").join("prd.yml");
        let mut remote_daemon_dir = PathBuf::from("/eos/runtime/daemon");
        let mut state_dir: Option<PathBuf> = None;
        let mut tcp_port = 37_657_u16;
        let mut ready_timeout_s = 60_u64;
        let mut request_timeout_s = 30_u64;
        let mut created_by = "eos-api".to_owned();
        while let Some(flag) = argv.next() {
            let mut value = |flag: &str| -> Result<String> {
                argv.next()
                    .with_context(|| format!("{flag} requires a value"))
            };
            match flag.as_str() {
                "--listen" => listen = value("--listen")?.into(),
                "--image" => image = Some(value("--image")?),
                "--platform" => platform = Some(value("--platform")?),
                "--eosd" => eosd_path = value("--eosd")?.into(),
                "--config-yaml" => config_yaml_path = value("--config-yaml")?.into(),
                "--remote-config" => remote_config_path = value("--remote-config")?.into(),
                "--remote-daemon-dir" => remote_daemon_dir = value("--remote-daemon-dir")?.into(),
                "--state-dir" => state_dir = Some(value("--state-dir")?.into()),
                "--tcp-port" => tcp_port = value("--tcp-port")?.parse().context("--tcp-port")?,
                "--ready-timeout-s" => {
                    ready_timeout_s = value("--ready-timeout-s")?
                        .parse()
                        .context("--ready-timeout-s")?;
                }
                "--request-timeout-s" => {
                    request_timeout_s = value("--request-timeout-s")?
                        .parse()
                        .context("--request-timeout-s")?;
                }
                "--created-by" => created_by = value("--created-by")?,
                other => bail!("unknown serve flag {other:?}"),
            }
        }
        let image = image.context("serve requires --image <docker image>")?;
        let state_dir = state_dir.unwrap_or_else(|| {
            listen
                .parent()
                .unwrap_or_else(|| std::path::Path::new("/tmp"))
                .join("eos-api-state")
        });
        let remote_eosd_path = remote_daemon_dir.join("eosd");
        Ok(Self {
            listen,
            host: HostConfig {
                image,
                platform,
                eosd_path,
                config_yaml_path,
                remote_daemon_dir,
                remote_eosd_path,
                remote_config_path,
                tcp_port,
                ready_timeout: Duration::from_secs(ready_timeout_s),
                request_timeout: Duration::from_secs(request_timeout_s),
                created_by,
                state_dir,
            },
        })
    }
}
