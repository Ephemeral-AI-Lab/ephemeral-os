//! Agent-core request cancellation entry point (spec §12.1).
//!
//! This is the boundary backend-server calls to cancel a top-level request. It
//! is agent-core state only: it does not destroy the sandbox or call
//! `commit_to_workspace` — sandbox cleanup is the sandbox cancellation
//! substrate's job. Backend-server calls this and then calls the sandbox
//! cancellation boundary itself.

use anyhow::Result;
use eos_state::RequestStatus;
use eos_types::RequestId;

use crate::entry::root_task_id_for;
use crate::runtime_services::RuntimeServices;

/// The result of an agent-core request cancellation.
#[non_exhaustive]
#[derive(Debug, Clone)]
pub struct CancelReport {
    /// The cancelled request.
    pub request_id: RequestId,
    /// Whether a live run was found and torn down (vs. an already-finished
    /// request, where cancellation is an idempotent state flip).
    pub had_live_run: bool,
}

/// Cancel a top-level request: recursively cancel the root task (and every live
/// run it owns) through the request's `CancelPort`, then mark the request
/// `Cancelled`.
///
/// Idempotent: a non-live or already-terminal request flips no live work and
/// `finish_request` no-ops on a terminal request (never clobbering a `Done`
/// outcome with `Cancelled`).
///
/// # Errors
/// Returns an error if recursive task cancellation or the request-status write
/// fails.
pub async fn cancel_agent_core_user_request(
    services: &RuntimeServices,
    request_id: &RequestId,
    reason: &str,
) -> Result<CancelReport> {
    let root_task_id = root_task_id_for(request_id);
    let port = services.cancel_registry.get(request_id);
    let had_live_run = port.is_some();
    if let Some(port) = port {
        // Recurse: flips the root task to Cancelled and tears down the root run
        // and every descendant (subagents, delegated workflows, command sessions).
        port.cancel_task(&root_task_id, reason).await?;
    }
    services
        .db
        .request_store
        .finish_request(request_id, RequestStatus::Cancelled)
        .await?;
    Ok(CancelReport {
        request_id: request_id.clone(),
        had_live_run,
    })
}
