//! `NodePool` — up to `sandboxes` daemon containers behind a blocking semaphore,
//! plus `NodeLease`, the ergonomic per-test handle that mints a fresh
//! `layer_stack_root` and injects the standard envelope members.
//!
//! A lease holds its node exclusively for the test's duration; the node (and its
//! daemon) is reused across leases until `recycle_after` checkouts bound scratch
//! growth, then torn down.

use std::sync::{Condvar, Mutex, MutexGuard, PoisonError};

use anyhow::{bail, Result};
use eos_protocol::ops;
use serde_json::{json, Map, Value};

use crate::audit::AuditTap;
use crate::client::{error_kind, is_success, ProtocolClient};
use crate::config::{Config, NodeMode};
use crate::container::{reap_e2e_containers, DaemonContainer, E2E_ROOT_DIR};
use crate::{next_invocation_id, unique_suffix};

struct Node {
    container: DaemonContainer,
    checkouts: usize,
}

struct Inner {
    available: Vec<Node>,
    created: usize,
}

/// A bounded pool of daemon containers.
pub struct NodePool {
    config: Config,
    inner: Mutex<Inner>,
    cvar: Condvar,
}

impl NodePool {
    /// Build a pool from `config`, adopting warm kept containers when enabled.
    #[must_use]
    pub fn new(config: Config) -> Self {
        if !config.keep_container || config.mode == NodeMode::PerTest {
            reap_stale_containers();
        }
        let cap = cap_for(&config);
        let available: Vec<Node> = if config.keep_container && config.mode != NodeMode::PerTest {
            DaemonContainer::adopt_healthy(&config)
                .into_iter()
                .take(cap)
                .map(|container| Node {
                    container,
                    checkouts: 0,
                })
                .collect()
        } else {
            Vec::new()
        };
        let created = available.len();
        Self {
            config,
            inner: Mutex::new(Inner {
                available,
                created,
            }),
            cvar: Condvar::new(),
        }
    }

    /// The effective cap (1 for `shared`, else `sandboxes`).
    fn cap(&self) -> usize {
        cap_for(&self.config)
    }

    fn lock(&self) -> MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Acquire a node lease, blocking until one is free (or a new one can spawn).
    ///
    /// # Errors
    /// Returns an error if a container fails to start or its base cannot be built.
    pub fn acquire(&self) -> Result<NodeLease<'_>> {
        let node = self.take_node()?;
        match NodeLease::open(self, node) {
            Ok(lease) => Ok(lease),
            Err((node, err)) => {
                // Minting the root failed; return the node so the slot frees.
                self.give_back(node, false);
                Err(err)
            }
        }
    }

    fn take_node(&self) -> Result<Node> {
        let mut inner = self.lock();
        loop {
            if let Some(node) = inner.available.pop() {
                return Ok(node);
            }
            if inner.created < self.cap() {
                inner.created += 1;
                drop(inner);
                match DaemonContainer::start(&self.config) {
                    Ok(container) => {
                        return Ok(Node {
                            container,
                            checkouts: 0,
                        })
                    }
                    Err(err) => {
                        let mut inner = self.lock();
                        inner.created -= 1;
                        self.cvar.notify_one();
                        return Err(err);
                    }
                }
            }
            inner = self
                .cvar
                .wait(inner)
                .unwrap_or_else(PoisonError::into_inner);
        }
    }

    fn give_back(&self, node: Node, recycle: bool) {
        let mut inner = self.lock();
        if recycle {
            inner.created -= 1;
            drop(inner);
            drop(node); // container teardown happens here
        } else {
            inner.available.push(node);
        }
        self.cvar.notify_one();
    }
}

/// A checked-out node: one daemon, one fresh `layer_stack_root`, one agent id.
pub struct NodeLease<'p> {
    pool: &'p NodePool,
    node: Option<Node>,
    stack_root: String,
    workspace_root: String,
    agent_id: String,
}

impl<'p> NodeLease<'p> {
    fn open(pool: &'p NodePool, mut node: Node) -> Result<Self, (Node, anyhow::Error)> {
        node.checkouts += 1;
        let id = unique_suffix();
        let base = format!("{E2E_ROOT_DIR}/{id}");
        let stack_root = format!("{base}/stack");
        let workspace_root = format!("{base}/work");
        let agent_id = format!("agent-{id}");

        if let Err(err) = node
            .container
            .exec(&["mkdir", "-p", &stack_root, &workspace_root])
        {
            return Err((node, err));
        }
        let iid = next_invocation_id();
        let ensure = node.container.client().request(
            ops::API_ENSURE_WORKSPACE_BASE,
            &iid,
            &json!({
                "layer_stack_root": stack_root,
                "workspace_root": workspace_root,
                "agent_id": agent_id,
            }),
        );
        match ensure {
            Ok(resp) if is_success(&resp) => Ok(Self {
                pool,
                node: Some(node),
                stack_root,
                workspace_root,
                agent_id,
            }),
            Ok(resp) => Err((
                node,
                anyhow::anyhow!("ensure_workspace_base failed: {resp}"),
            )),
            Err(err) => Err((node, err)),
        }
    }

    fn node(&self) -> &Node {
        self.node.as_ref().expect("lease node present until drop")
    }

    /// The wire client for this lease's daemon.
    #[must_use]
    pub fn client(&self) -> &ProtocolClient {
        self.node().container.client()
    }

    /// The container backing this lease (for lifecycle/provisioning exec only).
    #[must_use]
    pub fn container(&self) -> &DaemonContainer {
        &self.node().container
    }

    /// This lease's fresh `layer_stack_root`.
    #[must_use]
    pub fn root(&self) -> &str {
        &self.stack_root
    }

    /// This lease's `workspace_root` (the base-seed / commit target dir).
    #[must_use]
    pub fn workspace_root(&self) -> &str {
        &self.workspace_root
    }

    /// This lease's unique agent id (auto-injected into [`Self::call`]).
    #[must_use]
    pub fn agent_id(&self) -> &str {
        &self.agent_id
    }

    /// Invoke `op` with `args`, auto-injecting `layer_stack_root` and `agent_id`
    /// (unless the caller already set them) plus a fresh invocation id.
    ///
    /// Returns the decoded response (success payload OR daemon error envelope).
    ///
    /// # Errors
    /// Returns an error only on transport failure.
    pub fn call(&self, op: &str, args: Value) -> Result<Value> {
        let mut obj = match args {
            Value::Object(map) => map,
            Value::Null => Map::new(),
            other => {
                bail!("call args must be a JSON object, got {other}");
            }
        };
        obj.entry("layer_stack_root".to_owned())
            .or_insert_with(|| json!(self.stack_root));
        obj.entry("agent_id".to_owned())
            .or_insert_with(|| json!(self.agent_id));
        let iid = next_invocation_id();
        self.client().request(op, &iid, &Value::Object(obj))
    }

    /// Like [`Self::call`] but asserts the response is a success payload.
    ///
    /// # Errors
    /// Returns an error on transport failure or a non-success response.
    pub fn call_ok(&self, op: &str, args: Value) -> Result<Value> {
        let resp = self.call(op, args)?;
        if is_success(&resp) {
            Ok(resp)
        } else {
            bail!(
                "{op} returned error{}: {resp}",
                error_kind(&resp).map_or(String::new(), |k| format!(" ({k})"))
            )
        }
    }

    /// Baseline a fresh audit tap on this lease's daemon.
    ///
    /// # Errors
    /// Returns an error if the baseline pull fails.
    pub fn audit_tap(&self) -> Result<AuditTap> {
        AuditTap::baseline(self.client().clone(), self.pool.config.audit_pull_limit)
    }
}

impl Drop for NodeLease<'_> {
    fn drop(&mut self) {
        if let Some(node) = self.node.take() {
            let recycle =
                self.pool.config.mode == NodeMode::PerTest || node.checkouts >= self.pool.config.recycle_after;
            self.pool.give_back(node, recycle);
        }
    }
}

fn cap_for(config: &Config) -> usize {
    match config.mode {
        NodeMode::Shared | NodeMode::PerFile => 1,
        NodeMode::Pool | NodeMode::PerTest => config.sandboxes,
    }
}

/// Remove stale `eos-e2e-*` containers left by prior runs (best effort).
fn reap_stale_containers() {
    let _ = reap_e2e_containers();
}
