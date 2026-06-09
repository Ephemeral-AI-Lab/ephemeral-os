//! [`StatsReader`] — assemble the `/api/stats/*` DTOs from `obs_event` and
//! `audit_cursor`.
//!
//! This is the backend stats facade the Phase 7 API calls. It owns the canonical
//! obs `kind` vocabulary and hands it to the store's typed aggregations, then
//! composes the loss DTO from the durable `audit_cursor` totals plus a live
//! [`PersistingSink`](crate::PersistingSink) snapshot. The matched / unmatched
//! split keeps daemon-facing and model-facing identities distinct (AC7).

use eos_backend_store::{AuditCursorRepo, ObsEventRepo, StoreError};
use eos_backend_types::{
    AgentRunStat, CorrectnessStats, ObsEvent, ObsLossStats, Page, PageResult, PerformanceStats,
};

use crate::{sink::SinkLoss, AGENT_RUN_COMPLETED, OS_RESOURCE_SAMPLED, TOOL_CALL_COMPLETED};

/// Reads `/api/stats/*` summaries from `backend.db`. Holds cheap repository clones.
#[derive(Debug, Clone)]
pub struct StatsReader {
    obs_events: ObsEventRepo,
    cursors: AuditCursorRepo,
}

impl StatsReader {
    /// Build a stats reader over the backend store's obs/cursor repos.
    #[must_use]
    pub fn new(obs_events: ObsEventRepo, cursors: AuditCursorRepo) -> Self {
        Self {
            obs_events,
            cursors,
        }
    }

    /// `/api/stats/performance`: tool-call timing and resource sampling.
    ///
    /// # Errors
    /// [`StoreError`] on a query failure.
    pub async fn performance(&self) -> Result<PerformanceStats, StoreError> {
        self.obs_events
            .performance(TOOL_CALL_COMPLETED, OS_RESOURCE_SAMPLED)
            .await
    }

    /// `/api/stats/correctness`: observed runs/tool-calls and the matched vs
    /// unmatched daemon-audit split.
    ///
    /// # Errors
    /// [`StoreError`] on a query failure.
    pub async fn correctness(&self) -> Result<CorrectnessStats, StoreError> {
        self.obs_events
            .correctness(AGENT_RUN_COMPLETED, TOOL_CALL_COMPLETED)
            .await
    }

    /// `/api/stats/agent-runs`: per-agent-run obs rollups.
    ///
    /// # Errors
    /// [`StoreError`] on a query or id-decode failure.
    pub async fn agent_runs(&self) -> Result<Vec<AgentRunStat>, StoreError> {
        self.obs_events
            .agent_run_stats(TOOL_CALL_COMPLETED, OS_RESOURCE_SAMPLED)
            .await
    }

    /// `/api/stats/events`: a newest-first page of normalized obs events (engine and
    /// daemon).
    ///
    /// # Errors
    /// [`StoreError`] on a query or decode failure.
    pub async fn events(&self, page: Page) -> Result<PageResult<ObsEvent>, StoreError> {
        self.obs_events.list_page(page).await
    }

    /// Loss accounting: the live sink's in-memory drop/persist-failure counters
    /// folded with the durable `audit_cursor` totals. Pass
    /// [`PersistingSink::loss_snapshot`](crate::PersistingSink::loss_snapshot).
    ///
    /// # Errors
    /// [`StoreError`] on a query failure.
    pub async fn obs_loss(&self, sink_loss: SinkLoss) -> Result<ObsLossStats, StoreError> {
        let (audit_dropped, audit_sandboxes_with_loss) = self.cursors.loss_totals().await?;
        Ok(ObsLossStats {
            obs_dropped_inflight: sink_loss.dropped_inflight,
            obs_persist_failed: sink_loss.persist_failed,
            audit_dropped,
            audit_sandboxes_with_loss,
        })
    }
}
