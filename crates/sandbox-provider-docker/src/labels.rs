//! Docker label keys that make sandbox ownership and recovery label-driven.

pub const SANDBOX_ID: &str = "eos.sandbox_id";
pub const GATEWAY_INSTANCE_ID: &str = "eos.gateway_instance_id";
pub const AUTH_TOKEN: &str = "eos.auth_token";
pub const DAEMON_PORT: &str = "eos.daemon_port";
pub const HOST_WORKSPACE_ROOT: &str = "eos.host_workspace_root";
pub const CONTAINER_WORKSPACE_ROOT: &str = "eos.container_workspace_root";
pub const CREATED_AT: &str = "eos.created_at";
pub const CLEANUP_POLICY: &str = "eos.cleanup_policy";

pub const CLEANUP_POLICY_REMOVE_ON_DESTROY: &str = "remove-on-destroy";
