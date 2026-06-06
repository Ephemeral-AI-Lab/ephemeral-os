//! Pinned daemon bootstrap upload: arch probe, sha256 verify against the `eosd`
//! pin, idempotent remote-marker skip, Docker `put_archive` fast paths, and
//! readiness checks.
//!
//! GC-01: there is no module-tarball builder (LayerStack/OCC/overlay/plugin/audit
//! vendoring) — the daemon is a single static `eosd` binary, so only the
//! pinned-binary upload exists. There is no base64-chunk upload fallback (Docker
//! always has `put_archive`) and no migration-bundle tarball.

use std::path::Path;

use sha2::{Digest, Sha256};

use crate::daemon_client::{posix_quote, BUNDLE_REMOTE_DIR, EOSD_REMOTE_PATH, EOSD_SHA_MARKER};
use crate::error::SandboxHostError;
use crate::provider::{ExecOpts, ProviderAdapter};
use crate::sandbox_upload::upload_file_into_eos;

/// The `eosd` artifact this host is pinned to (bumped on a coordinated release).
pub const EOSD_VERSION: &str = "0.1.0-local.20260602";

// Minisign trust-anchor public-key verification is deferred; the spec §6 mandates
// omitting a `MINISIGN_PUBLIC_KEY` const until it carries a value, so none is
// declared here.

/// The wire protocol version the pinned `eosd` speaks (from `eos_protocol`, the
/// crate the artifact itself is built from). Lockstep with
/// [`crate::daemon_client::DAEMON_PROTOCOL_VERSION`].
pub const PROTOCOL_VERSION: u32 = eos_protocol::DAEMON_PROTOCOL_VERSION as u32;

// AC-eos-sandbox-host-08: the host and the pinned artifact must agree on the wire
// protocol version. A drift fails the build.
const _: () = assert!(crate::daemon_client::DAEMON_PROTOCOL_VERSION == PROTOCOL_VERSION);

/// Per-arch SHA256 of the `eosd-linux-{arch}` binary, keyed by the container arch
/// token. Keep these pins in lockstep with the actual
/// `sandbox/dist/eosd-linux-{arch}` digests.
///
/// VOLATILE: the amd64 `eosd` binary is rebuilt repeatedly during development
/// (observed churn this session: `bb066eb…`→`0bf55d43…`→`033ed149…`→`5589fff8…`→
/// `4c306b4e…`), so any hardcoded amd64 value is a best-effort snapshot that
/// races the rebuild and is intentionally NOT unit-test-pinned (a value-coupled
/// test would be permanently flaky). This crate is not yet wired into a running
/// runtime (`eos-runtime` is Phase 6), so a lagging pin has no runtime impact
/// today; Phase-7 cutover reconciles the final release pin against the stabilized
/// binary. The upload/verify LOGIC (arch map, sha-mismatch, marker-skip decision)
/// is what this crate owns and is fully unit-tested; the pin VALUE is a cutover
/// concern. arm64 is stable.
static EOSD_SHA256: &[(&str, &str)] = &[
    (
        "amd64",
        "af19704510087edfc6dde9218ab2baded18659d946c068897fce3d9655d66e30",
    ),
    (
        "arm64",
        "e07a59546cecf931922386a91bf08a8ee5e1fa08747cbc45ee56462eeac4417b",
    ),
];

fn expected_sha(arch: &str) -> Option<&'static str> {
    EOSD_SHA256
        .iter()
        .find(|(key, _)| *key == arch)
        .map(|(_, sha)| *sha)
}

/// Map a `uname -m` token to an `eosd` arch token, rejecting the unsupported.
pub(crate) fn artifact_arch(machine: &str) -> Result<&'static str, SandboxHostError> {
    match machine.trim().to_ascii_lowercase().as_str() {
        "x86_64" | "amd64" => Ok("amd64"),
        "aarch64" | "arm64" => Ok("arm64"),
        _ => Err(SandboxHostError::UnsupportedArchitecture {
            machine: machine.to_owned(),
        }),
    }
}

fn sha256_hex(payload: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(payload);
    format!("{:x}", hasher.finalize())
}

fn probe_opts(timeout_s: u32) -> ExecOpts {
    ExecOpts {
        cwd: None,
        timeout: Some(std::time::Duration::from_secs(u64::from(timeout_s))),
    }
}

async fn exec_stdout(
    adapter: &dyn ProviderAdapter,
    id: &eos_types::SandboxId,
    command: &str,
    timeout_s: u32,
) -> Result<String, SandboxHostError> {
    let result = adapter.exec(id, command, &probe_opts(timeout_s)).await?;
    if result.exit_code != 0 {
        return Err(SandboxHostError::ExecFailed {
            exit_code: result.exit_code,
            message: format!("runtime probe failed: {}", result.stdout),
        });
    }
    Ok(result.stdout.trim().to_owned())
}

async fn check_exec(
    adapter: &dyn ProviderAdapter,
    id: &eos_types::SandboxId,
    command: &str,
    timeout_s: u32,
    message: &str,
) -> Result<(), SandboxHostError> {
    let result = adapter.exec(id, command, &probe_opts(timeout_s)).await?;
    if result.exit_code != 0 {
        return Err(SandboxHostError::ExecFailed {
            exit_code: result.exit_code,
            message: format!("{message} (sandbox={id}): {}", result.stdout),
        });
    }
    Ok(())
}

async fn cleanup_staging_dir(adapter: &dyn ProviderAdapter, id: &eos_types::SandboxId, path: &str) {
    let _ = adapter
        .exec(
            id,
            &format!("rm -rf {}", posix_quote(path)),
            &probe_opts(15),
        )
        .await;
}

/// Whether the remote marker-check result indicates the pinned binary is already
/// present with a matching digest (idempotent skip).
fn marker_indicates_skip(check: &crate::provider::RawExecResult, digest: &str) -> bool {
    check.exit_code == 0 && check.stdout.trim() == digest
}

/// Upload + verify the pinned `eosd` binary into the sandbox (idempotent).
///
/// `artifact_dir` holds the `eosd-linux-{arch}` binaries; the composition root
/// supplies it. Fail-closed: a
/// missing artifact, hash mismatch, unsupported arch, or failed `--version`
/// verify all propagate.
pub(crate) async fn ensure_daemon_bootstrap(
    adapter: &dyn ProviderAdapter,
    id: &eos_types::SandboxId,
    artifact_dir: &Path,
) -> Result<(), SandboxHostError> {
    // 1. probe arch.
    let machine = exec_stdout(adapter, id, "uname -m", 15).await?;
    let arch = artifact_arch(&machine)?;

    // 2. locate the pinned binary on the host fs.
    let artifact_path = artifact_dir.join(format!("eosd-linux-{arch}"));
    if !artifact_path.exists() {
        return Err(SandboxHostError::ArtifactMissing {
            arch: arch.to_owned(),
        });
    }

    // 3. read + hash.
    let payload = tokio::fs::read(&artifact_path).await?;
    let digest = sha256_hex(&payload);

    // 4. verify against the pin (fail-closed).
    let expected = expected_sha(arch).ok_or_else(|| SandboxHostError::ArtifactMissing {
        arch: arch.to_owned(),
    })?;
    if digest != expected {
        return Err(SandboxHostError::ArtifactHashMismatch {
            arch: arch.to_owned(),
            got: digest,
            expected: expected.to_owned(),
        });
    }

    // 5. remote-marker skip check (binary present + executable + marker matches).
    let remote = posix_quote(EOSD_REMOTE_PATH);
    let marker = posix_quote(EOSD_SHA_MARKER);
    let skip_check = adapter
        .exec(
            id,
            &format!("test -x {remote} && test -f {marker} && cat {marker}"),
            &probe_opts(15),
        )
        .await?;
    if marker_indicates_skip(&skip_check, &digest) {
        return Ok(());
    }

    // 6. ensure the remote dir exists.
    check_exec(
        adapter,
        id,
        &format!("mkdir -p {}", posix_quote(BUNDLE_REMOTE_DIR)),
        30,
        "eosd runtime directory setup failed",
    )
    .await?;

    // 7. upload through the typed `/eos` fast path into a random staging dir.
    let staging_dir = format!(
        "{BUNDLE_REMOTE_DIR}/.eosd-upload-{}",
        uuid::Uuid::new_v4().simple()
    );
    let staging_file = format!("{staging_dir}/eosd");
    check_exec(
        adapter,
        id,
        &format!("mkdir -p {}", posix_quote(&staging_dir)),
        30,
        "eosd staging directory setup failed",
    )
    .await?;
    if let Err(err) = upload_file_into_eos(adapter, id, &staging_dir, "eosd", &payload, 0o755).await
    {
        cleanup_staging_dir(adapter, id, &staging_dir).await;
        return Err(err);
    }
    if let Err(err) = check_exec(
        adapter,
        id,
        &format!(
            "mv -f {} {remote} && chmod 755 {remote} && rm -rf {}",
            posix_quote(&staging_file),
            posix_quote(&staging_dir)
        ),
        30,
        "eosd finalize failed",
    )
    .await
    {
        cleanup_staging_dir(adapter, id, &staging_dir).await;
        return Err(err);
    }

    // 8. write marker + readiness verify (marker written before the version
    // check, in one `&&` chain — ported verbatim, not reordered).
    check_exec(
        adapter,
        id,
        &format!(
            "printf %s {} > {marker} && {remote} --version >/dev/null",
            posix_quote(&digest)
        ),
        30,
        "eosd upload verification failed",
    )
    .await?;
    Ok(())
}

#[cfg(test)]
#[path = "../tests/bootstrap_artifact/mod.rs"]
mod tests;
