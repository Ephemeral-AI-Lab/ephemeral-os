use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use anyhow::{bail, Context, Result};

use eos_sandbox_host::{HostConfig, SandboxHost};

use crate::gateway;

pub(crate) fn run(argv: std::env::Args) -> Result<()> {
    let config = ServeArgs::parse(argv)?;
    let host = SandboxHost::open(config.host)?;
    gateway::serve(&config.listen, Arc::new(host))
}

struct ServeArgs {
    listen: PathBuf,
    host: HostConfig,
}

impl ServeArgs {
    fn parse(mut argv: std::env::Args) -> Result<Self> {
        // The gateway and eosd are built from the same sandbox workspace, so
        // daemon config and packaged binary defaults are derivable here.
        let workspace = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(2)
            .map(std::path::Path::to_path_buf)
            .context("derive workspace root")?;
        let mut listen = PathBuf::from("/tmp/eos-sandbox-gateway.sock");
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
        let mut created_by = "eos-sandbox-gateway".to_owned();
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
                .join("eos-sandbox-gateway-state")
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
