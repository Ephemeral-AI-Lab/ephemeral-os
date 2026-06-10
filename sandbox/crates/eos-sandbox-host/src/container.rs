//! `DaemonContainer` — one Docker container running one `eosd`, driven entirely
//! through the Docker CLI plus the Engine archive API for binary upload.
//!
//! Container lifecycle (create / upload / spawn / teardown) is *infrastructure*,
//! not a sandbox operation, so it is allowed to use `docker` directly. Sandbox
//! operations go exclusively through [`ProtocolClient`] over the wire.
//! Container-filesystem peeking is never used as a verification oracle.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use serde_json::json;

use crate::client::{is_success, ProtocolClient};
use crate::docker::{
    docker, docker_exec_args, parse_published_addr, path_str, put_archive_bytes, put_archive_file,
};
use crate::wire::HEARTBEAT_OP;

/// How long a container outlives its `DaemonContainer` handle.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ContainerLifetime {
    /// Survives drop (warm reuse); the owner reaps it explicitly.
    Keep,
    /// Removed on drop, with an in-container `timeout` bounding the lifetime
    /// so a leaked container reclaims itself.
    SelfDestruct {
        /// Upper bound on the container lifetime.
        ttl: Duration,
    },
}

/// `docker run` parameters for one sandbox container.
#[derive(Debug, Clone)]
pub struct ContainerSpec {
    /// Container name (also the handle docker commands use).
    pub name: String,
    /// Image to run.
    pub image: String,
    /// Optional `--platform` (e.g. `linux/amd64`).
    pub platform: Option<String>,
    /// Extra `--cap-add` entries.
    pub cap_add: Vec<String>,
    /// Extra `--security-opt` entries.
    pub security_opt: Vec<String>,
    /// Extra `--tmpfs` mounts.
    pub tmpfs: Vec<String>,
    /// Labels stamped on the container; the caller owns the vocabulary
    /// (fleet registry labels, pool adoption labels, ...).
    pub labels: Vec<(String, String)>,
    /// Drop/reuse behavior.
    pub lifetime: ContainerLifetime,
}

/// Daemon bring-up parameters: the binary, its merged config, and timing.
#[derive(Debug, Clone)]
pub struct DaemonSpec {
    /// Host path of the static `eosd` binary to upload.
    pub eosd_path: PathBuf,
    /// In-container daemon state dir (socket/pid/log live here).
    pub remote_daemon_dir: PathBuf,
    /// In-container path the binary is uploaded to.
    pub remote_eosd_path: PathBuf,
    /// In-container path the merged config document is uploaded to.
    pub remote_config_path: PathBuf,
    /// Merged config document content.
    pub config_yaml: String,
    /// Additional in-container dirs to create at bring-up.
    pub extra_dirs: Vec<PathBuf>,
    /// In-container TCP port the daemon binds (published to loopback).
    pub tcp_port: u16,
    /// Budget for the ready gate.
    pub ready_timeout: Duration,
    /// Per-request socket timeout.
    pub request_timeout: Duration,
}

/// A live daemon container.
#[derive(Debug)]
pub struct DaemonContainer {
    name: String,
    client: ProtocolClient,
    daemon_log_path: String,
    token: String,
    keep: bool,
}

impl DaemonContainer {
    /// Create a container, upload `eosd` plus its merged config, spawn the
    /// daemon (TCP + auth), and block until the ready gate passes.
    ///
    /// # Errors
    /// Returns an error if any docker step fails or the daemon never becomes ready.
    pub fn start(
        container: &ContainerSpec,
        daemon: &DaemonSpec,
        auth_token: String,
    ) -> Result<Self> {
        let keep = container.lifetime == ContainerLifetime::Keep;
        let mut run = vec![
            "run".to_owned(),
            "-d".to_owned(),
            "--name".to_owned(),
            container.name.clone(),
        ];
        for (key, value) in &container.labels {
            run.push("--label".to_owned());
            run.push(format!("{key}={value}"));
        }
        if !keep {
            run.push("--rm".to_owned());
        }
        // The isolated-workspace tier creates a per-workspace cgroup under
        // /sys/fs/cgroup, which Docker mounts read-only under plain --cap-add
        // (EROFS, e.g. on Docker Desktop). --privileged makes cgroup2 writable so
        // the real ns-holder/veth/cgroup path runs. Sandboxes already require
        // SYS_ADMIN/NET_ADMIN + unconfined seccomp/apparmor, so this is an
        // acceptable superset; the explicit caps below remain for documentation
        // and hosts where privileged is unavailable.
        run.push("--privileged".to_owned());
        if let Some(platform) = &container.platform {
            run.push("--platform".to_owned());
            run.push(platform.clone());
        }
        for cap in &container.cap_add {
            run.push("--cap-add".to_owned());
            run.push(cap.clone());
        }
        for opt in &container.security_opt {
            run.push("--security-opt".to_owned());
            run.push(opt.clone());
        }
        for tmpfs in &container.tmpfs {
            run.push("--tmpfs".to_owned());
            run.push(tmpfs.clone());
        }
        run.push("--init".to_owned());
        run.push("-p".to_owned());
        run.push(format!("127.0.0.1::{}", daemon.tcp_port));
        run.push(container.image.clone());
        // Keep the container alive but self-terminating: `timeout` bounds the
        // lifetime so a leaked (`--rm`) container is reclaimed automatically.
        match container.lifetime {
            ContainerLifetime::Keep => run.extend(["sleep".to_owned(), "infinity".to_owned()]),
            ContainerLifetime::SelfDestruct { ttl } => run.extend([
                "timeout".to_owned(),
                ttl.as_secs().to_string(),
                "sleep".to_owned(),
                "infinity".to_owned(),
            ]),
        }

        docker(&run).with_context(|| format!("docker run for {}", container.name))?;

        // From here, any failure must still tear the container down.
        let mut handle = Self {
            name: container.name.clone(),
            // Placeholder client; replaced once the port is resolved.
            client: ProtocolClient::new(
                placeholder_addr(),
                Some(auth_token.clone()),
                daemon.request_timeout,
            ),
            daemon_log_path: daemon
                .remote_daemon_dir
                .join("runtime.log")
                .to_string_lossy()
                .into_owned(),
            token: auth_token.clone(),
            keep,
        };
        match handle.bringup(daemon, &auth_token) {
            Ok(client) => {
                handle.client = client;
                Ok(handle)
            }
            Err(err) => {
                let log = handle.daemon_log().unwrap_or_default();
                drop(handle);
                Err(err.context(format!("daemon bringup failed; log tail:\n{log}")))
            }
        }
    }

    /// A non-owning handle over a registry-tracked container, for engine
    /// exec/respawn paths. Never removes the container on drop and performs
    /// no liveness checks; `endpoint` seeds the wire client when known.
    #[must_use]
    pub fn for_engine(
        name: String,
        auth_token: String,
        daemon: &DaemonSpec,
        endpoint: Option<SocketAddr>,
    ) -> Self {
        Self {
            name,
            client: ProtocolClient::new(
                endpoint.unwrap_or_else(placeholder_addr),
                Some(auth_token.clone()),
                daemon.request_timeout,
            ),
            daemon_log_path: daemon
                .remote_daemon_dir
                .join("runtime.log")
                .to_string_lossy()
                .into_owned(),
            token: auth_token,
            keep: true,
        }
    }

    /// Adopt an already-running container whose daemon was spawned with
    /// `auth_token`: resolve its published port and pass the ready gate. The
    /// adopted handle is always [`ContainerLifetime::Keep`].
    ///
    /// # Errors
    /// Returns an error if the port cannot be resolved or the daemon is not ready.
    pub fn adopt(id: &str, auth_token: String, daemon: &DaemonSpec) -> Result<Self> {
        let mut handle = Self {
            name: id.to_owned(),
            client: ProtocolClient::new(
                placeholder_addr(),
                Some(auth_token.clone()),
                daemon.request_timeout,
            ),
            daemon_log_path: daemon
                .remote_daemon_dir
                .join("runtime.log")
                .to_string_lossy()
                .into_owned(),
            token: auth_token.clone(),
            keep: true,
        };
        let addr = handle.resolve_addr(daemon.tcp_port)?;
        let client = ProtocolClient::new(addr, Some(auth_token), daemon.request_timeout);
        await_ready(&client, daemon.ready_timeout)?;
        handle.client = client;
        Ok(handle)
    }

    fn bringup(&self, daemon: &DaemonSpec, token: &str) -> Result<ProtocolClient> {
        let daemon_dir = path_str(&daemon.remote_daemon_dir)?;
        let remote_eosd_path = path_str(&daemon.remote_eosd_path)?;
        let config_dir = daemon
            .remote_config_path
            .parent()
            .context("remote config path has no parent")?;
        let config_dir = path_str(config_dir)?;
        let config_name = daemon
            .remote_config_path
            .file_name()
            .and_then(|name| name.to_str())
            .context("remote config path has no UTF-8 file name")?;

        let mut mkdir = vec!["mkdir", "-p", &daemon_dir, &config_dir];
        let extra_dirs = daemon
            .extra_dirs
            .iter()
            .map(|dir| path_str(dir))
            .collect::<Result<Vec<_>>>()?;
        mkdir.extend(extra_dirs.iter().map(String::as_str));
        self.exec(&mkdir).context("mkdir daemon dirs")?;
        self.exec(&[
            "sh",
            "-lc",
            "mount -o remount,rw /sys/fs/cgroup 2>/dev/null || true; test -w /sys/fs/cgroup",
        ])
        .context("make cgroup v2 writable for isolated workspaces")?;
        put_archive_file(
            &self.name,
            &daemon_dir,
            "eosd",
            &daemon.eosd_path,
            daemon.request_timeout,
        )
        .with_context(|| {
            format!(
                "Docker put_archive eosd ({}) into {daemon_dir}",
                daemon.eosd_path.display()
            )
        })?;
        put_archive_bytes(
            &self.name,
            &config_dir,
            config_name,
            daemon.config_yaml.as_bytes(),
            0o644,
            daemon.request_timeout,
        )
        .with_context(|| format!("Docker put_archive merged config into {config_dir}"))?;

        // Spawn the daemon detached: `--spawn` re-execs a foreground child with
        // stdout/stderr redirected to `--log-file`, so bringup diagnostics land in
        // runtime.log (a plain foreground daemon parses but ignores `--log-file`).
        self.exec(&[
            "-d",
            &remote_eosd_path,
            "daemon",
            "--spawn",
            "--socket",
            &format!("{daemon_dir}/runtime.sock"),
            "--pid-file",
            &format!("{daemon_dir}/runtime.pid"),
            "--log-file",
            &format!("{daemon_dir}/runtime.log"),
            "--tcp-host",
            "0.0.0.0",
            "--tcp-port",
            &daemon.tcp_port.to_string(),
            "--auth-token",
            token,
        ])
        .context("spawn eosd daemon")?;

        let addr = self.resolve_addr(daemon.tcp_port)?;
        let client = ProtocolClient::new(addr, Some(token.to_owned()), daemon.request_timeout);
        await_ready(&client, daemon.ready_timeout)?;
        Ok(client)
    }

    /// Map the published TCP port to a host `SocketAddr` (retrying briefly while
    /// docker wires up the port binding).
    fn resolve_addr(&self, container_port: u16) -> Result<SocketAddr> {
        let deadline = Instant::now() + Duration::from_secs(15);
        loop {
            if let Ok(out) = docker(&[
                "port".to_owned(),
                self.name.clone(),
                format!("{container_port}/tcp"),
            ]) {
                if let Some(addr) = parse_published_addr(&out) {
                    return Ok(addr);
                }
            }
            if Instant::now() >= deadline {
                bail!(
                    "could not resolve published port {container_port} for {}",
                    self.name
                );
            }
            thread::sleep(Duration::from_millis(200));
        }
    }

    /// The wire client for this container.
    #[must_use]
    pub fn client(&self) -> &ProtocolClient {
        &self.client
    }

    /// The container name.
    #[must_use]
    pub fn name(&self) -> &str {
        &self.name
    }

    /// Run a `docker exec <name> ...` against this container (lifecycle/provision
    /// only — never used as a verification oracle).
    ///
    /// # Errors
    /// Returns an error if the exec exits non-zero.
    pub fn exec(&self, argv: &[&str]) -> Result<String> {
        // `exec` argv may start with docker flags like `-d`; the container name
        // goes after them and before the command. Everything after the command
        // token is passed through verbatim.
        docker(&docker_exec_args(&self.name, argv))
    }

    /// Restart the in-container `eosd`: hard-kill (SIGKILL) the running daemon so
    /// graceful-shutdown cleanup does NOT run, clear the stale socket/pid, then
    /// re-spawn it with the same socket, pid, log, TCP port, and auth token, and
    /// block until the ready gate passes. The published container port is owned
    /// by Docker, so the existing wire client stays valid across the restart.
    /// This exercises daemon startup-recovery paths (e.g. isolated-handle orphan
    /// reconciliation); the spawn mirrors [`Self::bringup`].
    ///
    /// # Errors
    /// Returns an error if the respawn exec fails or the daemon never becomes
    /// ready within the configured budget.
    pub fn restart_daemon(&self, daemon: &DaemonSpec) -> Result<()> {
        let daemon_dir = path_str(&daemon.remote_daemon_dir)?;
        let remote_eosd_path = path_str(&daemon.remote_eosd_path)?;
        let teardown = format!(
            "kill -9 \"$(cat {daemon_dir}/runtime.pid 2>/dev/null)\" 2>/dev/null; \
             pkill -9 -f 'eosd daemon' 2>/dev/null; sleep 1; \
             rm -f {daemon_dir}/runtime.sock {daemon_dir}/runtime.pid"
        );
        let _ = self.exec(&["sh", "-lc", &teardown]);
        self.respawn_daemon(&daemon_dir, &remote_eosd_path, daemon.tcp_port)
            .context("respawn eosd daemon")?;
        await_ready(&self.client, daemon.ready_timeout).context("daemon not ready after restart")
    }

    /// Re-exec the daemon in place with this container's original spawn flags.
    fn respawn_daemon(
        &self,
        daemon_dir: &str,
        remote_eosd_path: &str,
        tcp_port: u16,
    ) -> Result<String> {
        self.exec(&[
            "-d",
            remote_eosd_path,
            "daemon",
            "--spawn",
            "--socket",
            &format!("{daemon_dir}/runtime.sock"),
            "--pid-file",
            &format!("{daemon_dir}/runtime.pid"),
            "--log-file",
            &format!("{daemon_dir}/runtime.log"),
            "--tcp-host",
            "0.0.0.0",
            "--tcp-port",
            &tcp_port.to_string(),
            "--auth-token",
            &self.token,
        ])
    }

    /// Best-effort tail of the daemon log for diagnostics (not an oracle).
    fn daemon_log(&self) -> Option<String> {
        docker(&[
            "exec".to_owned(),
            self.name.clone(),
            "tail".to_owned(),
            "-n".to_owned(),
            "40".to_owned(),
            self.daemon_log_path.clone(),
        ])
        .ok()
    }
}

impl Drop for DaemonContainer {
    fn drop(&mut self) {
        if self.keep {
            return;
        }
        let _ = docker(&["rm".to_owned(), "-f".to_owned(), self.name.clone()]);
    }
}

fn placeholder_addr() -> SocketAddr {
    SocketAddr::from(([127, 0, 0, 1], 1))
}

/// The bring-up ready gate: poll heartbeat until the daemon answers with
/// success, with exponential backoff. `sandbox.runtime.ready` cannot gate
/// provisioning — its `control_plane` probe requires a seeded workspace base
/// (see [`crate::wire::HEARTBEAT_OP`]).
fn await_ready(client: &ProtocolClient, budget: Duration) -> Result<()> {
    let deadline = Instant::now() + budget;
    let mut delay = Duration::from_millis(150);
    loop {
        let observed = match client.request(HEARTBEAT_OP, "ready-probe", &json!({})) {
            Ok(resp) if is_success(&resp) => return Ok(()),
            Ok(resp) => format!("non-success heartbeat: {resp}"),
            Err(err) => err.to_string(),
        };
        if Instant::now() >= deadline {
            bail!("daemon not ready within {budget:?}: {observed}");
        }
        thread::sleep(delay);
        delay = (delay * 2).min(Duration::from_secs(2));
    }
}
