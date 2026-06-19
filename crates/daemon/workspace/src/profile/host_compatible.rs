use crate::model::NetworkMode;
use crate::namespace::NamespacePlan;
use crate::profile::common::ProfileHooks;

#[derive(Debug, Default)]
pub(crate) struct HostCompatibleProfile;

impl ProfileHooks for HostCompatibleProfile {
    fn kind(&self) -> NetworkMode {
        NetworkMode::Host
    }

    fn namespace_plan(&self) -> NamespacePlan {
        NamespacePlan::host_workspace()
    }
}
