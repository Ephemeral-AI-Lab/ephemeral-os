//! `sandbox-daemon ns-runner` subcommand adapter.

use std::fs::File;
use std::io::{Read, Write};
#[cfg(unix)]
use std::os::fd::RawFd;
use std::path::PathBuf;

use anyhow::{anyhow, Context, Result};

const DAEMON_CONFIG_YAML_ENV: &str = "SANDBOX_DAEMON_CONFIG_YAML";

/// Execute one command inside a holder namespace, reading the
/// resolved `NamespaceCommandRequest` payload and emitting the `RunResult` JSON.
///
/// This is a thin call into the `sandbox-runtime-namespace-process` runner module:
/// read the request payload from stdin or `--request <path>`, load the runner
/// config, dispatch the selected [`RunnerCliMode`], and write the compact
/// `RunResult` JSON to stdout or `--output <path>`.
pub(crate) fn run(args: std::env::Args) -> Result<()> {
    let config = RunnerCliConfig::parse(args)?;
    wait_for_start_ack(config.start_ack_fd)?;
    let request_json = read_payload(config.request_path.as_ref())?;
    let request: sandbox_runtime_namespace_process::runner::protocol::NamespaceCommandRequest =
        serde_json::from_str(&request_json).context("failed to decode ns-runner request JSON")?;
    let runner_config = load_runner_config()?;
    let mut output_target = OutputTarget::open(config.output_path.as_ref())?;
    let result = match config.mode {
        RunnerCliMode::RemountOverlay => {
            sandbox_runtime_namespace_process::runner::protocol::RunResult {
                exit_code: 0,
                payload: sandbox_runtime_namespace_process::runner::setns::remount_overlay(
                    &request,
                    &runner_config,
                )
                .context("ns-runner remount overlay failed")?,
            }
        }
        RunnerCliMode::MountOverlay => {
            sandbox_runtime_namespace_process::runner::setns::setns_overlay_mount(
                &request,
                &runner_config,
            )
            .context("ns-runner setns overlay mount failed")?;
            ok_result()
        }
        RunnerCliMode::ConfigureDns => {
            sandbox_runtime_namespace_process::runner::protocol::RunResult {
                exit_code: 0,
                payload: sandbox_runtime_namespace_process::runner::setns::configure_dns(&request)
                    .context("ns-runner configure dns failed")?,
            }
        }
        RunnerCliMode::Run => {
            sandbox_runtime_namespace_process::runner::run(&request).context("ns-runner failed")?
        }
    };
    let output = serde_json::to_vec(&result).context("failed to encode ns-runner result JSON")?;
    write_payload(&mut output_target, &output)
}

fn ok_result() -> sandbox_runtime_namespace_process::runner::protocol::RunResult {
    sandbox_runtime_namespace_process::runner::protocol::RunResult {
        exit_code: 0,
        payload: serde_json::json!({"success": true, "status": "ok"}),
    }
}

fn load_runner_config() -> Result<sandbox_runtime_namespace_process::runner::config::RunnerConfig> {
    let doc = match std::env::var_os(DAEMON_CONFIG_YAML_ENV) {
        Some(path) => {
            let path = PathBuf::from(path);
            sandbox_runtime_config::load_path(&path)
                .with_context(|| format!("load {}", path.display()))?
        }
        None => sandbox_runtime_config::load_prd().context("load eos-sandbox/config/prd.yml")?,
    };
    let config = doc
        .section::<sandbox_runtime_namespace_process::runner::config::RunnerConfig>("runner")
        .context("deserialize runner config section")?;
    config.validate().context("validate runner config")?;
    Ok(config)
}

/// Which ns-runner operation the CLI flags selected; default is command execution.
enum RunnerCliMode {
    Run,
    MountOverlay,
    RemountOverlay,
    ConfigureDns,
}

pub(crate) struct RunnerCliConfig {
    request_path: Option<PathBuf>,
    output_path: Option<PathBuf>,
    start_ack_fd: Option<RawFd>,
    mode: RunnerCliMode,
}

impl RunnerCliConfig {
    pub(crate) fn parse(args: impl IntoIterator<Item = String>) -> Result<Self> {
        let mut request_path = None;
        let mut output_path = None;
        let mut start_ack_fd = None;
        let mut mode = None;
        let mut set_mode = |selected: RunnerCliMode| {
            if mode.is_some() {
                return Err(anyhow!(
                    "ns-runner accepts only one of --mount-overlay, --remount-overlay, or --configure-dns"
                ));
            }
            mode = Some(selected);
            Ok(())
        };
        let mut args = args.into_iter();
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--mount-overlay" => set_mode(RunnerCliMode::MountOverlay)?,
                "--remount-overlay" => set_mode(RunnerCliMode::RemountOverlay)?,
                "--configure-dns" => set_mode(RunnerCliMode::ConfigureDns)?,
                "--request" => {
                    request_path = Some(PathBuf::from(
                        args.next()
                            .ok_or_else(|| anyhow!("--request requires a path"))?,
                    ));
                }
                "--output" => {
                    output_path = Some(PathBuf::from(
                        args.next()
                            .ok_or_else(|| anyhow!("--output requires a path"))?,
                    ));
                }
                "--start-ack-fd" => {
                    start_ack_fd = Some(
                        args.next()
                            .ok_or_else(|| anyhow!("--start-ack-fd requires a file descriptor"))?
                            .parse::<RawFd>()
                            .context("--start-ack-fd must be an integer file descriptor")?,
                    );
                }
                "--help" | "-h" => {
                    println!(
                        "usage: sandbox-daemon ns-runner [--mount-overlay | --remount-overlay | --configure-dns] [--request PATH] [--output PATH]"
                    );
                    std::process::exit(0);
                }
                other if other.starts_with('-') => {
                    return Err(anyhow!("unknown ns-runner flag {other:?}"));
                }
                other => {
                    return Err(anyhow!(
                        "unexpected ns-runner positional argument {other:?}; use --request PATH"
                    ));
                }
            }
        }
        Ok(Self {
            request_path,
            output_path,
            start_ack_fd,
            mode: mode.unwrap_or(RunnerCliMode::Run),
        })
    }
}

fn wait_for_start_ack(fd: Option<RawFd>) -> Result<()> {
    let Some(fd) = fd else {
        return Ok(());
    };
    let file = open_fd_for_read(fd)
        .with_context(|| format!("failed to open ns-runner start ack fd {fd}"))?;
    wait_for_start_ack_reader(file)
}

pub(crate) fn wait_for_start_ack_reader(mut reader: impl Read) -> Result<()> {
    let mut byte = [0_u8; 1];
    reader
        .read_exact(&mut byte)
        .context("ns-runner start ack closed before command start")
}

fn open_fd_for_read(fd: RawFd) -> std::io::Result<File> {
    File::open(format!("/proc/self/fd/{fd}")).or_else(|_| File::open(format!("/dev/fd/{fd}")))
}

fn read_payload(path: Option<&PathBuf>) -> Result<String> {
    let mut payload = String::new();
    if let Some(path) = path {
        std::fs::File::open(path)
            .with_context(|| format!("failed to open request payload {}", path.display()))?
            .read_to_string(&mut payload)
            .with_context(|| format!("failed to read request payload {}", path.display()))?;
    } else {
        std::io::stdin()
            .read_to_string(&mut payload)
            .context("failed to read request payload from stdin")?;
    }
    Ok(payload)
}

enum OutputTarget {
    File(File),
    Stdout,
}

impl OutputTarget {
    fn open(path: Option<&PathBuf>) -> Result<Self> {
        if let Some(path) = path {
            if let Some(parent) = path
                .parent()
                .filter(|parent| !parent.as_os_str().is_empty())
            {
                std::fs::create_dir_all(parent)
                    .with_context(|| format!("failed to create output dir {}", parent.display()))?;
            }
            return File::create(path)
                .map(Self::File)
                .with_context(|| format!("failed to create ns-runner output {}", path.display()));
        }
        Ok(Self::Stdout)
    }
}

fn write_payload(target: &mut OutputTarget, payload: &[u8]) -> Result<()> {
    match target {
        OutputTarget::File(file) => file
            .write_all(payload)
            .context("failed to write ns-runner output")?,
        OutputTarget::Stdout => {
            let mut stdout = std::io::stdout().lock();
            stdout
                .write_all(payload)
                .context("failed to write ns-runner output to stdout")?;
            stdout
                .write_all(b"\n")
                .context("failed to terminate ns-runner output line")?;
        }
    }
    Ok(())
}
