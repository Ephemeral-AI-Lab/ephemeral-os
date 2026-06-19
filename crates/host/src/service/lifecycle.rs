use std::fs;
use std::path::Path;
use std::sync::Arc;
use std::time::Instant;

use anyhow::{bail, Context, Result};
use protocol::HostGatewayErrorKind;
use serde_json::{json, Value};

use crate::container::{docker, ContainerLifetime, ContainerSpec, DaemonContainer};
use crate::daemon_wire::{
    response_is_accepted, ProtocolClient, DEFAULT_LAYER_STACK_ROOT, READY_OP,
};
use crate::service::registry::{
    SandboxRecord, SandboxRegistry, CREATED_BY_LABEL, SANDBOX_ID_LABEL, TCP_PORT_LABEL,
};
use crate::trace_store::{RequestStartInput, TraceStore};

use super::args::validate_container_name;
use super::response::{host_error_response, host_ok_response};
use super::utils::{path_str, random_hex};
use super::{
    workspace_root_from_args, ForwardTraceContext, ManagedSandboxStart, SandboxHost, SandboxStatus,
    HOST_SANDBOX_ACQUIRE, HOST_SANDBOX_RELEASE, SANDBOX_OVERLAY_ROOT, SANDBOX_SCRATCH_TMPFS,
};

impl SandboxHost {
    pub fn open(config: super::HostConfig) -> Result<Self> {
        let config_yaml = fs::read_to_string(&config.config_yaml_path).with_context(|| {
            format!(
                "read daemon config document {}",
                config.config_yaml_path.display()
            )
        })?;
        let registry = Arc::new(SandboxRegistry::open(config.state_dir.clone())?);
        registry.rebuild_from_docker();
        let trace_store = Arc::new(TraceStore::open(&config.state_dir)?);
        Ok(Self {
            config,
            config_yaml,
            registry,
            trace_store,
        })
    }

    pub fn acquire(&self) -> Result<String> {
        let trace = ForwardTraceContext::new("host-acquire");
        self.acquire_with_trace(&trace, &json!({}))
    }

    pub fn acquire_with_trace(&self, trace: &ForwardTraceContext, args: &Value) -> Result<String> {
        let sandbox_id = format!("sb-{}", random_hex(16)?);
        let (image, platform) = self.resolve_image_profile(args)?;
        let workspace_root = workspace_root_from_args(args)?;
        self.start_managed_sandbox(
            HOST_SANDBOX_ACQUIRE,
            trace,
            args,
            ManagedSandboxStart {
                sandbox_id,
                image,
                platform,
                workspace_root,
                response_op: HOST_SANDBOX_ACQUIRE,
            },
        )
    }

    pub(crate) fn start_managed_sandbox(
        &self,
        op: &'static str,
        trace: &ForwardTraceContext,
        args: &Value,
        start: ManagedSandboxStart,
    ) -> Result<String> {
        let ManagedSandboxStart {
            sandbox_id,
            image,
            platform,
            workspace_root,
            response_op,
        } = start;
        validate_container_name(&sandbox_id)?;
        let op_started = Instant::now();
        self.trace_store.prepare_forward(RequestStartInput {
            sandbox_id: &sandbox_id,
            trace_id: trace.trace_id.clone(),
            request_id: trace.request_id.clone(),
            op,
            caller_id: args.get("caller_id").and_then(Value::as_str),
            mutates_state: true,
            args: args.clone(),
        })?;
        self.record_host_gateway_events(&sandbox_id, trace);
        let token = random_hex(32)?;
        let forward_token = random_hex(32)?;
        let container = ContainerSpec {
            name: sandbox_id.clone(),
            image: image.clone(),
            platform: platform.clone(),
            privileged: self.config.docker_privileged,
            cap_add: Vec::new(),
            security_opt: Vec::new(),
            tmpfs: vec![SANDBOX_SCRATCH_TMPFS.to_owned()],
            labels: vec![
                (SANDBOX_ID_LABEL.to_owned(), sandbox_id.clone()),
                (TCP_PORT_LABEL.to_owned(), self.config.tcp_port.to_string()),
                (CREATED_BY_LABEL.to_owned(), self.config.created_by.clone()),
            ],
            lifetime: ContainerLifetime::Keep,
        };
        let mut daemon = self.config.daemon_spec(self.config.tcp_port);
        daemon.config_yaml = self.config_yaml.clone();
        let record = SandboxRecord::new_with_forward_token(
            sandbox_id.clone(),
            sandbox_id.clone(),
            token.clone(),
            forward_token.clone(),
            self.config.tcp_port,
            self.config.created_by.clone(),
            None,
        );
        let record = self.registry.insert(record)?;
        self.record_host_lifecycle_event(
            &sandbox_id,
            trace,
            "container_start_started",
            json!({"image": image, "platform": platform, "tcp_port": self.config.tcp_port}),
        );
        let started_container = match DaemonContainer::start_with_forward_token(
            &container,
            &daemon,
            token.clone(),
            forward_token.clone(),
        ) {
            Ok(started) => started,
            Err(err) => {
                self.registry.remove(&sandbox_id);
                let _ = docker(["rm", "-f", sandbox_id.as_str()]);
                let response = host_error_response(
                    response_op,
                    trace,
                    HostGatewayErrorKind::SandboxUnavailable.as_str(),
                    &format!("container start failed: {err:#}"),
                );
                let _ =
                    self.record_host_response_or_missing(&sandbox_id, trace, &response, op_started);
                return Err(err);
            }
        };
        self.record_host_lifecycle_event(
            &sandbox_id,
            trace,
            "container_start_finished",
            json!({"container": sandbox_id.clone(), "endpoint": started_container.client().addr().to_string()}),
        );
        record.cache_endpoint(started_container.client().addr());
        if let Err(err) = self.setup_managed_sandbox(
            &sandbox_id,
            started_container.client(),
            trace,
            &workspace_root,
        ) {
            self.registry.remove(&sandbox_id);
            let _ = docker(["rm", "-f", sandbox_id.as_str()]);
            let response = host_error_response(
                response_op,
                trace,
                HostGatewayErrorKind::SandboxUnavailable.as_str(),
                &format!("sandbox setup failed: {err:#}"),
            );
            let _ = self.record_host_response_or_missing(&sandbox_id, trace, &response, op_started);
            return Err(err);
        }
        let response = host_ok_response(
            response_op,
            trace,
            json!({"sandbox_id": sandbox_id.clone()}),
        );
        self.record_host_response_or_missing(&sandbox_id, trace, &response, op_started)?;
        Ok(sandbox_id)
    }

    fn setup_managed_sandbox(
        &self,
        sandbox_id: &str,
        client: &ProtocolClient,
        trace: &ForwardTraceContext,
        workspace_root: &Path,
    ) -> Result<()> {
        let workspace_root = path_str(workspace_root, "host.workspace_root")?;
        self.record_host_lifecycle_event(
            sandbox_id,
            trace,
            "sandbox_setup_started",
            json!({
                "workspace_root": workspace_root,
                "layer_stack_root": DEFAULT_LAYER_STACK_ROOT,
                "overlay_root": SANDBOX_OVERLAY_ROOT,
            }),
        );
        docker([
            "exec",
            "-w",
            "/",
            sandbox_id,
            "mkdir",
            "-p",
            workspace_root,
            SANDBOX_OVERLAY_ROOT,
        ])
        .with_context(|| format!("mkdir sandbox workspace {workspace_root}"))?;
        self.record_host_lifecycle_event(
            sandbox_id,
            trace,
            "sandbox_setup_dirs_created",
            json!({
                "workspace_root": workspace_root,
                "overlay_root": SANDBOX_OVERLAY_ROOT,
            }),
        );

        self.record_host_lifecycle_event(
            sandbox_id,
            trace,
            "sandbox_setup_base_skipped",
            json!({
                "reason": "workspace base op removed",
            }),
        );
        let _ = client;
        Ok(())
    }

    pub fn release(&self, sandbox_id: &str) -> bool {
        let trace = ForwardTraceContext::new("host-release");
        self.release_with_trace(sandbox_id, &trace, &json!({}))
            .unwrap_or(false)
    }

    pub fn release_with_trace(
        &self,
        sandbox_id: &str,
        trace: &ForwardTraceContext,
        args: &Value,
    ) -> Result<bool> {
        let op_started = Instant::now();
        self.trace_store.prepare_forward(RequestStartInput {
            sandbox_id,
            trace_id: trace.trace_id.clone(),
            request_id: trace.request_id.clone(),
            op: HOST_SANDBOX_RELEASE,
            caller_id: args.get("caller_id").and_then(Value::as_str),
            mutates_state: true,
            args: args.clone(),
        })?;
        self.record_host_gateway_events(sandbox_id, trace);
        let Some(record) = self.registry.get(sandbox_id) else {
            let response = host_error_response(
                HOST_SANDBOX_RELEASE,
                trace,
                HostGatewayErrorKind::UnknownSandbox.as_str(),
                &format!("unknown sandbox: {sandbox_id}"),
            );
            self.record_host_response_or_missing(sandbox_id, trace, &response, op_started)?;
            return Ok(false);
        };
        self.record_host_lifecycle_event(
            sandbox_id,
            trace,
            "container_removal_started",
            json!({"container": record.container.clone()}),
        );
        let docker_result = docker(["rm", "-f", record.container.as_str()]);
        self.record_host_lifecycle_event(
            sandbox_id,
            trace,
            "container_removal_finished",
            json!({
                "container": record.container.clone(),
                "removed": docker_result.is_ok(),
                "error": docker_result.as_ref().err().map(ToString::to_string),
            }),
        );
        match docker_result {
            Ok(_) => {
                self.registry.remove(sandbox_id);
                let response = host_ok_response(
                    HOST_SANDBOX_RELEASE,
                    trace,
                    json!({"sandbox_id": sandbox_id}),
                );
                self.record_host_response_or_missing(sandbox_id, trace, &response, op_started)?;
                Ok(true)
            }
            Err(err) => {
                let message = format!("container removal failed: {err:#}");
                let response = host_error_response(
                    HOST_SANDBOX_RELEASE,
                    trace,
                    HostGatewayErrorKind::SandboxUnavailable.as_str(),
                    &message,
                );
                self.record_host_response_or_missing(sandbox_id, trace, &response, op_started)?;
                Err(err.context(format!("remove sandbox container {}", record.container)))
            }
        }
    }

    pub fn status(&self, sandbox_id: &str) -> Option<SandboxStatus> {
        let record = self.registry.get(sandbox_id)?;
        let daemon = self.probe_readiness(&record);
        Some(SandboxStatus {
            sandbox_id: record.sandbox_id.clone(),
            container: record.container.clone(),
            endpoint: record.cached_endpoint(),
            created_by: record.created_by.clone(),
            daemon,
        })
    }

    pub fn list(&self) -> Vec<SandboxStatus> {
        self.registry
            .list()
            .into_iter()
            .map(|record| SandboxStatus {
                sandbox_id: record.sandbox_id.clone(),
                container: record.container.clone(),
                endpoint: record.cached_endpoint(),
                created_by: record.created_by.clone(),
                daemon: Value::Null,
            })
            .collect()
    }

    fn resolve_image_profile(&self, args: &Value) -> Result<(String, Option<String>)> {
        let profile = super::args::optional_string_arg(args, "image_profile").unwrap_or("default");
        if profile != "default" {
            bail!("unknown image_profile: {profile}");
        }
        Ok((self.config.image.clone(), self.config.platform.clone()))
    }

    pub(crate) fn probe_readiness(&self, record: &SandboxRecord) -> Value {
        let Some(endpoint) = record.cached_endpoint() else {
            return json!({"ready": false, "error": "endpoint not resolved"});
        };
        let client = ProtocolClient::new_forward_authorized(
            endpoint,
            Some(record.forward_token.clone()),
            self.config.request_timeout,
        );
        match client.request(
            READY_OP,
            "status-probe",
            &json!({"layer_stack_root": DEFAULT_LAYER_STACK_ROOT}),
        ) {
            Ok(resp) if response_is_accepted(&resp) => resp,
            Ok(resp) => json!({"ready": false, "error": resp}),
            Err(err) => json!({"ready": false, "error": err.to_string()}),
        }
    }
}
