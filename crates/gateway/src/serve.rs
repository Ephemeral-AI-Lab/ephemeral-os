use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use anyhow::{bail, Context, Result};
use config::configs::gateway::GatewayConfig;

use host::{HostConfig, SandboxHost};

use crate::transport;

pub(crate) fn run(argv: impl Iterator<Item = String>) -> Result<()> {
    let config = ServeArgs::parse(argv)?;
    let host = SandboxHost::open(config.host)?;
    transport::serve(&config.listen, Arc::new(host))
}

pub(crate) struct ServeArgs {
    pub(crate) listen: PathBuf,
    pub(crate) host: HostConfig,
}

impl ServeArgs {
    pub(crate) fn parse(mut argv: impl Iterator<Item = String>) -> Result<Self> {
        // The gateway and eosd are built from the same sandbox workspace, so
        // daemon config and packaged binary defaults are derivable here.
        let workspace = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .nth(2)
            .map(std::path::Path::to_path_buf)
            .context("derive workspace root")?;
        let default_runtime_dir = default_runtime_dir();
        let mut listen = default_listen_path();
        let mut image = None;
        let mut platform = None;
        let mut docker_privileged = true;
        let mut eosd_path = workspace.join("dist").join("eosd-linux-amd64");
        let mut config_yaml_path = workspace.join("config").join("prd.yml");
        let mut remote_config_path = None;
        let mut remote_daemon_dir = PathBuf::from("/eos/runtime/daemon");
        let mut state_dir: Option<PathBuf> = None;
        let mut tcp_port = 37_657_u16;
        let mut ready_timeout_s = 60_u64;
        let mut request_timeout_s = 30_u64;
        let mut created_by = "ephai-sandbox-gateway".to_owned();
        while let Some(flag) = argv.next() {
            let mut value = || -> Result<String> {
                argv.next()
                    .with_context(|| format!("{flag} requires a value"))
            };
            match flag.as_str() {
                "--listen" => listen = value()?.into(),
                "--image" => image = Some(value()?),
                "--platform" => platform = Some(value()?),
                "--docker-privileged" => docker_privileged = true,
                "--no-docker-privileged" => docker_privileged = false,
                "--eosd" => eosd_path = value()?.into(),
                "--config-yaml" => config_yaml_path = value()?.into(),
                "--remote-config" => remote_config_path = Some(value()?.into()),
                "--remote-daemon-dir" => remote_daemon_dir = value()?.into(),
                "--state-dir" => state_dir = Some(value()?.into()),
                "--tcp-port" => tcp_port = value()?.parse().context("--tcp-port")?,
                "--ready-timeout-s" => {
                    ready_timeout_s = value()?.parse().context("--ready-timeout-s")?;
                }
                "--request-timeout-s" => {
                    request_timeout_s = value()?.parse().context("--request-timeout-s")?;
                }
                "--created-by" => created_by = value()?,
                other => bail!("unknown serve flag {other:?}"),
            }
        }
        let default_profile = if image.is_none() {
            Some(
                load_default_image_profile(&config_yaml_path).with_context(|| {
                    format!(
                        "load gateway default image profile from {}",
                        config_yaml_path.display()
                    )
                })?,
            )
        } else if platform.is_none() {
            load_default_image_profile(&config_yaml_path).ok()
        } else {
            None
        };
        let image = image
            .or_else(|| default_profile.as_ref().map(|profile| profile.image.clone()))
            .context(
                "serve requires gateway.default_image_profile.image in config or --image <docker image>",
            )?;
        let platform = platform.or_else(|| {
            default_profile
                .as_ref()
                .and_then(|profile| profile.platform.clone())
        });
        let state_dir = state_dir.unwrap_or_else(|| default_runtime_dir.join("state"));
        let remote_eosd_path = remote_daemon_dir.join("eosd");
        let remote_config_path =
            remote_config_path.unwrap_or_else(|| remote_daemon_dir.join("config.yml"));
        Ok(Self {
            listen,
            host: HostConfig {
                image,
                platform,
                docker_privileged,
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

#[derive(Debug, Clone)]
struct DefaultImageProfile {
    image: String,
    platform: Option<String>,
}

fn load_default_image_profile(path: &std::path::Path) -> Result<DefaultImageProfile> {
    let doc = config::load_path(path)?;
    let config = GatewayConfig::from_document(&doc)?;
    Ok(DefaultImageProfile {
        image: config.default_image_profile.image,
        platform: config.default_image_profile.platform,
    })
}

fn default_runtime_dir() -> PathBuf {
    if let Some(runtime_dir) = std::env::var_os("XDG_RUNTIME_DIR") {
        return PathBuf::from(runtime_dir).join("eos-sandbox");
    }
    let suffix = std::env::var("UID")
        .or_else(|_| std::env::var("USER"))
        .unwrap_or_else(|_| std::process::id().to_string());
    std::env::temp_dir().join(format!("ephai-sandbox-gateway-{suffix}"))
}

pub(crate) fn default_listen_path() -> PathBuf {
    if let Some(socket) = std::env::var_os("EOS_GATEWAY_SOCKET") {
        return PathBuf::from(socket);
    }
    default_runtime_dir().join("gateway.sock")
}
