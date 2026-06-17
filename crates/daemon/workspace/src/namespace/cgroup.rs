use std::path::PathBuf;

use crate::network_mode::isolated_network::IsolatedNetworkError;
use crate::network_mode::isolated_network::WorkspaceModeHandle;

use super::{setup_error, NamespaceRuntime};

impl NamespaceRuntime {
    pub(crate) fn create_cgroup(
        &self,
        handle: &WorkspaceModeHandle,
    ) -> Result<PathBuf, IsolatedNetworkError> {
        if self.stub {
            return Ok(PathBuf::new());
        }
        let path = PathBuf::from(crate::network_mode::isolated_network::CGROUP_ROOT).join(format!(
            "{}{}",
            crate::network_mode::isolated_network::HANDLE_PREFIX,
            handle.workspace_id.0
        ));
        std::fs::create_dir_all(&path).map_err(setup_error)?;
        Ok(path)
    }
}
