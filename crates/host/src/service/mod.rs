pub(crate) mod forward;
pub(crate) mod registry;

mod args;
mod container_ops;
mod docker_json;
mod forwarding;
mod image_ops;
mod lifecycle;
mod types;
mod utils;

pub use forward::ForwardError;
pub use types::{HostConfig, HostForwardRequest, SandboxHost, SandboxStatus};

pub(crate) use args::workspace_root_from_args;
pub(crate) use types::ManagedSandboxStart;

const SANDBOX_SCRATCH_TMPFS: &str = "/eos/scratch:rw,exec,size=2g,mode=1777";
const SANDBOX_OVERLAY_ROOT: &str = "/eos/scratch/overlay";
const DEFAULT_WORKSPACE_ROOT: &str = "/testbed";
