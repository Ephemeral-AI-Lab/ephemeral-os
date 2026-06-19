use std::collections::HashMap;
use std::time::Instant;

use crate::isolated_network_setup::IsolatedNetwork;
use crate::namespace::{NamespacePlan, NamespaceRuntime};
use crate::profile::common::{record_phase_ms, ProfileHooks};
use crate::profile::manager::IsolatedNetworkError;
use crate::profile::resource_control;
use crate::profile::WorkspaceModeHandle;

pub(crate) struct IsolatedProfile<'a> {
    network: &'a mut IsolatedNetwork,
    fallback_dns: &'a str,
    setup_timeout_s: f64,
}

impl<'a> IsolatedProfile<'a> {
    pub(crate) fn new(
        network: &'a mut IsolatedNetwork,
        fallback_dns: &'a str,
        setup_timeout_s: f64,
    ) -> Self {
        Self {
            network,
            fallback_dns,
            setup_timeout_s,
        }
    }
}

impl ProfileHooks for IsolatedProfile<'_> {
    fn namespace_plan(&self) -> NamespacePlan {
        NamespacePlan::isolated_network()
    }

    fn setup_after_namespace(
        &mut self,
        _runtime: &NamespaceRuntime,
        handle: &mut WorkspaceModeHandle,
        phases_ms: &mut HashMap<String, f64>,
    ) -> Result<(), IsolatedNetworkError> {
        let phase_start = Instant::now();
        self.network.initialize()?;
        handle.veth = Some(
            self.network
                .install_veth(&handle.workspace_id.0, handle.holder_pid)?,
        );
        record_phase_ms(phases_ms, "install_veth", phase_start);
        Ok(())
    }

    fn setup_after_mount(
        &mut self,
        runtime: &NamespaceRuntime,
        handle: &mut WorkspaceModeHandle,
        phases_ms: &mut HashMap<String, f64>,
    ) -> Result<(), IsolatedNetworkError> {
        let phase_start = Instant::now();
        handle.dns_configuration =
            runtime.configure_dns(handle, self.fallback_dns, self.setup_timeout_s)?;
        record_phase_ms(phases_ms, "configure_dns", phase_start);
        runtime.signal_net_ready(handle, self.setup_timeout_s)?;
        resource_control::create_cgroup(runtime, handle, phases_ms)
    }

    fn teardown_environment(
        &mut self,
        _runtime: &NamespaceRuntime,
        handle: &WorkspaceModeHandle,
        phases_ms: &mut HashMap<String, f64>,
    ) {
        let phase_start = Instant::now();
        if let Some(veth) = handle.veth.as_ref() {
            self.network.teardown_veth(veth);
        }
        record_phase_ms(phases_ms, "teardown_veth", phase_start);
        let _ = resource_control::remove_cgroup(handle, phases_ms);
    }
}
