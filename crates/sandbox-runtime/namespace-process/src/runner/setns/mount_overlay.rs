use std::path::PathBuf;

use sandbox_runtime_overlay::OverlayHandle;

use crate::runner::protocol::NamespaceRunnerRequest;
use crate::runner::RunnerError;

/// Mount the overlay inside an existing workspace mount namespace.
pub(crate) fn setns_overlay_mount(
    request: &NamespaceRunnerRequest,
    hidden_paths: &[PathBuf],
) -> Result<(), RunnerError> {
    super::namespaces::setns_user_mnt(request, "setns overlay mount")?;
    let upperdir = request.upperdir.as_ref().ok_or_else(|| {
        RunnerError::InvalidRequest("setns overlay mount requires upperdir".to_owned())
    })?;
    let workdir = request.workdir.as_ref().ok_or_else(|| {
        RunnerError::InvalidRequest("setns overlay mount requires workdir".to_owned())
    })?;
    let handle = OverlayHandle {
        layer_paths: if request.layer_paths.is_empty() {
            return Err(RunnerError::InvalidRequest(
                "setns overlay mount requires layer_paths".to_owned(),
            ));
        } else {
            request.layer_paths.clone()
        },
        upperdir: upperdir.clone(),
        workdir: workdir.clone(),
    };
    let guard = sandbox_runtime_overlay::mount_overlay(&request.workspace_root, &handle)?;
    crate::runner::mask_model_shell_paths(hidden_paths)?;
    // The setns mount helper is a one-shot process. The mounted overlay must
    // outlive this helper and remain pinned by the target mount namespace until
    // isolated teardown, so the unmount-on-drop guard is deliberately leaked.
    std::mem::forget(guard);
    Ok(())
}
