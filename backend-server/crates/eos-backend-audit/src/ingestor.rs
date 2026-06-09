//! [`AuditIngestor`] — ingest a daemon `api.audit.pull` response into `obs_event`,
//! joining daemon-facing identities to model-facing ids through
//! `sandbox_call_correlation`, and tracking the per-sandbox `audit_cursor` across
//! daemon reboots (AC7, AC8).
//!
//! The crux of AC7: the daemon stamps its own **invocation id** into the audit
//! event's `tool_call.tool_use_id` slot (see the daemon transport server). So for a
//! daemon row this ingestor treats that value as the `sandbox_invocation_id`, never
//! as a model `tool_use_id`. The model-facing `tool_use_id` (and the owning
//! request/task/agent-run) come **only** from a matched correlation bridge; with no
//! bridge the row persists with null model ids and an `unmatched` marker.
//!
//! Boot epoch (AC8): the ingestor reads `boot_epoch_id` from the pull snapshot
//! before advancing `last_seq`. A changed epoch means the daemon rebooted into a
//! fresh sequence space, so the cursor records loss for the prior epoch and resets
//! `last_seq` rather than trusting a sequence comparison.
//!
//! This module owns the *ingest* of a pull response; the transport-driven poll
//! loop that fetches responses (`DaemonOp::AuditPull`) is wired at the backend
//! composition root (later phase), keeping the obs crate free of a transport edge.

use serde_json::{Map, Value};

use eos_backend_store::{AuditCursorRepo, ObsEventRepo, SandboxCallCorrelationRepo, StoreError};
use eos_backend_types::{AuditCursor, ObsEvent, ObsSource, SandboxCallCorrelation};
use eos_protocol::CallerId;
use eos_types::{InvocationId, SandboxId, UtcDateTime};

use crate::{normalize_sandbox_pull_response, ObsEnvelope, ObsNormalizationError};

/// Payload marker stamped on a daemon row that has an invocation id but no
/// correlation bridge. Visible in `/api/stats/events` without a re-join; the
/// column shape (`tool_use_id IS NULL AND sandbox_invocation_id IS NOT NULL`) is
/// the durable source of truth for stats.
pub const UNMATCHED_MARKER: &str = "unmatched";

/// Payload sections (and the top level) that may carry a daemon `caller_id`.
const CALLER_ID_SECTIONS: &[&str] = &[
    "tool_call",
    "os_resource",
    "background_tool",
    "isolated_workspace",
];

/// Errors raised while ingesting a daemon audit pull.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum IngestError {
    /// The pull response had no `snapshot.daemon.boot_epoch_id`; without boot
    /// identity the cursor cannot safely advance `last_seq` (AC8).
    #[error("daemon audit pull is missing snapshot.daemon.boot_epoch_id")]
    MissingBootEpoch,
    /// The pull response could not be normalized.
    #[error("daemon audit pull could not be normalized")]
    Normalize(#[from] ObsNormalizationError),
    /// A backend store read/write failed.
    #[error("backend store error while ingesting daemon audit")]
    Store(#[from] StoreError),
}

/// Outcome of ingesting one pull response, for loss accounting and tests.
#[derive(Debug, Clone, PartialEq)]
pub struct IngestReport {
    /// Whether this pull observed a daemon reboot (epoch change).
    pub epoch_reset: bool,
    /// Daemon rows joined to a correlation bridge.
    pub matched: u64,
    /// Daemon rows with an invocation id but no bridge.
    pub unmatched: u64,
    /// The cursor persisted after this pull (carries `boot_epoch_id` and loss).
    pub cursor: AuditCursor,
}

/// Ingests daemon audit pulls into `obs_event` + `audit_cursor`, joining the
/// correlation bridge. Holds cheap repository clones.
#[derive(Debug, Clone)]
pub struct AuditIngestor {
    obs_events: ObsEventRepo,
    correlations: SandboxCallCorrelationRepo,
    cursors: AuditCursorRepo,
}

impl AuditIngestor {
    /// Build an ingestor over the backend store's obs/correlation/cursor repos.
    #[must_use]
    pub fn new(
        obs_events: ObsEventRepo,
        correlations: SandboxCallCorrelationRepo,
        cursors: AuditCursorRepo,
    ) -> Self {
        Self {
            obs_events,
            correlations,
            cursors,
        }
    }

    /// Ingest one `api.audit.pull` response for `sandbox_id`: persist every
    /// normalized event as an `obs_event` (matched or unmatched), then advance the
    /// `audit_cursor` epoch-safely.
    ///
    /// # Errors
    /// [`IngestError`] when the response lacks boot identity, fails normalization,
    /// or a store operation fails.
    pub async fn ingest_pull(
        &self,
        sandbox_id: &SandboxId,
        response: &Value,
    ) -> Result<IngestReport, IngestError> {
        let boot_epoch_id = boot_epoch_id(response).ok_or(IngestError::MissingBootEpoch)?;
        let batch = normalize_sandbox_pull_response(response)?;
        let prior = self.cursors.get(sandbox_id).await?;
        let epoch_reset = prior
            .as_ref()
            .is_some_and(|cursor| cursor.boot_epoch_id != boot_epoch_id);

        let mut matched = 0;
        let mut unmatched = 0;
        for row in &batch.rows {
            let invocation = invocation_id(row);
            let caller = caller_id(&row.payload);
            let bridge = match (&invocation, &caller) {
                (Some(invocation), Some(caller)) => {
                    self.correlations
                        .get(sandbox_id, caller, invocation)
                        .await?
                }
                _ => None,
            };
            if invocation.is_some() {
                if bridge.is_some() {
                    matched += 1;
                } else {
                    unmatched += 1;
                }
            }
            let obs = build_obs_event(sandbox_id, row, invocation.as_ref(), bridge.as_ref());
            self.obs_events.insert(&obs).await?;
        }

        let cursor = next_cursor(
            sandbox_id,
            boot_epoch_id,
            epoch_reset,
            prior.as_ref(),
            &batch,
        );
        self.cursors.upsert(&cursor).await?;
        Ok(IngestReport {
            epoch_reset,
            matched,
            unmatched,
            cursor,
        })
    }
}

/// Build the persisted `obs_event` for one normalized daemon row.
///
/// Model-facing ids (`request_id`/`task_id`/`agent_run_id`/`tool_use_id`) come
/// **only** from a matched `bridge`; the daemon invocation id is stored solely as
/// `sandbox_invocation_id`. A row that has an invocation id but no bridge is stamped
/// with the [`UNMATCHED_MARKER`] and keeps null model ids (AC7).
fn build_obs_event(
    sandbox_id: &SandboxId,
    row: &ObsEnvelope,
    invocation: Option<&InvocationId>,
    bridge: Option<&SandboxCallCorrelation>,
) -> ObsEvent {
    let mut payload = row.payload.clone();
    // A row with an invocation id but no bridge is unmatched: mark it and keep model
    // ids null. The model ids are lifted ONLY from the bridge (`Option<&_>` is `Copy`),
    // so the daemon invocation never lands in `tool_use_id` (AC7).
    if bridge.is_none() && invocation.is_some() {
        payload.insert(UNMATCHED_MARKER.to_owned(), Value::Bool(true));
    }
    ObsEvent {
        id: None,
        request_id: bridge.map(|bridge| bridge.request_id.clone()),
        task_id: bridge.map(|bridge| bridge.task_id.clone()),
        agent_run_id: bridge.map(|bridge| bridge.agent_run_id.clone()),
        tool_use_id: bridge.map(|bridge| bridge.tool_use_id.clone()),
        sandbox_invocation_id: invocation.cloned(),
        sandbox_id: Some(sandbox_id.clone()),
        source: ObsSource::Daemon,
        kind: row.event_type.clone(),
        payload: Value::Object(payload),
        created_at: UtcDateTime::now(),
    }
}

/// Compute the next `audit_cursor`, recording prior-epoch loss before trusting a
/// new sequence space.
fn next_cursor(
    sandbox_id: &SandboxId,
    boot_epoch_id: i64,
    epoch_reset: bool,
    prior: Option<&AuditCursor>,
    batch: &crate::SandboxPullBatch,
) -> AuditCursor {
    let prior_last = prior.map_or(0, |cursor| cursor.last_seq);
    let cursor_after = batch.loss.cursor_after_seq;
    let ring_lost = batch.loss.lost_before_seq;
    let ring_dropped = batch.loss.dropped_event_count.unwrap_or(0).max(0) as u64;

    let (last_seq, lost_before_seq, dropped_count) = if epoch_reset {
        // Daemon rebooted: record loss up to the old high-water mark, then reset
        // `last_seq` into the new epoch's space (do not max with the old epoch).
        // `dropped_count` likewise tracks only the current epoch — the daemon's ring
        // counter restarts at reboot, so the prior epoch's count is intentionally
        // replaced (not summed or maxed); a single cursor row cannot hold a lifetime
        // total. The durable cross-epoch loss signal is `lost_before_seq` /
        // `audit_sandboxes_with_loss`, not this per-epoch count.
        //
        // Only record a real (> 0) loss boundary: a degenerate reboot with
        // `prior_last == 0` and no ring-reported loss must stay null, matching
        // `SandboxAuditLoss::has_counted_loss` (`seq > 0`) so the two loss
        // consumers agree on whether the row carries loss.
        let lost = max_opt(Some(prior_last), ring_lost).filter(|&seq| seq > 0);
        (cursor_after.unwrap_or(0), lost, ring_dropped)
    } else {
        // Same epoch: never regress `last_seq`; keep the furthest loss boundary and
        // the daemon's cumulative dropped count.
        let last = cursor_after.map_or(prior_last, |after| after.max(prior_last));
        let lost = max_opt(prior.and_then(|cursor| cursor.lost_before_seq), ring_lost);
        let dropped = prior.map_or(ring_dropped, |cursor| {
            cursor.dropped_count.max(ring_dropped)
        });
        (last, lost, dropped)
    };

    AuditCursor {
        sandbox_id: sandbox_id.clone(),
        last_seq,
        boot_epoch_id,
        lost_before_seq,
        dropped_count,
        updated_at: UtcDateTime::now(),
    }
}

/// The daemon boot epoch from `snapshot.daemon.boot_epoch_id`.
fn boot_epoch_id(response: &Value) -> Option<i64> {
    response
        .get("snapshot")?
        .get("daemon")?
        .get("boot_epoch_id")?
        .as_i64()
}

/// The daemon invocation id of a normalized sandbox row, parsed as an
/// [`InvocationId`]. Normalization places it in the `tool_use_id` slot — that is
/// the daemon's identity, not a model tool-use id.
fn invocation_id(row: &ObsEnvelope) -> Option<InvocationId> {
    let raw = row.ids.tool_use_id.as_deref()?;
    InvocationId::try_from(raw.to_owned()).ok()
}

/// The daemon `caller_id` from a normalized row payload (top level or a known
/// section), if present and non-empty.
fn caller_id(payload: &Map<String, Value>) -> Option<CallerId> {
    let raw = string_at(payload, &["caller_id"]).or_else(|| {
        CALLER_ID_SECTIONS
            .iter()
            .find_map(|section| string_at(payload, &[section, "caller_id"]))
    });
    raw.filter(|value| !value.is_empty()).map(CallerId)
}

/// Read a string at `path` within a JSON object map.
fn string_at(map: &Map<String, Value>, path: &[&str]) -> Option<String> {
    let (first, rest) = path.split_first()?;
    let mut current = map.get(*first)?;
    for key in rest {
        current = current.get(*key)?;
    }
    current.as_str().map(str::to_owned)
}

/// The larger of two optional sequence values (`None` is "unknown").
fn max_opt(left: Option<i64>, right: Option<i64>) -> Option<i64> {
    match (left, right) {
        (Some(left), Some(right)) => Some(left.max(right)),
        (value @ Some(_), None) | (None, value @ Some(_)) => value,
        (None, None) => None,
    }
}

#[cfg(test)]
#[path = "../tests/ingestor/mod.rs"]
mod tests;
