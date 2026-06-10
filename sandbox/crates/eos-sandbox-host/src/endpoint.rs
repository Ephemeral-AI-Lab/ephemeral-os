//! Endpoint resolution: the docker-published loopback port for one sandbox,
//! cached on its registry record and invalidated by the recovery ladder.

use std::net::SocketAddr;

use anyhow::{bail, Result};

use crate::docker::resolve_published_addr;
use crate::registry::SandboxRecord;

/// The cached endpoint, or a fresh `docker port` resolution (cached on
/// success).
///
/// # Errors
/// Returns an error if docker cannot resolve a published port.
pub(crate) fn cached_or_resolve(record: &SandboxRecord) -> Result<SocketAddr> {
    if let Some(addr) = record.cached_endpoint() {
        return Ok(addr);
    }
    resolve(record)
}

/// Force a fresh `docker port` resolution and cache it.
///
/// # Errors
/// Returns an error if docker cannot resolve a published port.
pub(crate) fn resolve(record: &SandboxRecord) -> Result<SocketAddr> {
    record.invalidate_endpoint();
    match resolve_published_addr(&record.container, record.tcp_port)? {
        Some(addr) => {
            record.cache_endpoint(addr);
            Ok(addr)
        }
        None => bail!(
            "no published port {} for container {}",
            record.tcp_port,
            record.container
        ),
    }
}
