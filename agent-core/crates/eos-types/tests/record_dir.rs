#![allow(clippy::expect_used)]

use eos_types::{format_record_dir, AgentRunId, AgentRunRecordIndex, RequestId};

fn id<T>(value: &str) -> T
where
    T: std::str::FromStr,
    T::Err: std::fmt::Debug,
{
    value.parse().expect("valid id")
}

fn index(request_id: &RequestId, agent_run_id: &AgentRunId) -> AgentRunRecordIndex {
    AgentRunRecordIndex {
        request_id: request_id.clone(),
        agent_run_id: agent_run_id.clone(),
    }
}

#[test]
fn root_record_dir_is_request_rooted() {
    let request_id: RequestId = id("req-1");
    let agent_run_id: AgentRunId = id("run-1");

    let dir = format_record_dir(&index(&request_id, &agent_run_id));

    assert_eq!(dir.as_str(), "requests/req-1/agent-runs/agent-run-run-1");
}

#[test]
fn workflow_record_dirs_are_flat_agent_run_dirs() {
    let request_id: RequestId = id("req-workflow");
    for agent_run_id in ["run-plan", "run-work"] {
        let agent_run_id = id::<AgentRunId>(agent_run_id);
        let dir = format_record_dir(&index(&request_id, &agent_run_id));

        assert_eq!(
            dir.as_str(),
            format!(
                "requests/req-workflow/agent-runs/agent-run-{}",
                agent_run_id.as_str()
            )
        );
    }
}

#[test]
fn parented_record_dirs_use_request_or_parent_root() {
    let request_id: RequestId = id("req-1");
    let subagent_id: AgentRunId = id("subagent-run");
    let advisor_id: AgentRunId = id("advisor-run");

    let subagent = format_record_dir(&index(&request_id, &subagent_id));
    assert_eq!(
        subagent.as_str(),
        "requests/req-1/agent-runs/agent-run-subagent-run"
    );

    let advisor = format_record_dir(&index(&request_id, &advisor_id));
    assert_eq!(
        advisor.as_str(),
        "requests/req-1/agent-runs/agent-run-advisor-run"
    );
}
