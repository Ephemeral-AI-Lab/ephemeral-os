//! `ObsEventRepo` and `SandboxCallCorrelationRepo` — the observability event log
//! and the model/daemon correlation bridge.

use sqlx::sqlite::SqliteRow;
use sqlx::{Row, SqlitePool};
use time::OffsetDateTime;

use eos_backend_types::{
    AgentRunStat, CorrectnessStats, ObsEvent, ObsSource, Page, PageResult, PerformanceStats,
    SandboxCallCorrelation,
};
use eos_protocol::CallerId;
use eos_types::{AgentRunId, InvocationId, RequestId, SandboxId, TaskId, ToolUseId};

use crate::db::{id_in, json_decode, json_encode, opt_id_in, ts_in, ts_out, StoreError};

/// JSON path to a tool-call duration, falling back to the daemon's `total_ms`.
const TOOL_CALL_MS: &str = "COALESCE(json_extract(payload_json, '$.tool_call.duration_ms'), \
     json_extract(payload_json, '$.tool_call.total_ms'))";
/// JSON path to a resource sample's RSS bytes.
const RSS_BYTES: &str = "json_extract(payload_json, '$.os_resource.rss_bytes')";

const OBS_COLUMNS: &str = "id, request_id, task_id, agent_run_id, tool_use_id, \
     sandbox_invocation_id, sandbox_id, source, kind, payload_json, created_at";

const OBS_INSERT_COLUMNS: &str = "request_id, task_id, agent_run_id, tool_use_id, \
     sandbox_invocation_id, sandbox_id, source, kind, payload_json, created_at";

const CORR_COLUMNS: &str = "request_id, task_id, agent_run_id, tool_use_id, \
     sandbox_invocation_id, caller_id, sandbox_id, created_at";

/// Repository for persisted observability events. Holds a cheap `SqlitePool` clone.
#[derive(Debug, Clone)]
pub struct ObsEventRepo {
    pool: SqlitePool,
}

impl ObsEventRepo {
    pub(crate) fn new(pool: SqlitePool) -> Self {
        Self { pool }
    }

    /// Insert an obs event and return its autoincrement id. Unmatched daemon
    /// rows are inserted with null model-facing ids (AC7).
    ///
    /// # Errors
    /// [`StoreError`] on a query or encode failure.
    pub async fn insert(&self, event: &ObsEvent) -> Result<i64, StoreError> {
        let result = sqlx::query(&format!(
            "INSERT INTO obs_event ({OBS_INSERT_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        ))
        .bind(event.request_id.as_ref().map(RequestId::as_str))
        .bind(event.task_id.as_ref().map(TaskId::as_str))
        .bind(event.agent_run_id.as_ref().map(AgentRunId::as_str))
        .bind(event.tool_use_id.as_ref().map(ToolUseId::as_str))
        .bind(event.sandbox_invocation_id.as_ref().map(InvocationId::as_str))
        .bind(event.sandbox_id.as_ref().map(SandboxId::as_str))
        .bind(event.source.as_str())
        .bind(&event.kind)
        .bind(json_encode(&event.payload)?)
        .bind(ts_in(event.created_at))
        .execute(&self.pool)
        .await?;
        Ok(result.last_insert_rowid())
    }

    /// All obs events for a request, oldest-first.
    ///
    /// # Errors
    /// [`StoreError`] on a query or decode failure.
    pub async fn list_for_request(
        &self,
        request_id: &RequestId,
    ) -> Result<Vec<ObsEvent>, StoreError> {
        let rows = sqlx::query(&format!(
            "SELECT {OBS_COLUMNS} FROM obs_event WHERE request_id = ? ORDER BY id ASC"
        ))
        .bind(request_id.as_str())
        .fetch_all(&self.pool)
        .await?;
        rows.iter().map(row_to_obs_event).collect()
    }

    /// A newest-first page of all obs events (`/api/stats/events`). `total` is the
    /// full unpaginated row count.
    ///
    /// # Errors
    /// [`StoreError`] on a query or decode failure.
    pub async fn list_page(&self, page: Page) -> Result<PageResult<ObsEvent>, StoreError> {
        let total: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM obs_event")
            .fetch_one(&self.pool)
            .await?;
        let rows = sqlx::query(&format!(
            "SELECT {OBS_COLUMNS} FROM obs_event ORDER BY id DESC LIMIT ? OFFSET ?"
        ))
        .bind(i64::from(page.limit))
        .bind(i64::from(page.offset))
        .fetch_all(&self.pool)
        .await?;
        let items = rows
            .iter()
            .map(row_to_obs_event)
            .collect::<Result<Vec<_>, _>>()?;
        Ok(PageResult {
            items,
            total: total.max(0) as u64,
            limit: page.limit,
            offset: page.offset,
        })
    }

    /// Timing and resource summary across all obs events. The caller supplies the
    /// canonical obs `kind` vocabulary (owned by `eos-backend-obs` / `eos-audit`).
    ///
    /// # Errors
    /// [`StoreError`] on a query failure.
    pub async fn performance(
        &self,
        tool_call_kind: &str,
        resource_kind: &str,
    ) -> Result<PerformanceStats, StoreError> {
        let row = sqlx::query(&format!(
            "SELECT \
               COUNT(CASE WHEN kind = ? THEN 1 END) AS tc_count, \
               CAST(COALESCE(SUM(CASE WHEN kind = ? THEN {TOOL_CALL_MS} END), 0) AS REAL) \
                 AS tc_total_ms, \
               COUNT(CASE WHEN kind = ? THEN 1 END) AS rs_count, \
               MAX({RSS_BYTES}) AS rss_max \
             FROM obs_event"
        ))
        .bind(tool_call_kind)
        .bind(tool_call_kind)
        .bind(resource_kind)
        .fetch_one(&self.pool)
        .await?;
        let tool_call_count: i64 = row.try_get("tc_count")?;
        let tool_call_total_ms: f64 = row.try_get("tc_total_ms")?;
        let resource_sample_count: i64 = row.try_get("rs_count")?;
        let rss_bytes_max: Option<i64> = row.try_get("rss_max")?;
        Ok(PerformanceStats {
            tool_call_count: tool_call_count.max(0) as u64,
            tool_call_total_ms,
            tool_call_avg_ms: (tool_call_count > 0)
                .then(|| tool_call_total_ms / tool_call_count as f64),
            resource_sample_count: resource_sample_count.max(0) as u64,
            rss_bytes_max,
        })
    }

    /// Correctness summary: observed agent runs and tool calls plus the
    /// matched/unmatched daemon-audit split (AC7). A matched row has a model-facing
    /// `tool_use_id` joined through the bridge; an unmatched row has only a
    /// `sandbox_invocation_id`.
    ///
    /// # Errors
    /// [`StoreError`] on a query failure.
    pub async fn correctness(
        &self,
        agent_run_kind: &str,
        tool_call_kind: &str,
    ) -> Result<CorrectnessStats, StoreError> {
        let row = sqlx::query(
            "SELECT \
               COUNT(CASE WHEN kind = ? THEN 1 END) AS agent_runs, \
               COUNT(CASE WHEN kind = ? THEN 1 END) AS tool_calls, \
               COUNT(CASE WHEN source = 'daemon' AND tool_use_id IS NOT NULL THEN 1 END) \
                 AS matched, \
               COUNT(CASE WHEN source = 'daemon' AND tool_use_id IS NULL \
                 AND sandbox_invocation_id IS NOT NULL THEN 1 END) AS unmatched \
             FROM obs_event",
        )
        .bind(agent_run_kind)
        .bind(tool_call_kind)
        .fetch_one(&self.pool)
        .await?;
        let count = |name| -> Result<u64, StoreError> {
            let raw: i64 = row.try_get(name)?;
            Ok(raw.max(0) as u64)
        };
        Ok(CorrectnessStats {
            agent_runs_observed: count("agent_runs")?,
            tool_calls_observed: count("tool_calls")?,
            audit_matched: count("matched")?,
            audit_unmatched: count("unmatched")?,
        })
    }

    /// Per-agent-run rollup of tool-call and resource-sample activity, ascending by
    /// agent-run id. Rows with no `agent_run_id` are excluded.
    ///
    /// # Errors
    /// [`StoreError`] on a query or id-decode failure.
    pub async fn agent_run_stats(
        &self,
        tool_call_kind: &str,
        resource_kind: &str,
    ) -> Result<Vec<AgentRunStat>, StoreError> {
        let rows = sqlx::query(&format!(
            "SELECT agent_run_id AS arid, \
               COUNT(CASE WHEN kind = ? THEN 1 END) AS tc_count, \
               CAST(COALESCE(SUM(CASE WHEN kind = ? THEN {TOOL_CALL_MS} END), 0) AS REAL) \
                 AS tc_total_ms, \
               COUNT(CASE WHEN kind = ? THEN 1 END) AS rs_count \
             FROM obs_event \
             WHERE agent_run_id IS NOT NULL \
             GROUP BY agent_run_id \
             ORDER BY agent_run_id ASC"
        ))
        .bind(tool_call_kind)
        .bind(tool_call_kind)
        .bind(resource_kind)
        .fetch_all(&self.pool)
        .await?;
        rows.iter()
            .map(|row| {
                let tool_call_count: i64 = row.try_get("tc_count")?;
                let resource_sample_count: i64 = row.try_get("rs_count")?;
                Ok(AgentRunStat {
                    agent_run_id: id_in("obs_event.agent_run_id", row.try_get("arid")?)?,
                    tool_call_count: tool_call_count.max(0) as u64,
                    tool_call_total_ms: row.try_get("tc_total_ms")?,
                    resource_sample_count: resource_sample_count.max(0) as u64,
                })
            })
            .collect()
    }
}

/// Repository for the model/daemon correlation bridge. Holds a cheap pool clone.
#[derive(Debug, Clone)]
pub struct SandboxCallCorrelationRepo {
    pool: SqlitePool,
}

impl SandboxCallCorrelationRepo {
    pub(crate) fn new(pool: SqlitePool) -> Self {
        Self { pool }
    }

    /// Insert a correlation bridge row (recorded before the daemon request is
    /// sent). Keyed by `(sandbox_id, caller_id, sandbox_invocation_id)`.
    ///
    /// # Errors
    /// [`StoreError`] on a primary-key collision or query failure.
    pub async fn insert(&self, bridge: &SandboxCallCorrelation) -> Result<(), StoreError> {
        sqlx::query(&format!(
            "INSERT INTO sandbox_call_correlation ({CORR_COLUMNS}) \
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        ))
        .bind(bridge.request_id.as_str())
        .bind(bridge.task_id.as_str())
        .bind(bridge.agent_run_id.as_str())
        .bind(bridge.tool_use_id.as_str())
        .bind(bridge.sandbox_invocation_id.as_str())
        .bind(bridge.caller_id.0.as_str())
        .bind(bridge.sandbox_id.as_str())
        .bind(ts_in(bridge.created_at))
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Look up a bridge row by its full daemon join key.
    ///
    /// # Errors
    /// [`StoreError`] on a query or decode failure.
    pub async fn get(
        &self,
        sandbox_id: &SandboxId,
        caller_id: &CallerId,
        sandbox_invocation_id: &InvocationId,
    ) -> Result<Option<SandboxCallCorrelation>, StoreError> {
        let row = sqlx::query(&format!(
            "SELECT {CORR_COLUMNS} FROM sandbox_call_correlation \
             WHERE sandbox_id = ? AND caller_id = ? AND sandbox_invocation_id = ?"
        ))
        .bind(sandbox_id.as_str())
        .bind(caller_id.0.as_str())
        .bind(sandbox_invocation_id.as_str())
        .fetch_optional(&self.pool)
        .await?;
        row.as_ref().map(row_to_correlation).transpose()
    }
}

fn row_to_obs_event(row: &SqliteRow) -> Result<ObsEvent, StoreError> {
    let source_raw: String = row.try_get("source")?;
    let source = ObsSource::from_db(&source_raw).ok_or(StoreError::InvalidEnum {
        field: "obs_event.source",
        value: source_raw,
    })?;
    let payload_json: String = row.try_get("payload_json")?;
    let created_at: OffsetDateTime = row.try_get("created_at")?;
    Ok(ObsEvent {
        id: Some(row.try_get("id")?),
        request_id: opt_id_in("obs_event.request_id", row.try_get("request_id")?)?,
        task_id: opt_id_in("obs_event.task_id", row.try_get("task_id")?)?,
        agent_run_id: opt_id_in("obs_event.agent_run_id", row.try_get("agent_run_id")?)?,
        tool_use_id: opt_id_in("obs_event.tool_use_id", row.try_get("tool_use_id")?)?,
        sandbox_invocation_id: opt_id_in(
            "obs_event.sandbox_invocation_id",
            row.try_get("sandbox_invocation_id")?,
        )?,
        sandbox_id: opt_id_in("obs_event.sandbox_id", row.try_get("sandbox_id")?)?,
        source,
        kind: row.try_get("kind")?,
        payload: json_decode(&payload_json)?,
        created_at: ts_out(created_at),
    })
}

fn row_to_correlation(row: &SqliteRow) -> Result<SandboxCallCorrelation, StoreError> {
    let created_at: OffsetDateTime = row.try_get("created_at")?;
    Ok(SandboxCallCorrelation {
        request_id: id_in("sandbox_call_correlation.request_id", row.try_get("request_id")?)?,
        task_id: id_in("sandbox_call_correlation.task_id", row.try_get("task_id")?)?,
        agent_run_id: id_in(
            "sandbox_call_correlation.agent_run_id",
            row.try_get("agent_run_id")?,
        )?,
        tool_use_id: id_in("sandbox_call_correlation.tool_use_id", row.try_get("tool_use_id")?)?,
        sandbox_invocation_id: id_in(
            "sandbox_call_correlation.sandbox_invocation_id",
            row.try_get("sandbox_invocation_id")?,
        )?,
        caller_id: CallerId(row.try_get("caller_id")?),
        sandbox_id: id_in("sandbox_call_correlation.sandbox_id", row.try_get("sandbox_id")?)?,
        created_at: ts_out(created_at),
    })
}
