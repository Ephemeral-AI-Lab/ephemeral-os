use crate::model::WorkspaceProfile;
use crate::namespace::NamespacePlan;
use crate::profile::common::ProfileHooks;

#[derive(Debug, Default)]
pub(crate) struct HostCompatibleProfile;

impl ProfileHooks for HostCompatibleProfile {
    fn kind(&self) -> WorkspaceProfile {
        WorkspaceProfile::HostCompatible
    }

    fn namespace_plan(&self) -> NamespacePlan {
        NamespacePlan::host_workspace()
    }
}
