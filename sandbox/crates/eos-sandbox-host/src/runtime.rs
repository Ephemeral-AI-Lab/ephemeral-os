use std::ffi::OsStr;
use std::fs;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use anyhow::{bail, Context, Result};
use serde_json::json;

use crate::protocol::{is_success, ProtocolClient, HEARTBEAT_OP};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ContainerLifetime {
    Keep,
    SelfDestruct { ttl: Duration },
}

#[derive(Debug, Clone)]
pub struct ContainerSpec {
    pub name: String,
    pub image: String,
    pub platform: Option<String>,
    pub cap_add: Vec<String>,
    pub security_opt: Vec<String>,
    pub tmpfs: Vec<String>,
    pub labels: Vec<(String, String)>,
    pub lifetime: ContainerLifetime,
}

#[derive(Debug, Clone)]
pub struct DaemonSpec {
    pub eosd_path: PathBuf,
    pub remote_daemon_dir: PathBuf,
    pub remote_eosd_path: PathBuf,
    pub remote_config_path: PathBuf,
    pub config_yaml: String,
    pub extra_dirs: Vec<PathBuf>,
    pub tcp_port: u16,
    pub ready_timeout: Duration,
    pub request_timeout: Duration,
}

#[derive(Debug)]
pub struct DaemonContainer {
    name: String,
    client: ProtocolClient,
    daemon_log_path: String,
    token: String,
    keep: bool,
}

impl DaemonContainer {
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
        match container.lifetime {
            ContainerLifetime::Keep => run.extend(["sleep".to_owned(), "infinity".to_owned()]),
            ContainerLifetime::SelfDestruct { ttl } => run.extend([
                "timeout".to_owned(),
                ttl.as_secs().to_string(),
                "sleep".to_owned(),
                "infinity".to_owned(),
            ]),
        }

        docker(run).with_context(|| format!("docker run for {}", container.name))?;

        let mut handle = Self::handle(
            container.name.clone(),
            auth_token.clone(),
            daemon,
            None,
            keep,
        );
        match handle.bringup(daemon) {
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
    pub(crate) fn for_engine(
        name: String,
        auth_token: String,
        daemon: &DaemonSpec,
        endpoint: Option<SocketAddr>,
    ) -> Self {
        Self::handle(name, auth_token, daemon, endpoint, true)
    }

    pub fn adopt(id: &str, auth_token: String, daemon: &DaemonSpec) -> Result<Self> {
        let mut handle = Self::handle(id.to_owned(), auth_token.clone(), daemon, None, true);
        let addr = wait_for_published_addr(id, daemon.tcp_port)?;
        let client = ProtocolClient::new(addr, Some(auth_token), daemon.request_timeout);
        await_ready(&client, daemon.ready_timeout)?;
        handle.client = client;
        Ok(handle)
    }

    fn handle(
        name: String,
        auth_token: String,
        daemon: &DaemonSpec,
        endpoint: Option<SocketAddr>,
        keep: bool,
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
            keep,
        }
    }

    fn bringup(&self, daemon: &DaemonSpec) -> Result<ProtocolClient> {
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
        copy_file_into(&self.name, &daemon_dir, "eosd", &daemon.eosd_path).with_context(|| {
            format!(
                "copy eosd ({}) into {daemon_dir}",
                daemon.eosd_path.display()
            )
        })?;
        copy_bytes_into(
            &self.name,
            &config_dir,
            config_name,
            daemon.config_yaml.as_bytes(),
            0o644,
        )
        .with_context(|| format!("copy merged config into {config_dir}"))?;

        self.spawn_daemon(&daemon_dir, &remote_eosd_path, daemon.tcp_port)
            .context("spawn eosd daemon")?;

        let addr = wait_for_published_addr(&self.name, daemon.tcp_port)?;
        let client = ProtocolClient::new(addr, Some(self.token.clone()), daemon.request_timeout);
        await_ready(&client, daemon.ready_timeout)?;
        Ok(client)
    }
    pub fn client(&self) -> &ProtocolClient {
        &self.client
    }

    pub fn exec(&self, argv: &[&str]) -> Result<String> {
        docker(docker_exec_args(&self.name, argv))
    }

    pub fn restart_daemon(&self, daemon: &DaemonSpec) -> Result<()> {
        let daemon_dir = path_str(&daemon.remote_daemon_dir)?;
        let remote_eosd_path = path_str(&daemon.remote_eosd_path)?;
        let teardown = format!(
            "kill -9 \"$(cat {daemon_dir}/runtime.pid 2>/dev/null)\" 2>/dev/null; \
             pkill -9 -f 'eosd daemon' 2>/dev/null; sleep 1; \
             rm -f {daemon_dir}/runtime.sock {daemon_dir}/runtime.pid"
        );
        let _ = self.exec(&["sh", "-lc", &teardown]);
        self.spawn_daemon(&daemon_dir, &remote_eosd_path, daemon.tcp_port)
            .context("respawn eosd daemon")?;
        await_ready(&self.client, daemon.ready_timeout).context("daemon not ready after restart")
    }

    fn spawn_daemon(
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

    fn daemon_log(&self) -> Option<String> {
        docker([
            "exec",
            self.name.as_str(),
            "tail",
            "-n",
            "40",
            self.daemon_log_path.as_str(),
        ])
        .ok()
    }
}

impl Drop for DaemonContainer {
    fn drop(&mut self) {
        if self.keep {
            return;
        }
        let _ = docker(["rm", "-f", self.name.as_str()]);
    }
}

fn placeholder_addr() -> SocketAddr {
    SocketAddr::from(([127, 0, 0, 1], 1))
}

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

fn docker_exec_args(container: &str, argv: &[&str]) -> Vec<String> {
    let mut rebuilt: Vec<String> = vec!["exec".to_owned()];
    let mut rest = argv.iter();
    for token in rest.by_ref() {
        if token.starts_with('-') {
            rebuilt.push((*token).to_owned());
        } else {
            rebuilt.extend(["-w".to_owned(), "/".to_owned(), container.to_owned()]);
            rebuilt.push((*token).to_owned());
            break;
        }
    }
    rebuilt.extend(rest.map(|s| (*s).to_owned()));
    rebuilt
}

pub(crate) fn docker<I, S>(args: I) -> Result<String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let args = args
        .into_iter()
        .map(|arg| arg.as_ref().to_os_string())
        .collect::<Vec<_>>();
    let display = args
        .iter()
        .map(|arg| arg.to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join(" ");
    let output = Command::new("docker")
        .args(&args)
        .output()
        .with_context(|| format!("spawn docker {display}"))?;
    if !output.status.success() {
        bail!(
            "docker {} failed ({}): {}",
            display,
            output.status,
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_owned())
}
pub fn running_container_ids<S: AsRef<str>>(label_filters: &[S]) -> Vec<String> {
    let mut args = vec!["ps".to_owned(), "-q".to_owned()];
    for filter in label_filters {
        args.push("--filter".to_owned());
        args.push(format!("label={}", filter.as_ref()));
    }
    let Ok(out) = docker(args) else {
        return Vec::new();
    };
    out.split_whitespace().map(str::to_owned).collect()
}

pub fn container_label(id: &str, label: &str) -> Result<String> {
    let value = docker([
        "inspect",
        "-f",
        &format!("{{{{ index .Config.Labels \"{label}\" }}}}"),
        id,
    ])?;
    if value.is_empty() || value == "<no value>" {
        bail!("missing {label} label on {id}");
    }
    Ok(value)
}

pub(crate) fn container_labels(
    ids: &[String],
) -> Result<Vec<serde_json::Map<String, serde_json::Value>>> {
    if ids.is_empty() {
        return Ok(Vec::new());
    }
    let mut args = vec![
        "inspect".to_owned(),
        "-f".to_owned(),
        "{{json .Config.Labels}}".to_owned(),
    ];
    args.extend(ids.iter().cloned());
    docker(args)?
        .lines()
        .map(|line| {
            serde_json::from_str(line).with_context(|| format!("parse container labels: {line}"))
        })
        .collect()
}

fn copy_file_into(container: &str, dest_dir: &str, remote_name: &str, source: &Path) -> Result<()> {
    copy_path_into(container, dest_dir, remote_name, source, 0o755)
}

fn copy_bytes_into(
    container: &str,
    dest_dir: &str,
    remote_name: &str,
    payload: &[u8],
    mode: u32,
) -> Result<()> {
    let upload = TempUploadFile::write(payload, mode)?;
    copy_path_into(container, dest_dir, remote_name, upload.path(), mode)
}

fn path_str(path: &Path) -> Result<String> {
    path.to_str()
        .map(str::to_owned)
        .with_context(|| format!("container path is not UTF-8: {}", path.display()))
}

fn copy_path_into(
    container: &str,
    dest_dir: &str,
    remote_name: &str,
    source: &Path,
    mode: u32,
) -> Result<()> {
    validate_remote_name(remote_name)?;
    let source = source
        .to_str()
        .with_context(|| format!("host path is not UTF-8: {}", source.display()))?;
    docker([
        "cp",
        source,
        &container_copy_target(container, dest_dir, remote_name),
    ])?;
    docker([
        "exec",
        container,
        "chmod",
        &format!("{mode:o}"),
        &remote_path(dest_dir, remote_name),
    ])?;
    Ok(())
}

fn validate_remote_name(remote_name: &str) -> Result<()> {
    if remote_name.is_empty() || remote_name.contains('/') || remote_name == ".." {
        bail!("invalid remote file name {remote_name:?}");
    }
    Ok(())
}

fn container_copy_target(container: &str, dest_dir: &str, remote_name: &str) -> String {
    format!("{container}:{}", remote_path(dest_dir, remote_name))
}

fn remote_path(dest_dir: &str, remote_name: &str) -> String {
    format!("{}/{remote_name}", dest_dir.trim_end_matches('/'))
}

struct TempUploadFile {
    path: PathBuf,
}

impl TempUploadFile {
    fn write(payload: &[u8], mode: u32) -> Result<Self> {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "eos-sandbox-host-upload-{}-{nanos}",
            std::process::id()
        ));
        fs::write(&path, payload).with_context(|| format!("write {}", path.display()))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&path, fs::Permissions::from_mode(mode))
                .with_context(|| format!("chmod {}", path.display()))?;
        }
        Ok(Self { path })
    }

    fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for TempUploadFile {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
    }
}

pub(crate) fn resolve_published_addr(
    container: &str,
    container_port: u16,
) -> Result<Option<SocketAddr>> {
    let out = docker(["port", container, &format!("{container_port}/tcp")])?;
    Ok(parse_published_addr(&out))
}

fn wait_for_published_addr(container: &str, container_port: u16) -> Result<SocketAddr> {
    let deadline = Instant::now() + Duration::from_secs(15);
    loop {
        if let Ok(Some(addr)) = resolve_published_addr(container, container_port) {
            return Ok(addr);
        }
        if Instant::now() >= deadline {
            bail!("could not resolve published port {container_port} for {container}");
        }
        thread::sleep(Duration::from_millis(200));
    }
}

fn parse_published_addr(output: &str) -> Option<SocketAddr> {
    for line in output.lines() {
        let mapping = line.trim();
        let port = mapping.rsplit(':').next()?.trim();
        if let Ok(port) = port.parse::<u16>() {
            if port != 0 {
                return Some(SocketAddr::from(([127, 0, 0, 1], port)));
            }
        }
    }
    None
}
pub fn docker_available() -> bool {
    docker(["version", "--format", "{{.Server.Version}}"]).is_ok()
}

pub fn remove_labeled_containers(label: &str) -> Result<usize> {
    let out = docker(["ps", "-aq", "--filter", &format!("label={label}")])?;
    let ids: Vec<&str> = out.split_whitespace().collect();
    if ids.is_empty() {
        return Ok(0);
    }
    let mut argv = vec!["rm".to_owned(), "-f".to_owned()];
    argv.extend(ids.iter().map(|id| (*id).to_owned()));
    docker(argv)?;
    Ok(ids.len())
}

#[cfg(test)]
#[path = "../tests/unit/runtime.rs"]
mod tests;
