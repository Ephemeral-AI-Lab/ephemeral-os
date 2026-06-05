//! Host-side plugin package setup.

use std::time::Duration;

use eos_sandbox_api::{
    plugin_ensure, PluginEnsureRequest, PluginPackageEnsureRequest, SandboxApiError,
};
use eos_types::{JsonObject, SandboxId};
use serde_json::Value;

use crate::daemon_client::{map_host_error_to_api_error, posix_quote, DaemonClient};
use crate::error::SandboxHostError;
use crate::provider::{ExecOpts, ProviderAdapter};
use crate::sandbox_upload::{upload_tree_into_eos, SandboxUploadEntry, SandboxUploadRequest};

const PLUGIN_PACKAGE_UPLOAD_TIMEOUT_S: u32 = 30;
const PLUGIN_PACKAGE_UPLOAD_ROOT: &str = "/eos/scratch/uploads/plugins";

/// Ensure a plugin package is available in a running sandbox.
///
/// Performs a daemon warm ensure first; if the daemon reports that package
/// content is missing or stale, privately uploads the catalog package tree under
/// `/eos/scratch/uploads/plugins/...` and retries the daemon ensure with a staged
/// package root. Reached through the `DaemonClient` plugin-package host path.
pub(crate) async fn ensure_plugin_package(
    daemon: &DaemonClient,
    sandbox_id: &SandboxId,
    request: PluginPackageEnsureRequest,
) -> Result<JsonObject, SandboxApiError> {
    let warm = plugin_ensure(
        daemon,
        sandbox_id,
        request.clone().into_plugin_ensure_request(None),
    )
    .await?;
    if !response_needs_upload(&warm) {
        return Ok(warm);
    }

    let upload_root = upload_id_root(&request).map_err(map_host_error_to_api_error)?;
    let staged_package_root = format!("{upload_root}/package");
    let adapter = daemon
        .registry()
        .adapter(sandbox_id)
        .map_err(map_host_error_to_api_error)?;

    if let Err(err) = stage_package_tree(
        &*adapter,
        sandbox_id,
        &upload_root,
        &staged_package_root,
        &request,
    )
    .await
    {
        cleanup_upload_root(&*adapter, sandbox_id, &upload_root).await;
        return Err(map_host_error_to_api_error(err));
    }

    let cold_request: PluginEnsureRequest =
        request.into_plugin_ensure_request(Some(staged_package_root));
    let cold = plugin_ensure(daemon, sandbox_id, cold_request).await;
    if cold.is_err() {
        cleanup_upload_root(&*adapter, sandbox_id, &upload_root).await;
    }
    cold
}

fn response_needs_upload(response: &JsonObject) -> bool {
    response.get("needs_upload") == Some(&Value::Bool(true))
}

fn upload_id_root(request: &PluginPackageEnsureRequest) -> Result<String, SandboxHostError> {
    let plugin_id = safe_path_segment(&request.package.manifest.plugin_id, "plugin_id")?;
    let digest = safe_path_segment(&request.package.manifest.plugin_digest, "plugin_digest")?;
    Ok(format!(
        "{PLUGIN_PACKAGE_UPLOAD_ROOT}/{plugin_id}/{digest}/{}",
        uuid::Uuid::new_v4().simple()
    ))
}

fn safe_path_segment(value: &str, field: &str) -> Result<String, SandboxHostError> {
    if value.is_empty()
        || value == "."
        || value == ".."
        || value.contains('/')
        || value.as_bytes().contains(&0)
    {
        return Err(SandboxHostError::InvalidRequest(format!(
            "invalid plugin package {field} path segment {value:?}"
        )));
    }
    Ok(value.to_owned())
}

async fn stage_package_tree(
    adapter: &dyn ProviderAdapter,
    sandbox_id: &SandboxId,
    upload_root: &str,
    staged_package_root: &str,
    request: &PluginPackageEnsureRequest,
) -> Result<(), SandboxHostError> {
    exec_checked(
        adapter,
        sandbox_id,
        &format!(
            "rm -rf {} && mkdir -p {}",
            posix_quote(upload_root),
            posix_quote(staged_package_root)
        ),
        "plugin package staging directory setup failed",
    )
    .await?;

    let entries = request
        .package
        .package_tree
        .files
        .iter()
        .map(|file| SandboxUploadEntry::file(&file.path, file.contents.clone(), file.mode))
        .collect::<Result<Vec<_>, _>>()?;
    upload_tree_into_eos(
        adapter,
        sandbox_id,
        SandboxUploadRequest::new(staged_package_root, entries)?,
    )
    .await
}

async fn cleanup_upload_root(
    adapter: &dyn ProviderAdapter,
    sandbox_id: &SandboxId,
    upload_root: &str,
) {
    let _ = adapter
        .exec(
            sandbox_id,
            &format!("rm -rf {}", posix_quote(upload_root)),
            &exec_opts(),
        )
        .await;
}

async fn exec_checked(
    adapter: &dyn ProviderAdapter,
    sandbox_id: &SandboxId,
    command: &str,
    message: &str,
) -> Result<(), SandboxHostError> {
    let result = adapter.exec(sandbox_id, command, &exec_opts()).await?;
    if result.exit_code != 0 {
        return Err(SandboxHostError::ExecFailed {
            exit_code: result.exit_code,
            message: format!("{message}: {}", result.stdout),
        });
    }
    Ok(())
}

fn exec_opts() -> ExecOpts {
    ExecOpts {
        cwd: None,
        timeout: Some(Duration::from_secs(u64::from(
            PLUGIN_PACKAGE_UPLOAD_TIMEOUT_S,
        ))),
    }
}
