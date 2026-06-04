//! `DaemonContainer` — one Docker container running one `eosd`, driven entirely
//! through the Docker CLI plus the Engine archive API for binary upload.
//!
//! Container lifecycle (create / upload / spawn / teardown) is *infrastructure*,
//! not a sandbox operation, so it is allowed to use `docker` directly. The
//! operations *under test* still go exclusively through [`ProtocolClient`] over
//! the wire (D1/D4). Container-filesystem peeking is never used as a verification
//! oracle.

use std::fmt::Write as FmtWrite;
use std::fs;
use std::io::{Read, Write as IoWrite};
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use eos_protocol::ops;
use serde_json::json;

use crate::client::{is_success, ProtocolClient};
use crate::config::{Config, NodeMode};
use crate::unique_suffix;

/// Daemon runtime dir under the EphemeralOS-owned root.
const DAEMON_DIR: &str = "/eos/runtime/daemon";
/// Remote daemon executable path. Keep this aligned with host runtime paths.
const EOSD_REMOTE_PATH: &str = "/eos/runtime/daemon/eosd";
/// Root under which the pool mints per-test `layer_stack_root`s.
pub const E2E_ROOT_DIR: &str = "/eos/state/e2e";
/// A non-kept container self-removes after this long (`--rm` + `timeout`), so an
/// aborted run cannot strand privileged containers even if teardown never runs.
const CONTAINER_TTL_SECONDS: u64 = 1800;
const POOL_LABEL: &str = "eos.e2e.pool";
const AUTH_LABEL: &str = "eos.e2e.auth";

/// A live daemon container.
#[derive(Debug)]
pub struct DaemonContainer {
    name: String,
    client: ProtocolClient,
    keep: bool,
}

impl DaemonContainer {
    /// Create a container, upload `eosd`, spawn the daemon (TCP + auth), and
    /// block until it answers a heartbeat.
    ///
    /// # Errors
    /// Returns an error if any docker step fails or the daemon never becomes ready.
    pub fn start(config: &Config) -> Result<Self> {
        let name = format!("eos-e2e-{}", unique_suffix());
        let token = format!("tok-{}", unique_suffix());
        let keep = config.keep_container && config.mode != NodeMode::PerTest;

        let mut run = vec![
            "run".to_owned(),
            "-d".to_owned(),
            "--name".to_owned(),
            name.clone(),
            "--label".to_owned(),
            format!("{POOL_LABEL}={}", config.image),
            "--label".to_owned(),
            format!("{AUTH_LABEL}={token}"),
        ];
        if !keep {
            run.push("--rm".to_owned());
        }
        // The isolated-workspace tier creates a per-workspace cgroup under
        // /sys/fs/cgroup, which Docker mounts read-only under plain --cap-add
        // (EROFS, e.g. on Docker Desktop). --privileged makes cgroup2 writable so
        // the real ns-holder/veth/cgroup path runs. The harness is test-only and
        // already requires SYS_ADMIN/NET_ADMIN + unconfined seccomp/apparmor, so
        // this is an acceptable superset; the explicit caps below remain for
        // documentation and hosts where privileged is unavailable.
        run.push("--privileged".to_owned());
        if let Some(platform) = &config.platform {
            run.push("--platform".to_owned());
            run.push(platform.clone());
        }
        for cap in &config.cap_add {
            run.push("--cap-add".to_owned());
            run.push(cap.clone());
        }
        for opt in &config.security_opt {
            run.push("--security-opt".to_owned());
            run.push(opt.clone());
        }
        // Enable the isolated-workspace feature for the daemon (the
        // `docker exec`-spawned eosd inherits the container env). Harmless for
        // non-isolated tiers. The upperdir cap is lowered and the MemAvailable
        // fraction raised so the host-RAM admission gate fits inside a
        // memory-modest Docker VM (the default 1 GiB reservation would be
        // refused); the real namespace/veth/cgroup path is otherwise unchanged.
        let isolated_upperdir = format!(
            "EOS_ISOLATED_WORKSPACE_UPPERDIR_BYTES={}",
            config.isolated_upperdir_bytes
        );
        let workspace_root = format!("EOS_WORKSPACE_ROOT={}", config.workspace_root);
        for env_kv in [
            "EOS_ISOLATED_WORKSPACE_ENABLED=true",
            isolated_upperdir.as_str(),
            "EOS_ISOLATED_WORKSPACE_MEMAVAIL_FRACTION=0.9",
            workspace_root.as_str(),
        ] {
            run.push("-e".to_owned());
            run.push(env_kv.to_owned());
        }
        for tmpfs in &config.tmpfs {
            run.push("--tmpfs".to_owned());
            run.push(tmpfs.clone());
        }
        run.push("--init".to_owned());
        run.push("-p".to_owned());
        run.push(format!("127.0.0.1::{}", config.tcp_port));
        run.push(config.image.clone());
        // Keep the container alive but self-terminating: `timeout` bounds the
        // lifetime so a leaked (`--rm`) container is reclaimed automatically.
        if keep {
            run.extend(["sleep".to_owned(), "infinity".to_owned()]);
        } else {
            run.extend([
                "timeout".to_owned(),
                CONTAINER_TTL_SECONDS.to_string(),
                "sleep".to_owned(),
                "infinity".to_owned(),
            ]);
        }

        docker(&run).with_context(|| format!("docker run for {name}"))?;

        // From here, any failure must still tear the container down.
        let mut container = Self {
            name: name.clone(),
            // Placeholder client; replaced once the port is resolved.
            client: ProtocolClient::new(
                "127.0.0.1:1".parse().expect("valid placeholder addr"),
                Some(token.clone()),
                config.request_timeout,
            ),
            keep,
        };
        match container.bringup(config, &token) {
            Ok(client) => {
                container.client = client;
                Ok(container)
            }
            Err(err) => {
                let log = container.daemon_log().unwrap_or_default();
                drop(container);
                Err(err.context(format!("daemon bringup failed; log tail:\n{log}")))
            }
        }
    }

    /// Adopt already-running warm e2e containers for this image.
    ///
    /// Containers are accepted only when their auth label is present, their
    /// published daemon port resolves, and the daemon answers heartbeat.
    pub fn adopt_healthy(config: &Config) -> Vec<Self> {
        let out = Command::new("docker")
            .args([
                "ps",
                "-q",
                "--filter",
                &format!("label={POOL_LABEL}={}", config.image),
            ])
            .output();
        let Ok(out) = out else {
            return Vec::new();
        };
        if !out.status.success() {
            return Vec::new();
        }
        std::str::from_utf8(&out.stdout)
            .unwrap_or("")
            .split_whitespace()
            .filter_map(|id| Self::adopt_one(id, config).ok())
            .collect()
    }

    fn adopt_one(id: &str, config: &Config) -> Result<Self> {
        let token = docker(&[
            "inspect".to_owned(),
            "-f".to_owned(),
            format!("{{{{ index .Config.Labels \"{AUTH_LABEL}\" }}}}"),
            id.to_owned(),
        ])?;
        if token.is_empty() || token == "<no value>" {
            bail!("missing {AUTH_LABEL} label on {id}");
        }
        let mut container = Self {
            name: id.to_owned(),
            client: ProtocolClient::new(
                "127.0.0.1:1".parse().expect("valid placeholder addr"),
                Some(token.clone()),
                config.request_timeout,
            ),
            keep: true,
        };
        let addr = container.resolve_addr(config.tcp_port)?;
        let client = ProtocolClient::new(addr, Some(token), config.request_timeout);
        container.await_ready(&client, config.ready_timeout)?;
        container.client = client;
        Ok(container)
    }

    fn bringup(&self, config: &Config, token: &str) -> Result<ProtocolClient> {
        self.exec(&["mkdir", "-p", DAEMON_DIR, E2E_ROOT_DIR])
            .context("mkdir daemon dirs")?;
        self.exec(&[
            "sh",
            "-lc",
            "mount -o remount,rw /sys/fs/cgroup 2>/dev/null || true; test -w /sys/fs/cgroup",
        ])
        .context("make cgroup v2 writable for isolated-workspace tests")?;
        put_archive_file(
            &self.name,
            DAEMON_DIR,
            "eosd",
            &config.eosd_path,
            config.request_timeout,
        )
        .with_context(|| {
            format!(
                "Docker put_archive eosd ({}) into {DAEMON_DIR}",
                config.eosd_path.display()
            )
        })?;

        // Detached foreground daemon (no --spawn needed: `exec -d` backgrounds it).
        self.exec(&[
            "-d",
            EOSD_REMOTE_PATH,
            "daemon",
            "--socket",
            &format!("{DAEMON_DIR}/runtime.sock"),
            "--pid-file",
            &format!("{DAEMON_DIR}/runtime.pid"),
            "--log-file",
            &format!("{DAEMON_DIR}/runtime.log"),
            "--tcp-host",
            "0.0.0.0",
            "--tcp-port",
            &config.tcp_port.to_string(),
            "--auth-token",
            token,
        ])
        .context("spawn eosd daemon")?;

        let addr = self.resolve_addr(config.tcp_port)?;
        let client = ProtocolClient::new(addr, Some(token.to_owned()), config.request_timeout);
        self.await_ready(&client, config.ready_timeout)?;
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

    fn await_ready(&self, client: &ProtocolClient, budget: Duration) -> Result<()> {
        let deadline = Instant::now() + budget;
        let mut delay = Duration::from_millis(150);
        loop {
            let observed = match client.request(ops::API_V1_HEARTBEAT, "ready-probe", &json!({})) {
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

    /// Best-effort tail of the daemon log for diagnostics (not an oracle).
    fn daemon_log(&self) -> Option<String> {
        docker(&[
            "exec".to_owned(),
            self.name.clone(),
            "tail".to_owned(),
            "-n".to_owned(),
            "40".to_owned(),
            format!("{DAEMON_DIR}/runtime.log"),
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

/// Run `docker <args...>`, returning trimmed stdout. Errors include stderr.
fn docker(args: &[String]) -> Result<String> {
    docker_str(&args.iter().map(String::as_str).collect::<Vec<_>>())
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

fn docker_str(args: &[&str]) -> Result<String> {
    let output = Command::new("docker")
        .args(args)
        .output()
        .with_context(|| format!("spawn docker {}", args.join(" ")))?;
    if !output.status.success() {
        bail!(
            "docker {} failed ({}): {}",
            args.join(" "),
            output.status,
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

fn put_archive_file(
    container: &str,
    dest_dir: &str,
    remote_name: &str,
    source: &Path,
    timeout: Duration,
) -> Result<()> {
    let payload = fs::read(source).with_context(|| format!("read eosd {}", source.display()))?;
    let tar_stream = tar_single_file(remote_name, &payload, 0o755)?;
    docker_put_archive(container, dest_dir, &tar_stream, timeout)
}

#[cfg(unix)]
fn docker_put_archive(
    container: &str,
    dest_dir: &str,
    tar_stream: &[u8],
    timeout: Duration,
) -> Result<()> {
    use std::os::unix::net::UnixStream;

    let socket = docker_socket_path()?;
    let mut stream = UnixStream::connect(&socket)
        .with_context(|| format!("connect Docker socket {}", socket.display()))?;
    stream
        .set_read_timeout(Some(timeout))
        .context("set Docker socket read timeout")?;
    stream
        .set_write_timeout(Some(timeout))
        .context("set Docker socket write timeout")?;

    let api_version = docker_api_version();
    let request_path = format!(
        "/v{}/containers/{}/archive?path={}",
        api_version.trim_start_matches('v'),
        percent_encode(container),
        percent_encode(dest_dir)
    );
    let request = format!(
        "PUT {request_path} HTTP/1.1\r\n\
         Host: docker\r\n\
         User-Agent: eos-e2e-test\r\n\
         Content-Type: application/x-tar\r\n\
         Content-Length: {}\r\n\
         Connection: close\r\n\
         \r\n",
        tar_stream.len()
    );
    stream
        .write_all(request.as_bytes())
        .context("write Docker put_archive request headers")?;
    stream
        .write_all(tar_stream)
        .context("write Docker put_archive tar stream")?;
    stream.flush().context("flush Docker put_archive request")?;

    let mut response = Vec::new();
    stream
        .read_to_end(&mut response)
        .context("read Docker put_archive response")?;
    let response_text = String::from_utf8_lossy(&response);
    let status = docker_http_status(&response_text)?;
    if !(200..300).contains(&status) {
        bail!("Docker put_archive failed with HTTP {status}: {response_text}");
    }
    Ok(())
}

#[cfg(not(unix))]
fn docker_put_archive(
    _container: &str,
    _dest_dir: &str,
    _tar_stream: &[u8],
    _timeout: Duration,
) -> Result<()> {
    bail!("Docker put_archive over a Unix socket is only supported on Unix hosts")
}

#[cfg(unix)]
fn docker_socket_path() -> Result<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(host) = std::env::var("DOCKER_HOST") {
        if let Some(path) = docker_unix_socket_from_host(&host) {
            candidates.push(path);
        }
    }
    if let Ok(host) = docker_str(&[
        "context",
        "inspect",
        "--format",
        "{{.Endpoints.docker.Host}}",
    ]) {
        if let Some(path) = docker_unix_socket_from_host(&host) {
            candidates.push(path);
        }
    }
    if let Some(home) = std::env::var_os("HOME") {
        candidates.push(PathBuf::from(home).join(".docker/run/docker.sock"));
    }
    candidates.push(PathBuf::from("/var/run/docker.sock"));

    candidates
        .into_iter()
        .find(|path| path.exists())
        .ok_or_else(|| anyhow::anyhow!("could not locate Docker Unix socket for put_archive"))
}

fn docker_unix_socket_from_host(host: &str) -> Option<PathBuf> {
    host.trim()
        .strip_prefix("unix://")
        .filter(|path| !path.is_empty())
        .map(PathBuf::from)
}

fn docker_api_version() -> String {
    docker_str(&["version", "--format", "{{.Server.APIVersion}}"])
        .ok()
        .filter(|version| !version.is_empty())
        .unwrap_or_else(|| "1.41".to_owned())
}

fn docker_http_status(response: &str) -> Result<u16> {
    let status_line = response
        .lines()
        .next()
        .context("Docker put_archive response missing status line")?;
    let status = status_line
        .split_whitespace()
        .nth(1)
        .context("Docker put_archive response missing status code")?;
    status
        .parse::<u16>()
        .with_context(|| format!("parse Docker HTTP status from {status_line:?}"))
}

fn percent_encode(value: &str) -> String {
    let mut encoded = String::with_capacity(value.len());
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~') {
            encoded.push(char::from(byte));
        } else {
            let _ = write!(&mut encoded, "%{byte:02X}");
        }
    }
    encoded
}

fn tar_single_file(name: &str, payload: &[u8], mode: u32) -> Result<Vec<u8>> {
    if name.is_empty() || name.starts_with('/') || name.split('/').any(|part| part == "..") {
        bail!("invalid tar entry name {name:?}");
    }
    let name_bytes = name.as_bytes();
    if name_bytes.len() > 100 {
        bail!("tar entry name too long: {name}");
    }

    let mut header = [0_u8; 512];
    header[..name_bytes.len()].copy_from_slice(name_bytes);
    write_octal(&mut header[100..108], u64::from(mode))?;
    write_octal(&mut header[108..116], 0)?;
    write_octal(&mut header[116..124], 0)?;
    write_octal(&mut header[124..136], payload.len() as u64)?;
    write_octal(&mut header[136..148], 0)?;
    header[148..156].fill(b' ');
    header[156] = b'0';
    header[257..263].copy_from_slice(b"ustar\0");
    header[263..265].copy_from_slice(b"00");
    let checksum = header.iter().map(|byte| u32::from(*byte)).sum::<u32>();
    write_checksum(&mut header[148..156], checksum)?;

    let mut archive = Vec::with_capacity(512 + payload.len() + 1536);
    archive.extend_from_slice(&header);
    archive.extend_from_slice(payload);
    let padding = (512 - (payload.len() % 512)) % 512;
    archive.resize(archive.len() + padding, 0);
    archive.resize(archive.len() + 1024, 0);
    Ok(archive)
}

fn write_octal(field: &mut [u8], value: u64) -> Result<()> {
    let digits = field
        .len()
        .checked_sub(1)
        .context("tar octal field too short")?;
    let encoded = format!("{value:0width$o}", width = digits);
    if encoded.len() > digits {
        bail!(
            "tar octal value {value} does not fit in {} bytes",
            field.len()
        );
    }
    field[..digits].copy_from_slice(encoded.as_bytes());
    field[digits] = 0;
    Ok(())
}

fn write_checksum(field: &mut [u8], value: u32) -> Result<()> {
    if field.len() != 8 {
        bail!("tar checksum field must be 8 bytes");
    }
    let encoded = format!("{value:06o}");
    if encoded.len() > 6 {
        bail!("tar checksum {value} does not fit");
    }
    field[..6].copy_from_slice(encoded.as_bytes());
    field[6] = 0;
    field[7] = b' ';
    Ok(())
}

/// Parse `docker port` output (`0.0.0.0:54321` / `127.0.0.1:54321`, possibly
/// multiple lines) into a loopback `SocketAddr`.
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

/// Whether a usable `docker` CLI is present (for the env guard).
#[must_use]
pub fn docker_available() -> bool {
    Command::new("docker")
        .args(["version", "--format", "{{.Server.Version}}"])
        .output()
        .map(|out| out.status.success())
        .unwrap_or(false)
}

/// Remove all `eos-e2e-*` containers left by prior harness runs.
///
/// # Errors
/// Returns an error if Docker is reachable but listing or removing containers
/// fails.
pub fn reap_e2e_containers() -> Result<usize> {
    let out = Command::new("docker")
        .args(["ps", "-aq", "--filter", &format!("label={POOL_LABEL}")])
        .output()
        .context("list eos-e2e containers")?;
    if !out.status.success() {
        bail!(
            "docker ps for eos-e2e containers failed ({}): {}",
            out.status,
            String::from_utf8_lossy(&out.stderr).trim()
        );
    }
    let ids: Vec<&str> = std::str::from_utf8(&out.stdout)
        .unwrap_or("")
        .split_whitespace()
        .collect();
    if ids.is_empty() {
        return Ok(0);
    }
    let mut argv = vec!["rm", "-f"];
    argv.extend(ids.iter().copied());
    let output = Command::new("docker")
        .args(&argv)
        .output()
        .context("remove eos-e2e containers")?;
    if !output.status.success() {
        bail!(
            "docker {} failed ({}): {}",
            argv.join(" "),
            output.status,
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    Ok(ids.len())
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use super::{
        docker_exec_args, docker_http_status, docker_unix_socket_from_host, percent_encode,
        tar_single_file,
    };

    #[test]
    fn tar_single_file_builds_executable_ustar_stream() {
        let tar = tar_single_file("eosd", b"payload", 0o755).expect("tar stream");
        assert_eq!(&tar[0..4], b"eosd");
        assert_eq!(&tar[100..108], b"0000755\0");
        assert_eq!(&tar[124..136], b"00000000007\0");
        assert_eq!(tar[156], b'0');
        assert_eq!(&tar[257..263], b"ustar\0");
        assert_eq!(tar.len() % 512, 0);
    }

    #[test]
    fn docker_helpers_parse_http_and_unix_host() {
        assert_eq!(
            docker_http_status("HTTP/1.1 200 OK\r\n\r\n").expect("status"),
            200
        );
        assert_eq!(
            percent_encode("/eos/runtime/daemon"),
            "%2Feos%2Fruntime%2Fdaemon"
        );
        assert_eq!(
            docker_unix_socket_from_host("unix:///var/run/docker.sock").expect("socket"),
            PathBuf::from("/var/run/docker.sock")
        );
    }

    #[test]
    fn docker_exec_args_runs_from_root_after_leading_flags() {
        assert_eq!(
            docker_exec_args("box", &["mkdir", "-p", "/testbed"]),
            vec!["exec", "-w", "/", "box", "mkdir", "-p", "/testbed"]
        );
        assert_eq!(
            docker_exec_args("box", &["-d", "/eos/runtime/daemon/eosd", "daemon"]),
            vec![
                "exec",
                "-d",
                "-w",
                "/",
                "box",
                "/eos/runtime/daemon/eosd",
                "daemon"
            ]
        );
    }
}
