//! `resolve_api_status` precedence-table tests (spec §Run State). Included into
//! `crate::status` under `#[cfg(test)]`.

use eos_backend_types::{ApiRunStatus, BackendRunStatus};
use eos_state::RequestStatus;

use super::resolve_api_status;

/// Every agent-core status the resolver may see, plus "no row yet".
const AGENT_CASES: [Option<RequestStatus>; 4] = [
    None,
    Some(RequestStatus::Running),
    Some(RequestStatus::Done),
    Some(RequestStatus::Failed),
];

#[test]
fn cancelled_backend_status_wins_over_any_agent_status() {
    for agent in AGENT_CASES {
        assert_eq!(
            resolve_api_status(BackendRunStatus::Cancelled, agent),
            ApiRunStatus::Cancelled,
            "cancelled must win over agent={agent:?}"
        );
    }
}

#[test]
fn terminal_backend_failed_and_done_win_over_any_agent_status() {
    for agent in AGENT_CASES {
        assert_eq!(
            resolve_api_status(BackendRunStatus::Failed, agent),
            ApiRunStatus::Failed
        );
        assert_eq!(
            resolve_api_status(BackendRunStatus::Done, agent),
            ApiRunStatus::Done
        );
    }
}

#[test]
fn non_terminal_backend_defers_to_agent_terminal_outcome() {
    for backend in [BackendRunStatus::Running, BackendRunStatus::Accepted] {
        assert_eq!(
            resolve_api_status(backend, Some(RequestStatus::Failed)),
            ApiRunStatus::Failed
        );
        assert_eq!(
            resolve_api_status(backend, Some(RequestStatus::Done)),
            ApiRunStatus::Done
        );
        // agent authoritatively running ⇒ running, even while backend is still
        // Accepted (the transient between bind and the Running write).
        assert_eq!(
            resolve_api_status(backend, Some(RequestStatus::Running)),
            ApiRunStatus::Running
        );
    }
}

#[test]
fn no_agent_row_falls_back_to_backend_accepted_running_view() {
    assert_eq!(
        resolve_api_status(BackendRunStatus::Accepted, None),
        ApiRunStatus::Accepted
    );
    assert_eq!(
        resolve_api_status(BackendRunStatus::Running, None),
        ApiRunStatus::Running
    );
}
