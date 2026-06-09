//! API run-status resolution: join backend [`BackendRunStatus`] with agent-core
//! [`RequestStatus`] into the single [`ApiRunStatus`] the HTTP API returns.
//!
//! This is a **pure** function (spec §Run State precedence table). It does not
//! persist: the table's "backend updates `finished_at` + status" write-back has
//! no caller until the Phase 7 read handlers exist, and doing it here would race
//! a concurrent `DELETE` (read `Running`+agent `Done` → write `Done`, clobbering
//! a just-written `Cancelled`). Phase 7 owns the write-back with a compare-and-set
//! guard; Phase 5 only needs the resolved value and tests this table.
//!
//! Precedence (highest first):
//!
//! | Backend status      | Agent status            | Resolved   |
//! |---------------------|-------------------------|------------|
//! | `Cancelled`         | any                     | `cancelled`|
//! | `Failed`            | any                     | `failed`   |
//! | `Done`              | any                     | `done`     |
//! | `Running`/`Accepted`| `Failed`                | `failed`   |
//! | `Running`/`Accepted`| `Done`                  | `done`     |
//! | `Running`/`Accepted`| `Running`               | `running`  |
//! | `Running`           | missing                 | `running`  |
//! | `Accepted`          | missing                 | `accepted` |
//!
//! The terminal *backend* states (`Cancelled`/`Failed`/`Done`) win over agent-core
//! because the reaper is the authority once it has finalized; a non-terminal
//! backend state defers to agent-core's authoritative terminal/running state, and
//! falls back to its own accepted/running view only while agent-core has no row.

use eos_backend_types::{ApiRunStatus, BackendRunStatus};
use eos_types::RequestStatus;

/// Resolve the API status from the backend run status and the agent-core request
/// status (`None` when agent-core has not created the request row yet — the
/// 202→bootstrap window).
#[must_use]
pub fn resolve_api_status(backend: BackendRunStatus, agent: Option<RequestStatus>) -> ApiRunStatus {
    match backend {
        // Terminal backend states are authoritative — the reaper has finalized.
        BackendRunStatus::Cancelled => ApiRunStatus::Cancelled,
        BackendRunStatus::Failed => ApiRunStatus::Failed,
        BackendRunStatus::Done => ApiRunStatus::Done,
        // Non-terminal backend state defers to agent-core's authoritative outcome,
        // then falls back to its own accepted/running view while no row exists.
        BackendRunStatus::Running | BackendRunStatus::Accepted => match agent {
            Some(RequestStatus::Failed) => ApiRunStatus::Failed,
            Some(RequestStatus::Done) => ApiRunStatus::Done,
            Some(RequestStatus::Cancelled) => ApiRunStatus::Cancelled,
            Some(RequestStatus::Running) => ApiRunStatus::Running,
            None => match backend {
                BackendRunStatus::Accepted => ApiRunStatus::Accepted,
                _ => ApiRunStatus::Running,
            },
        },
    }
}

#[cfg(test)]
#[path = "../tests/status/mod.rs"]
mod tests;
