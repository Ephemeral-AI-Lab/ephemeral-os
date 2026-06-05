//! Daemon-owned adapters for neutral workspace-mode traits.

mod file_ports;

pub(crate) use file_ports::EphemeralFilePorts;
#[cfg(target_os = "linux")]
pub(crate) use file_ports::IsolatedFilePorts;
