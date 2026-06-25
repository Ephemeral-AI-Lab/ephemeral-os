//! Pure mapping from config + record + token to the in-container daemon argv.

use std::path::Path;

use sandbox_config::configs::manager::DockerRuntimeConfig;
use sandbox_manager::SandboxRecord;

/// In-container Unix socket the daemon binds (unused by the host, which uses TCP).
pub const CONTAINER_RUNTIME_SOCKET: &str = "/tmp/eos-runtime.sock";
/// In-container pid file the daemon writes.
pub const CONTAINER_RUNTIME_PID: &str = "/tmp/eos-runtime.pid";
/// The daemon binds its TCP listener on all container interfaces; Docker
/// publishes it to a loopback host port.
pub const DAEMON_TCP_BIND_HOST: &str = "0.0.0.0";

/// Build the foreground `Cmd` argv that runs the uploaded daemon binary inside
/// the container, using container-side paths and the per-sandbox auth token.
#[must_use]
pub fn daemon_launch_argv(
    config: &DockerRuntimeConfig,
    record: &SandboxRecord,
    auth_token: &str,
) -> Vec<String> {
    vec![
        path_string(&config.container_daemon_binary_path),
        "serve".to_owned(),
        "--config-yaml".to_owned(),
        path_string(&config.container_daemon_config_yaml_path),
        "--workspace-root".to_owned(),
        path_string(&config.container_workspace_root),
        "--socket".to_owned(),
        CONTAINER_RUNTIME_SOCKET.to_owned(),
        "--pid-file".to_owned(),
        CONTAINER_RUNTIME_PID.to_owned(),
        "--tcp-host".to_owned(),
        DAEMON_TCP_BIND_HOST.to_owned(),
        "--tcp-port".to_owned(),
        config.daemon_port.to_string(),
        "--auth-token".to_owned(),
        auth_token.to_owned(),
        "--sandbox-id".to_owned(),
        record.id.as_str().to_owned(),
    ]
}

fn path_string(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}
