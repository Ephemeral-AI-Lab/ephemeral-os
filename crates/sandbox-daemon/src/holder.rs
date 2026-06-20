//! Namespace holder subcommand adapter.

use std::os::fd::RawFd;

use anyhow::{anyhow, Context, Result};

/// `<binary> ns-holder <readiness_fd> <control_fd> [shared|isolated]` becomes
/// the single-threaded child that creates and pins a workspace namespace stack,
/// runs the readiness handshake, then `pause()`s until `SIGTERM`.
pub(crate) fn run(mut args: std::env::Args) -> Result<()> {
    let readiness_fd = parse_fd(args.next(), "readiness_fd")?;
    let control_fd = parse_fd(args.next(), "control_fd")?;
    let network = parse_holder_network(args.next())?;

    match namespace_process::holder::run(readiness_fd, control_fd, network) {
        Ok(()) => Ok(()),
        Err(err) => {
            let code = match &err {
                namespace_process::holder::NsHolderError::ControlPipeClosed => {
                    namespace_process::holder::NsHolderError::CONTROL_CLOSED_EXIT
                }
                namespace_process::holder::NsHolderError::UnexpectedToken => {
                    namespace_process::holder::NsHolderError::UNEXPECTED_TOKEN_EXIT
                }
                namespace_process::holder::NsHolderError::TestCrash => {
                    namespace_process::holder::NsHolderError::TEST_CRASH_EXIT
                }
                _ => return Err(anyhow::Error::new(err).context("ns-holder failed")),
            };
            std::process::exit(code);
        }
    }
}

fn parse_holder_network(
    value: Option<String>,
) -> Result<namespace_process::holder::NamespaceNetwork> {
    match value.as_deref() {
        None | Some("isolated") => Ok(namespace_process::holder::NamespaceNetwork::Isolated),
        Some("shared") => Ok(namespace_process::holder::NamespaceNetwork::Shared),
        Some(other) => Err(anyhow!(
            "invalid ns-holder network mode {other:?}; expected shared or isolated"
        )),
    }
}

fn parse_fd(value: Option<String>, name: &str) -> Result<RawFd> {
    value
        .ok_or_else(|| anyhow!("missing {name} argument for ns-holder"))?
        .parse::<RawFd>()
        .with_context(|| format!("{name} must be an integer file descriptor"))
}
