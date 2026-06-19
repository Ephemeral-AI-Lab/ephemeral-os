use crate::namespace::NamespacePlan;
use crate::profile::common::ProfileHooks;

pub use crate::profile::host_workspace::{
    HostNamespaceWorkspaceRequest, HostWorkspace, HostWorkspaceError, WorkspaceNamespaceFds,
};

#[derive(Debug, Default)]
pub(crate) struct HostCompatibleProfile;

impl ProfileHooks for HostCompatibleProfile {
    fn namespace_plan(&self) -> NamespacePlan {
        NamespacePlan::host_workspace()
    }
}
