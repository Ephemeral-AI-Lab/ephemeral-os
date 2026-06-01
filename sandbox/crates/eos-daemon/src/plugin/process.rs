//! Plugin service process specifications.
//!
//! The daemon is the impure owner for service process lifecycle. This module
//! keeps the launch contract explicit and keyed by `PluginServiceKey`: every
//! service process gets a stable `/eos/plugin/ppc/*.sock` endpoint plus the
//! environment a small generic harness needs to connect back to the daemon.

use std::collections::BTreeMap;
use std::io::ErrorKind;
use std::os::unix::net::UnixListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use eos_plugin::{PluginError, PluginServiceKey};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use super::ppc_router::PpcClient;
use crate::error::DaemonError;

pub(crate) const PLUGIN_PPC_ROOT: &str = "/eos/plugin/ppc";
pub(crate) const ENV_PLUGIN_PPC_SOCKET: &str = "EOS_PLUGIN_PPC_SOCKET";
pub(crate) const ENV_PLUGIN_LAYER_STACK_ROOT: &str = "EOS_PLUGIN_LAYER_STACK_ROOT";
pub(crate) const ENV_PLUGIN_WORKSPACE_ROOT: &str = "EOS_PLUGIN_WORKSPACE_ROOT";
pub(crate) const ENV_PLUGIN_ID: &str = "EOS_PLUGIN_ID";
pub(crate) const ENV_PLUGIN_DIGEST: &str = "EOS_PLUGIN_DIGEST";
pub(crate) const ENV_PLUGIN_SERVICE_ID: &str = "EOS_PLUGIN_SERVICE_ID";
pub(crate) const ENV_PLUGIN_SERVICE_PROFILE_DIGEST: &str = "EOS_PLUGIN_SERVICE_PROFILE_DIGEST";
pub(crate) const ENV_PLUGIN_PPC_PROTOCOL_VERSION: &str = "EOS_PLUGIN_PPC_PROTOCOL_VERSION";

#[derive(Debug, Clone)]
pub(crate) struct PluginProcessSpec {
    key: PluginServiceKey,
    command: Vec<String>,
    ppc_protocol_version: u32,
    socket_path: PathBuf,
}

impl PluginProcessSpec {
    pub(crate) fn new(
        key: PluginServiceKey,
        command: Vec<String>,
        ppc_protocol_version: u32,
    ) -> Result<Self, PluginError> {
        Self::new_with_socket_root(key, command, ppc_protocol_version, PLUGIN_PPC_ROOT)
    }

    pub(crate) fn new_with_socket_root(
        key: PluginServiceKey,
        command: Vec<String>,
        ppc_protocol_version: u32,
        socket_root: impl AsRef<Path>,
    ) -> Result<Self, PluginError> {
        if command.is_empty() || command[0].trim().is_empty() {
            return Err(PluginError::Manifest(format!(
                "service {} requires a launch command",
                key.service_id
            )));
        }
        if ppc_protocol_version == 0 {
            return Err(PluginError::Manifest(
                "ppc_protocol_version must be positive".to_owned(),
            ));
        }
        let socket_path = socket_path_for_key(&key, socket_root.as_ref());
        Ok(Self {
            key,
            command,
            ppc_protocol_version,
            socket_path,
        })
    }

    pub(crate) fn environment(&self) -> BTreeMap<&'static str, String> {
        BTreeMap::from([
            (
                ENV_PLUGIN_PPC_SOCKET,
                self.socket_path.to_string_lossy().into_owned(),
            ),
            (
                ENV_PLUGIN_LAYER_STACK_ROOT,
                self.key.layer_stack_root.clone(),
            ),
            (ENV_PLUGIN_WORKSPACE_ROOT, self.key.workspace_root.clone()),
            (ENV_PLUGIN_ID, self.key.plugin_id.clone()),
            (ENV_PLUGIN_DIGEST, self.key.plugin_digest.clone()),
            (ENV_PLUGIN_SERVICE_ID, self.key.service_id.clone()),
            (
                ENV_PLUGIN_SERVICE_PROFILE_DIGEST,
                self.key.service_profile_digest.clone(),
            ),
            (
                ENV_PLUGIN_PPC_PROTOCOL_VERSION,
                self.ppc_protocol_version.to_string(),
            ),
        ])
    }

    pub(crate) fn service_instance_id(&self) -> String {
        self.key.service_instance_id()
    }

    pub(crate) fn spawn(&self) -> Result<PluginServiceProcess, DaemonError> {
        let mut command = Command::new(&self.command[0]);
        command
            .args(&self.command[1..])
            .envs(self.environment())
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        let child = command.spawn()?;
        let process_group_id = i32::try_from(child.id()).ok();
        Ok(PluginServiceProcess {
            spec: self.clone(),
            child,
            process_group_id,
            torn_down: false,
        })
    }

    pub(crate) fn spawn_connected(
        &self,
        timeout: Duration,
    ) -> Result<(PluginServiceProcess, PpcClient), DaemonError> {
        let listener = bind_ppc_listener(&self.socket_path)?;
        let mut process = self.spawn()?;
        match accept_ppc_client(&listener, &mut process, timeout) {
            Ok(client) => Ok((process, client)),
            Err(err) => {
                process.teardown();
                Err(err)
            }
        }
    }

    pub(crate) fn to_json(&self) -> Value {
        json!({
            "service_id": self.key.service_id,
            "service_instance_id": self.key.service_instance_id(),
            "command": self.command,
            "socket_path": self.socket_path,
            "env": self.environment(),
            "ppc_protocol_version": self.ppc_protocol_version,
            "process_started": false,
        })
    }
}

#[derive(Debug)]
pub(crate) struct PluginServiceProcess {
    spec: PluginProcessSpec,
    child: Child,
    process_group_id: Option<i32>,
    torn_down: bool,
}

impl PluginServiceProcess {
    pub(crate) fn status_json(&mut self) -> Value {
        let exit_status = self.child.try_wait().ok().flatten();
        let running = exit_status.is_none();
        json!({
            "service_id": self.spec.key.service_id,
            "service_instance_id": self.spec.service_instance_id(),
            "pid": self.child.id(),
            "process_group_id": self.process_group_id,
            "running": running,
            "exit_status": exit_status.and_then(|status| status.code()),
            "socket_path": self.spec.socket_path,
        })
    }

    pub(crate) fn teardown(&mut self) {
        if self.torn_down {
            return;
        }
        self.torn_down = true;
        if self.child.try_wait().ok().flatten().is_some() {
            return;
        }
        terminate_process_group(self.process_group_id);
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for PluginServiceProcess {
    fn drop(&mut self) {
        self.teardown();
    }
}

#[cfg(target_os = "linux")]
fn terminate_process_group(process_group_id: Option<i32>) {
    use nix::sys::signal::{killpg, Signal};
    use nix::unistd::Pid;

    let Some(process_group_id) = process_group_id else {
        return;
    };
    if killpg(Pid::from_raw(process_group_id), Signal::SIGTERM).is_ok() {
        std::thread::sleep(std::time::Duration::from_millis(50));
        let _ = killpg(Pid::from_raw(process_group_id), Signal::SIGKILL);
    }
}

#[cfg(not(target_os = "linux"))]
fn terminate_process_group(_process_group_id: Option<i32>) {}

fn socket_path_for_key(key: &PluginServiceKey, socket_root: &Path) -> PathBuf {
    let mut hasher = Sha256::new();
    hasher.update(key.service_instance_id().as_bytes());
    hasher.update(b"\0");
    hasher.update(key.plugin_digest.as_bytes());
    let digest = hasher.finalize();
    socket_root.join(format!("{}.sock", lower_hex_16(&digest[..16])))
}

fn lower_hex_16(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(char::from(HEX[usize::from(byte >> 4)]));
        out.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    out
}

fn bind_ppc_listener(socket_path: &Path) -> Result<UnixListener, DaemonError> {
    if let Some(parent) = socket_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    match std::fs::remove_file(socket_path) {
        Ok(()) => {}
        Err(err) if err.kind() == ErrorKind::NotFound => {}
        Err(err) => return Err(err.into()),
    }
    let listener = UnixListener::bind(socket_path)?;
    listener.set_nonblocking(true)?;
    Ok(listener)
}

fn accept_ppc_client(
    listener: &UnixListener,
    process: &mut PluginServiceProcess,
    timeout: Duration,
) -> Result<PpcClient, DaemonError> {
    let deadline = Instant::now() + timeout;
    loop {
        match listener.accept() {
            Ok((stream, _addr)) => {
                stream.set_nonblocking(false)?;
                return Ok(PpcClient { stream });
            }
            Err(err) if err.kind() == ErrorKind::WouldBlock => {}
            Err(err) => return Err(err.into()),
        }
        if let Some(status) = process.child.try_wait()? {
            return Err(PluginError::Ensure(format!(
                "plugin service {} exited before PPC connect: {status}",
                process.spec.key.service_id
            ))
            .into());
        }
        if Instant::now() >= deadline {
            return Err(PluginError::Ensure(format!(
                "timed out waiting for plugin service {} to connect PPC socket {}",
                process.spec.key.service_id,
                process.spec.socket_path.display()
            ))
            .into());
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use eos_plugin::{RefreshStrategy, ServiceMode};

    fn key(profile: &str) -> PluginServiceKey {
        PluginServiceKey::new(
            "/eos/plugin/layer-stack",
            "/eos/plugin/workspace",
            "demo",
            "digest-a",
            "indexer",
            profile,
            ServiceMode::WorkspaceSnapshotRefresh,
            RefreshStrategy::RemountWorkspaceAndNotify,
        )
        .expect("valid key")
    }

    #[test]
    fn process_spec_uses_stable_eos_plugin_socket_and_env() {
        let spec = PluginProcessSpec::new(
            key("profile-a"),
            vec!["demo-indexer".to_owned(), "--stdio".to_owned()],
            1,
        )
        .expect("process spec");
        let env = spec.environment();

        assert!(env[ENV_PLUGIN_PPC_SOCKET].starts_with("/eos/plugin/ppc/"));
        assert!(env[ENV_PLUGIN_PPC_SOCKET].ends_with(".sock"));
        assert_eq!(env[ENV_PLUGIN_LAYER_STACK_ROOT], "/eos/plugin/layer-stack");
        assert_eq!(env[ENV_PLUGIN_WORKSPACE_ROOT], "/eos/plugin/workspace");
        assert_eq!(env[ENV_PLUGIN_ID], "demo");
        assert_eq!(env[ENV_PLUGIN_SERVICE_ID], "indexer");
        assert_eq!(env[ENV_PLUGIN_PPC_PROTOCOL_VERSION], "1");
    }

    #[test]
    fn process_spec_key_changes_socket_path() {
        let first = PluginProcessSpec::new(key("profile-a"), vec!["svc".to_owned()], 1)
            .expect("first spec");
        let second = PluginProcessSpec::new(key("profile-b"), vec!["svc".to_owned()], 1)
            .expect("second spec");

        assert_ne!(
            first.environment()[ENV_PLUGIN_PPC_SOCKET],
            second.environment()[ENV_PLUGIN_PPC_SOCKET]
        );
    }

    #[test]
    fn process_spec_rejects_empty_command() {
        assert!(matches!(
            PluginProcessSpec::new(key("profile-a"), Vec::new(), 1),
            Err(PluginError::Manifest(message)) if message.contains("launch command")
        ));
    }

    #[test]
    fn spawned_process_reports_running_then_tears_down() {
        let spec = PluginProcessSpec::new(
            key("profile-a"),
            vec![
                "/bin/sh".to_owned(),
                "-c".to_owned(),
                "test \"$EOS_PLUGIN_SERVICE_ID\" = indexer && sleep 30".to_owned(),
            ],
            1,
        )
        .expect("process spec");
        let mut process = spec.spawn().expect("spawn service process");

        let status = process.status_json();
        assert_eq!(status["service_id"], "indexer");
        assert_eq!(status["running"], true);
        assert!(status["pid"].as_u64().expect("pid") > 0);

        process.teardown();
        let status = process.status_json();
        assert_eq!(status["running"], false);
    }

    #[test]
    fn spawn_connected_accepts_ppc_socket() {
        let root = test_socket_root("spawn-connected");
        let spec = PluginProcessSpec::new_with_socket_root(
            key("profile-a"),
            vec!["/bin/sh".to_owned(), "-c".to_owned(), "sleep 30".to_owned()],
            1,
            &root,
        )
        .expect("process spec");
        let socket_root = root.clone();
        let connector = std::thread::spawn(move || {
            let socket = wait_for_socket(&socket_root);
            std::os::unix::net::UnixStream::connect(socket).expect("connect ppc socket");
        });

        let (mut process, _client) = spec
            .spawn_connected(Duration::from_secs(1))
            .expect("spawn and accept service PPC");
        connector.join().expect("connector thread");
        process.teardown();
        let _ = std::fs::remove_dir_all(root);
    }

    fn test_socket_root(name: &str) -> PathBuf {
        let root = PathBuf::from("target").join(format!("ppc-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        root
    }

    fn wait_for_socket(root: &Path) -> PathBuf {
        let deadline = Instant::now() + Duration::from_secs(1);
        loop {
            if let Ok(entries) = std::fs::read_dir(root) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.extension().and_then(|ext| ext.to_str()) == Some("sock") {
                        return path;
                    }
                }
            }
            assert!(
                Instant::now() < deadline,
                "timed out waiting for socket under {}",
                root.display()
            );
            std::thread::sleep(Duration::from_millis(10));
        }
    }
}
