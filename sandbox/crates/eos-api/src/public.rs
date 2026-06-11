//! The op catalog as `eos-api` sees it: parsed from the committed
//! `contract/ops.json` (embedded at compile time — the shared artifact is
//! data, never code) and indexed by canonical name.

use std::collections::HashMap;
use std::sync::Arc;

use anyhow::{bail, Context, Result};
use serde_json::Value;

/// The committed catalog document this binary serves.
const OPS_JSON: &str = include_str!("../../../contract/ops.json");

/// Who may invoke an op, and from which socket.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Visibility {
    /// Client socket.
    Public,
    /// `eos-api admin` socket only.
    Operator,
    /// Host machinery only; never served from a socket.
    Internal,
    /// Daemon-side test hook; never served from a socket.
    Test,
}

/// Host lifecycle verbs (`served_by: host`), parsed once at catalog load so
/// the request path never branches on op names.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HostVerb {
    /// `sandbox.acquire`
    Acquire,
    /// `sandbox.release`
    Release,
    /// `sandbox.status`
    Status,
    /// `sandbox.list`
    List,
}

/// Where an op is served.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Route {
    /// Engine call in `eos-sandbox-host`.
    Host(HostVerb),
    /// Forwarded to the sandbox daemon.
    Daemon,
}

/// One catalog entry's routing metadata (the ONLY per-op data the router reads).
#[derive(Debug)]
pub struct OpEntry {
    /// Canonical name (for error messages and logs).
    pub name: String,
    /// Routing target.
    pub route: Route,
    /// Caller surface.
    pub visibility: Visibility,
    /// Drives the recovery ladder's fail-closed branch.
    pub mutates_state: bool,
}

/// The catalog indexed by canonical name.
pub struct Catalog {
    by_name: HashMap<String, Arc<OpEntry>>,
}

impl Catalog {
    /// Parse the embedded `contract/ops.json`.
    ///
    /// # Errors
    /// Returns an error when the committed catalog is malformed — every entry
    /// must resolve to a route (the SPEC §9.3 "router covers every catalog
    /// entry" bar).
    pub fn load_builtin() -> Result<Self> {
        Self::parse(OPS_JSON)
    }

    fn parse(ops_json: &str) -> Result<Self> {
        let document: Value = serde_json::from_str(ops_json).context("parse ops.json")?;
        let ops = document
            .get("ops")
            .and_then(Value::as_array)
            .context("ops.json must carry an `ops` array")?;
        let mut by_name = HashMap::new();
        for op in ops {
            let name = str_field(op, "name")?.to_owned();
            let served_by = str_field(op, "served_by")?;
            let route = match served_by {
                "daemon" => Route::Daemon,
                "host" => Route::Host(host_verb(&name)?),
                other => bail!("op {name}: unknown served_by {other:?}"),
            };
            let visibility = match str_field(op, "visibility")? {
                "public" => Visibility::Public,
                "operator" => Visibility::Operator,
                "internal" => Visibility::Internal,
                "test" => Visibility::Test,
                other => bail!("op {name}: unknown visibility {other:?}"),
            };
            let mutates_state = op
                .get("mutates_state")
                .and_then(Value::as_bool)
                .with_context(|| format!("op {name}: missing mutates_state"))?;
            let entry = Arc::new(OpEntry {
                name: name.clone(),
                route,
                visibility,
                mutates_state,
            });
            if by_name.insert(name.clone(), entry).is_some() {
                bail!("catalog name claimed twice: {name}");
            }
        }
        Ok(Self { by_name })
    }

    /// Resolve a request spelling to its catalog entry.
    #[must_use]
    pub fn lookup(&self, op: &str) -> Option<&Arc<OpEntry>> {
        self.by_name.get(op)
    }

    /// Every entry, for coverage tests.
    #[must_use]
    pub fn entries(&self) -> Vec<&Arc<OpEntry>> {
        self.by_name.values().collect()
    }
}

fn host_verb(name: &str) -> Result<HostVerb> {
    // The four host verbs are the entire host surface; an unrecognized
    // host-served name is a catalog/router drift and must fail at startup.
    match name {
        "sandbox.acquire" => Ok(HostVerb::Acquire),
        "sandbox.release" => Ok(HostVerb::Release),
        "sandbox.status" => Ok(HostVerb::Status),
        "sandbox.list" => Ok(HostVerb::List),
        other => bail!("host-served op {other} has no router implementation"),
    }
}

fn str_field<'a>(op: &'a Value, field: &str) -> Result<&'a str> {
    op.get(field)
        .and_then(Value::as_str)
        .with_context(|| format!("catalog op missing string field {field}"))
}
