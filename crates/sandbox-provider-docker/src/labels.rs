//! Docker label keys that make sandbox ownership and recovery label-driven.

pub const SANDBOX_ID: &str = "eos.sandbox_id";
pub const GATEWAY_INSTANCE_ID: &str = "eos.gateway_instance_id";
pub const AUTH_TOKEN: &str = "eos.auth_token";
pub const DAEMON_PORT: &str = "eos.daemon_port";
pub const HOST_WORKSPACE_ROOT: &str = "eos.host_workspace_root";
pub const CONTAINER_WORKSPACE_ROOT: &str = "eos.container_workspace_root";
pub const SHARED_BASE_SOURCE: &str = "eos.shared_base.source";
pub const SHARED_BASE_TARGET: &str = "eos.shared_base.target";
pub const SHARED_BASE_ROOT_HASH: &str = "eos.shared_base.root_hash";
pub const SHARED_BASE_READONLY: &str = "eos.shared_base.readonly";
pub const CREATED_AT: &str = "eos.created_at";
pub const CLEANUP_POLICY: &str = "eos.cleanup_policy";
pub const RESOURCE_PROFILE: &str = "eos.resource_profile";
pub const RESOURCE_NANO_CPUS: &str = "eos.resource.nano_cpus";
pub const RESOURCE_MEMORY_HIGH_BYTES: &str = "eos.resource.memory_high_bytes";
pub const RESOURCE_MEMORY_MAX_BYTES: &str = "eos.resource.memory_max_bytes";
pub const RESOURCE_PIDS_MAX: &str = "eos.resource.pids_max";
pub const RESOURCE_WORKLOAD_MEMORY_HIGH_BYTES: &str = "eos.resource.workload_memory_high_bytes";
pub const RESOURCE_WORKLOAD_MEMORY_MAX_BYTES: &str = "eos.resource.workload_memory_max_bytes";
pub const RESOURCE_WORKLOAD_PIDS_MAX: &str = "eos.resource.workload_pids_max";
pub const RESOURCE_CONTROL_PLANE_PIDS_RESERVE: &str = "eos.resource.control_plane_pids_reserve";
pub const RESOURCE_DAEMON_RUNTIME_PROFILE: &str = "eos.resource.daemon_runtime_profile";
pub const RESOURCE_SEPARATE_WORKLOAD_CGROUP: &str = "eos.resource.separate_workload_cgroup";

pub const CLEANUP_POLICY_REMOVE_ON_DESTROY: &str = "remove-on-destroy";
