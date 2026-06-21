use std::path::PathBuf;

use crate::profile::WorkspaceModeError;
use crate::profile::WorkspaceModeHandle;

#[cfg(target_os = "linux")]
use super::cgroup_monitor::session_cgroup_path;
#[cfg(not(target_os = "linux"))]
use super::NamespaceRuntime;
#[cfg(target_os = "linux")]
use super::{setup_error, NamespaceRuntime};

impl NamespaceRuntime {
    pub(crate) fn create_cgroup(
        &self,
        handle: &WorkspaceModeHandle,
    ) -> Result<PathBuf, WorkspaceModeError> {
        #[cfg(not(target_os = "linux"))]
        {
            let _ = handle;
            Ok(PathBuf::new())
        }
        #[cfg(target_os = "linux")]
        {
            let path = session_cgroup_path(
                &PathBuf::from(crate::profile::CGROUP_ROOT),
                &crate::model::WorkspaceSessionId(handle.workspace_id.0.clone()),
            );
            std::fs::create_dir_all(&path).map_err(setup_error)?;
            Ok(path)
        }
    }

    pub(crate) fn join_holder_cgroup(
        &self,
        handle: &WorkspaceModeHandle,
    ) -> Result<(), WorkspaceModeError> {
        #[cfg(not(target_os = "linux"))]
        {
            let _ = handle;
            Ok(())
        }
        #[cfg(target_os = "linux")]
        {
            let Some(cgroup_path) = handle.cgroup_path.as_ref() else {
                return Ok(());
            };
            if handle.holder_pid <= 0 {
                return Ok(());
            }
            let procs = cgroup_path.join("cgroup.procs");
            std::fs::write(procs, format!("{}\n", handle.holder_pid)).map_err(setup_error)
        }
    }
}
