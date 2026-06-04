//! Pinned runtime-artifact upload: arch probe, sha256 verify against the `eosd`
//! pin, idempotent remote-marker skip, Docker `put_archive` fast paths, and
//! readiness checks.
//!
//! GC-01: the Python module-tarball builder (LayerStack/OCC/overlay/plugin/audit
//! vendoring) is **dropped** — the Rust daemon is a single static `eosd` binary,
//! so only the pinned-binary upload survives. The base64-chunk fallback
//! (`chunked_upload.py`) is also dropped (Docker always has `put_archive`). The
//! `compat_python_bundle` migration tarball is intentionally not implemented.

use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use crate::daemon_client::{posix_quote, BUNDLE_REMOTE_DIR, EOSD_REMOTE_PATH, EOSD_SHA_MARKER};
use crate::error::SandboxHostError;
use crate::provider::{ExecOpts, ProviderAdapter};

/// The `eosd` artifact this host is pinned to (bumped on a coordinated release).
pub const EOSD_VERSION: &str = "0.1.0-local.20260602";

// Minisign trust-anchor public-key verification is deferred (the Python key is
// empty); the spec §6 mandates omitting a `MINISIGN_PUBLIC_KEY` const until it
// carries a value, so none is declared here.

/// The wire protocol version the pinned `eosd` speaks (from `eos_protocol`, the
/// crate the artifact itself is built from). Lockstep with
/// [`crate::daemon_client::DAEMON_PROTOCOL_VERSION`].
pub const PROTOCOL_VERSION: u32 = eos_protocol::DAEMON_PROTOCOL_VERSION as u32;
/// Sandbox path for the built-in LSP PPC service wrapper.
pub const BUILTIN_LSP_PPC_SERVICE_PATH: &str =
    "/eos/daemon/plugins/catalog/lsp/runtime/ppc_service.sh";

// AC-eos-sandbox-host-08: the host and the pinned artifact must agree on the wire
// protocol version. A drift fails the build.
const _: () = assert!(crate::daemon_client::DAEMON_PROTOCOL_VERSION == PROTOCOL_VERSION);

/// Per-arch SHA256 of the `eosd-linux-{arch}` binary, keyed by the container arch
/// token. Source of truth: the working-tree
/// `backend/src/sandbox/host/runtime_artifact/__init__.py` pin, kept in lockstep
/// with the actual `sandbox/dist/eosd-linux-{arch}` digest (the spec §6 amd64
/// value is a stale placeholder).
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
        "4c306b4ea08f0cf07cbb01bba9320b417ec1e581227014e770e878d7e8e72825",
    ),
    (
        "arm64",
        "e07a59546cecf931922386a91bf08a8ee5e1fa08747cbc45ee56462eeac4417b",
    ),
];

const BUILTIN_LSP_RUNTIME_MARKER: &str = "/eos/daemon/.builtin-lsp-runtime-sha256";
const LSP_PACKAGE_REMOTE_DIR: &str = "/eos/plugin-packages/lsp";
const LSP_NODE_ARCHIVE: &str = "node.tar.xz";
const LSP_PYRIGHT_PACKAGE: &str = "pyright.tgz";

struct RuntimeFile {
    remote_path: &'static str,
    payload: &'static [u8],
    mode: u32,
}

static BUILTIN_LSP_RUNTIME_FILES: &[RuntimeFile] = &[
    RuntimeFile {
        remote_path: "plugins/__init__.py",
        payload: include_bytes!("../../../../backend/src/plugins/__init__.py"),
        mode: 0o644,
    },
    RuntimeFile {
        remote_path: "plugins/catalog/__init__.py",
        payload: b"",
        mode: 0o644,
    },
    RuntimeFile {
        remote_path: "plugins/catalog/lsp/__init__.py",
        payload: b"",
        mode: 0o644,
    },
    RuntimeFile {
        remote_path: "plugins/catalog/lsp/runtime/__init__.py",
        payload: include_bytes!("../../../../backend/src/plugins/catalog/lsp/runtime/__init__.py"),
        mode: 0o644,
    },
    RuntimeFile {
        remote_path: "plugins/catalog/lsp/runtime/apply.py",
        payload: include_bytes!("../../../../backend/src/plugins/catalog/lsp/runtime/apply.py"),
        mode: 0o644,
    },
    RuntimeFile {
        remote_path: "plugins/catalog/lsp/runtime/lsp_jsonrpc.py",
        payload: include_bytes!(
            "../../../../backend/src/plugins/catalog/lsp/runtime/lsp_jsonrpc.py"
        ),
        mode: 0o644,
    },
    RuntimeFile {
        remote_path: "plugins/catalog/lsp/runtime/pyright_session.py",
        payload: include_bytes!(
            "../../../../backend/src/plugins/catalog/lsp/runtime/pyright_session.py"
        ),
        mode: 0o644,
    },
    RuntimeFile {
        remote_path: "plugins/catalog/lsp/runtime/server.py",
        payload: include_bytes!("../../../../backend/src/plugins/catalog/lsp/runtime/server.py"),
        mode: 0o644,
    },
    RuntimeFile {
        remote_path: "plugins/catalog/lsp/runtime/session_manager.py",
        payload: include_bytes!(
            "../../../../backend/src/plugins/catalog/lsp/runtime/session_manager.py"
        ),
        mode: 0o644,
    },
    RuntimeFile {
        remote_path: "plugins/catalog/lsp/runtime/ppc_service.sh",
        payload: LSP_PPC_SERVICE_WRAPPER.as_bytes(),
        mode: 0o755,
    },
    RuntimeFile {
        remote_path: "plugins/runtime_bridge/__init__.py",
        payload: include_bytes!("../../../../backend/src/plugins/runtime_bridge/__init__.py"),
        mode: 0o644,
    },
    RuntimeFile {
        remote_path: "plugins/runtime_bridge/op_context.py",
        payload: include_bytes!("../../../../backend/src/plugins/runtime_bridge/op_context.py"),
        mode: 0o644,
    },
    RuntimeFile {
        remote_path: "plugins/runtime_bridge/op_registry.py",
        payload: include_bytes!("../../../../backend/src/plugins/runtime_bridge/op_registry.py"),
        mode: 0o644,
    },
    RuntimeFile {
        remote_path: "plugins/runtime_bridge/ppc_service.py",
        payload: include_bytes!("../../../../backend/src/plugins/runtime_bridge/ppc_service.py"),
        mode: 0o644,
    },
    RuntimeFile {
        remote_path: "sandbox/__init__.py",
        payload: include_bytes!("../../../../backend/src/sandbox/__init__.py"),
        mode: 0o644,
    },
    RuntimeFile {
        remote_path: "sandbox/_shared/__init__.py",
        payload: include_bytes!("../../../../backend/src/sandbox/_shared/__init__.py"),
        mode: 0o644,
    },
    RuntimeFile {
        remote_path: "sandbox/_shared/models.py",
        payload: include_bytes!("../../../../backend/src/sandbox/_shared/models.py"),
        mode: 0o644,
    },
];

const LSP_PPC_SERVICE_WRAPPER: &str = r#"#!/usr/bin/env sh
set -eu

PACKAGE_DIR="${EOS_PLUGIN_PACKAGE_DIR:-/eos/plugin-packages/lsp}"
NODE_HOME="${EOS_NODE_HOME:-$PACKAGE_DIR/node}"
export PATH="$NODE_HOME/bin:$PATH"

if ! command -v pyright-langserver >/dev/null 2>&1; then
    if [ -s "$PACKAGE_DIR/node.tar.xz" ] && [ -s "$PACKAGE_DIR/pyright.tgz" ]; then
        mkdir -p "$NODE_HOME"
        if [ ! -x "$NODE_HOME/bin/node" ]; then
            tar -xJf "$PACKAGE_DIR/node.tar.xz" -C "$NODE_HOME" --strip-components=1
        fi
        export PATH="$NODE_HOME/bin:$PATH"
        npm config set prefix "$NODE_HOME"
        npm install -g --offline --cache "$PACKAGE_DIR/npm-cache" --omit=optional "$PACKAGE_DIR/pyright.tgz"
    fi
fi

if ! command -v pyright-langserver >/dev/null 2>&1; then
    echo "pyright-langserver unavailable; provide /eos/plugin-packages/lsp/node.tar.xz and pyright.tgz or use an image with pyright" >&2
    exit 36
fi

cd /eos/daemon
exec python3 -m plugins.runtime_bridge.ppc_service
"#;

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

/// Build the uncompressed single-file tar stream the Docker `put_archive` fast
/// path expects (deterministic: mtime/uid/gid 0, empty uname/gname).
pub(crate) fn tar_file_at_path(
    name: &str,
    payload: &[u8],
    mode: u32,
) -> Result<Vec<u8>, SandboxHostError> {
    let mut builder = tar::Builder::new(Vec::new());
    let mut header = tar::Header::new_gnu();
    header.set_size(payload.len() as u64);
    header.set_mtime(0);
    header.set_uid(0);
    header.set_gid(0);
    header.set_mode(mode);
    header.set_cksum();
    builder.append_data(&mut header, name.trim_matches('/'), payload)?;
    Ok(builder.into_inner()?)
}

fn sha256_hex(payload: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(payload);
    format!("{:x}", hasher.finalize())
}

fn update_digest(hasher: &mut Sha256, path: &str, payload: &[u8]) {
    hasher.update(path.as_bytes());
    hasher.update([0]);
    hasher.update((payload.len() as u64).to_be_bytes());
    hasher.update([0]);
    hasher.update(payload);
    hasher.update([0xff]);
}

async fn optional_file(path: PathBuf) -> Result<Option<Vec<u8>>, SandboxHostError> {
    match tokio::fs::read(&path).await {
        Ok(payload) => Ok(Some(payload)),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(err) => Err(err.into()),
    }
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

/// Whether the remote marker-check result indicates the pinned binary is already
/// present with a matching digest (idempotent skip).
fn marker_indicates_skip(check: &crate::provider::RawExecResult, digest: &str) -> bool {
    check.exit_code == 0 && check.stdout.trim() == digest
}

/// Upload + verify the pinned `eosd` binary into the sandbox (idempotent).
///
/// `artifact_dir` holds the `eosd-linux-{arch}` binaries (the Python
/// `<repo>/sandbox/dist`); the composition root supplies it. Fail-closed: a
/// missing artifact, hash mismatch, unsupported arch, or failed `--version`
/// verify all propagate.
pub(crate) async fn ensure_eosd_uploaded(
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

    // 7. upload via the put_archive fast path through a random staging dir.
    let staging_dir = format!("/tmp/eosd-upload-{}", uuid::Uuid::new_v4().simple());
    let staging_file = format!("{staging_dir}/eosd");
    check_exec(
        adapter,
        id,
        &format!("mkdir -p {}", posix_quote(&staging_dir)),
        30,
        "eosd staging directory setup failed",
    )
    .await?;
    let tar_stream = tar_file_at_path("eosd", &payload, 0o755)?;
    adapter.put_archive(id, &tar_stream, &staging_dir).await?;
    check_exec(
        adapter,
        id,
        &format!(
            "cat {} > {remote} && chmod 755 {remote} && rm -rf {}",
            posix_quote(&staging_file),
            posix_quote(&staging_dir)
        ),
        30,
        "eosd finalize failed",
    )
    .await?;

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

/// Upload the built-in LSP plugin runtime used by the Rust plugin facade.
///
/// This stages a trusted, minimal sandbox-Python PPC bridge plus LSP runtime
/// modules into `/eos/daemon`. It does not run catalog `setup.sh`; the wrapper
/// uses sandbox `python3` and either an image-provided `pyright-langserver` or
/// trusted offline Node/Pyright archives staged under `/eos/plugin-packages/lsp`.
pub(crate) async fn ensure_builtin_lsp_plugin_runtime_uploaded(
    adapter: &dyn ProviderAdapter,
    id: &eos_types::SandboxId,
    artifact_dir: &Path,
) -> Result<(), SandboxHostError> {
    let node_archive_path = artifact_dir
        .join("plugin-packages/lsp")
        .join(LSP_NODE_ARCHIVE);
    let pyright_package_path = artifact_dir
        .join("plugin-packages/lsp")
        .join(LSP_PYRIGHT_PACKAGE);
    let node_archive = optional_file(node_archive_path).await?;
    let pyright_package = optional_file(pyright_package_path).await?;

    let digest = builtin_lsp_runtime_digest(node_archive.as_deref(), pyright_package.as_deref());
    let marker = posix_quote(BUILTIN_LSP_RUNTIME_MARKER);
    let skip_check = adapter
        .exec(
            id,
            &format!("test -f {marker} && cat {marker}"),
            &probe_opts(15),
        )
        .await?;
    if marker_indicates_skip(&skip_check, &digest) {
        return Ok(());
    }

    check_exec(
        adapter,
        id,
        "mkdir -p /eos/daemon/plugins/catalog/lsp/runtime /eos/daemon/plugins/runtime_bridge /eos/daemon/sandbox/_shared /eos/plugin-packages/lsp",
        30,
        "builtin lsp runtime directory setup failed",
    )
    .await?;

    for file in BUILTIN_LSP_RUNTIME_FILES {
        let tar_stream = tar_file_at_path(file.remote_path, file.payload, file.mode)?;
        adapter
            .put_archive(id, &tar_stream, BUNDLE_REMOTE_DIR)
            .await?;
    }
    if let Some(payload) = node_archive {
        let tar_stream = tar_file_at_path(LSP_NODE_ARCHIVE, &payload, 0o644)?;
        adapter
            .put_archive(id, &tar_stream, LSP_PACKAGE_REMOTE_DIR)
            .await?;
    }
    if let Some(payload) = pyright_package {
        let tar_stream = tar_file_at_path(LSP_PYRIGHT_PACKAGE, &payload, 0o644)?;
        adapter
            .put_archive(id, &tar_stream, LSP_PACKAGE_REMOTE_DIR)
            .await?;
    }

    check_exec(
        adapter,
        id,
        &format!(
            "test -x {} && test -f {} && command -v python3 >/dev/null && printf %s {} > {marker}",
            posix_quote(BUILTIN_LSP_PPC_SERVICE_PATH),
            posix_quote("/eos/daemon/plugins/runtime_bridge/ppc_service.py"),
            posix_quote(&digest)
        ),
        30,
        "builtin lsp runtime upload verification failed",
    )
    .await
}

fn builtin_lsp_runtime_digest(
    node_archive: Option<&[u8]>,
    pyright_package: Option<&[u8]>,
) -> String {
    let mut hasher = Sha256::new();
    for file in BUILTIN_LSP_RUNTIME_FILES {
        update_digest(&mut hasher, file.remote_path, file.payload);
    }
    if let Some(payload) = node_archive {
        update_digest(&mut hasher, LSP_NODE_ARCHIVE, payload);
    }
    if let Some(payload) = pyright_package {
        update_digest(&mut hasher, LSP_PYRIGHT_PACKAGE, payload);
    }
    format!("{:x}", hasher.finalize())
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use std::io::Read;
    use std::sync::Arc;

    use super::*;
    use crate::provider::RawExecResult;
    use crate::registry::ProviderRegistry;
    use crate::testutil::MockAdapter;

    fn sid() -> eos_types::SandboxId {
        "sb-1".parse().unwrap()
    }

    // AC-08 lockstep is a compile-time assert above; assert the value here too.
    #[test]
    fn protocol_version_lockstep() {
        assert_eq!(
            PROTOCOL_VERSION,
            crate::daemon_client::DAEMON_PROTOCOL_VERSION
        );
        assert_eq!(PROTOCOL_VERSION, 1);
    }

    #[test]
    fn artifact_arch_maps_and_rejects() {
        assert_eq!(artifact_arch("x86_64").unwrap(), "amd64");
        assert_eq!(artifact_arch(" AMD64 ").unwrap(), "amd64");
        assert_eq!(artifact_arch("aarch64").unwrap(), "arm64");
        assert_eq!(artifact_arch("arm64").unwrap(), "arm64");
        let err = artifact_arch("riscv64").unwrap_err();
        assert!(matches!(
            err,
            SandboxHostError::UnsupportedArchitecture { machine } if machine == "riscv64"
        ));
    }

    #[test]
    fn pinned_shas_are_64_hex() {
        for (arch, sha) in EOSD_SHA256 {
            assert_eq!(sha.len(), 64, "{arch} sha length");
            assert!(sha.bytes().all(|b| b.is_ascii_hexdigit()), "{arch} sha hex");
        }
        assert!(expected_sha("amd64").is_some());
        assert!(expected_sha("riscv64").is_none());
    }

    #[test]
    fn tar_stream_is_deterministic_single_file() {
        let payload = b"#!/bin/true\n";
        let stream = tar_file_at_path("eosd", payload, 0o755).unwrap();
        let mut archive = tar::Archive::new(&stream[..]);
        let mut entries = archive.entries().unwrap();
        let mut entry = entries.next().unwrap().unwrap();
        assert_eq!(entry.path().unwrap().to_str().unwrap(), "eosd");
        assert_eq!(entry.header().mode().unwrap(), 0o755);
        assert_eq!(entry.header().mtime().unwrap(), 0);
        let mut content = Vec::new();
        entry.read_to_end(&mut content).unwrap();
        assert_eq!(content, payload);
        assert!(entries.next().is_none(), "exactly one entry");
    }

    #[test]
    fn builtin_lsp_runtime_wrapper_uses_sandbox_python() {
        assert!(
            LSP_PPC_SERVICE_WRAPPER.contains("exec python3 -m plugins.runtime_bridge.ppc_service")
        );
        assert!(!LSP_PPC_SERVICE_WRAPPER.contains("PYTHONPATH"));
        assert!(BUILTIN_LSP_RUNTIME_FILES
            .iter()
            .any(|file| file.remote_path == "plugins/runtime_bridge/ppc_service.py"));
        assert!(BUILTIN_LSP_RUNTIME_FILES
            .iter()
            .any(|file| file.remote_path == "sandbox/_shared/models.py"));
    }

    #[test]
    fn builtin_lsp_runtime_digest_tracks_optional_packages() {
        let base = builtin_lsp_runtime_digest(None, None);
        let with_node = builtin_lsp_runtime_digest(Some(b"node"), None);
        let with_both = builtin_lsp_runtime_digest(Some(b"node"), Some(b"pyright"));
        assert_ne!(base, with_node);
        assert_ne!(with_node, with_both);
    }

    #[test]
    fn marker_skip_decision() {
        let digest = "abc123";
        let hit = RawExecResult {
            exit_code: 0,
            stdout: "abc123\n".to_owned(),
            stderr: String::new(),
            success: true,
        };
        assert!(marker_indicates_skip(&hit, digest));
        let wrong = RawExecResult {
            stdout: "deadbeef".to_owned(),
            ..hit.clone()
        };
        assert!(!marker_indicates_skip(&wrong, digest));
        let absent = RawExecResult {
            exit_code: 1,
            stdout: "abc123".to_owned(),
            stderr: String::new(),
            success: false,
        };
        assert!(!marker_indicates_skip(&absent, digest));
    }

    // AC-06: a host artifact whose digest differs from the pin returns
    // ArtifactHashMismatch; a missing artifact returns ArtifactMissing.
    #[tokio::test]
    async fn upload_verifies_hash_and_missing() {
        let tmp = std::env::temp_dir().join(format!("eosd-test-{}", uuid::Uuid::new_v4().simple()));
        tokio::fs::create_dir_all(&tmp).await.unwrap();
        // amd64 arch (mock `uname -m` → x86_64), but a fake binary != pin.
        tokio::fs::write(tmp.join("eosd-linux-amd64"), b"not the real binary")
            .await
            .unwrap();
        let adapter = MockAdapter::new().with_exec(|cmd| {
            let stdout = if cmd.contains("uname -m") {
                "x86_64"
            } else {
                ""
            };
            RawExecResult {
                exit_code: 0,
                stdout: stdout.to_owned(),
                stderr: String::new(),
                success: true,
            }
        });
        let registry = ProviderRegistry::new();
        let adapter_arc: Arc<dyn ProviderAdapter> = Arc::new(adapter);
        registry.set_default(Arc::clone(&adapter_arc));

        let err = ensure_eosd_uploaded(&*adapter_arc, &sid(), &tmp)
            .await
            .unwrap_err();
        assert!(matches!(
            err,
            SandboxHostError::ArtifactHashMismatch { arch, .. } if arch == "amd64"
        ));

        // Remove the artifact → ArtifactMissing.
        tokio::fs::remove_file(tmp.join("eosd-linux-amd64"))
            .await
            .unwrap();
        let err = ensure_eosd_uploaded(&*adapter_arc, &sid(), &tmp)
            .await
            .unwrap_err();
        assert!(matches!(err, SandboxHostError::ArtifactMissing { arch } if arch == "amd64"));

        tokio::fs::remove_dir_all(&tmp).await.ok();
    }
}
