#![allow(clippy::unwrap_used)]

use std::sync::Arc;

use eos_types::JsonObject;
use serde_json::{json, Value};

use crate::support::{metadata, FakeTransport};
use crate::tools::{CallerScope, SandboxToolService, SkillToolService};
use eos_tool_ports::{ToolName, ToolRegistry};

fn obj(pairs: &[(&str, Value)]) -> JsonObject {
    pairs
        .iter()
        .map(|(key, value)| ((*key).to_owned(), value.clone()))
        .collect()
}

fn registry() -> ToolRegistry {
    crate::tools::build_default_registry_with_services(
        &crate::tools::repo_tools_config(),
        &CallerScope::default(),
        SandboxToolService::new(Arc::new(FakeTransport::inert())),
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        SkillToolService::new(Arc::new(crate::SkillRegistry::new())),
    )
}

async fn execute(name: ToolName, input: JsonObject) -> eos_tool_ports::ToolResult {
    let registry = registry();
    registry
        .get(name)
        .expect("registered")
        .executor()
        .execute(&input, &metadata())
        .await
        .expect("tool execution")
}

#[tokio::test]
async fn submit_advisor_feedback_accepts_approve_and_reject() {
    for (verdict, summary) in [
        ("approve", "looks correct"),
        ("reject", "needs more evidence"),
    ] {
        let res = execute(
            ToolName::SubmitAdvisorFeedback,
            obj(&[("verdict", json!(verdict)), ("summary", json!(summary))]),
        )
        .await;

        assert!(!res.is_error, "{res:?}");
        assert_eq!(res.output, summary);
        assert_eq!(res.metadata["helper_role"], json!("advisor"));
        assert_eq!(res.metadata["verdict"], json!(verdict));
    }
}

#[tokio::test]
async fn submit_advisor_feedback_rejects_blank_summary() {
    let res = execute(
        ToolName::SubmitAdvisorFeedback,
        obj(&[("verdict", json!("approve")), ("summary", json!("   "))]),
    )
    .await;

    assert!(res.is_error);
    assert_eq!(res.output, "summary must be nonblank");
}

#[tokio::test]
async fn submit_subagent_result_preserves_findings_and_references() {
    let res = execute(
        ToolName::SubmitSubagentResult,
        obj(&[
            ("summary", json!("found the issue")),
            ("findings", json!(["src/lib.rs:10", "src/main.rs:22"])),
            ("references", json!(["design.md#tooling"])),
        ]),
    )
    .await;

    assert!(!res.is_error, "{res:?}");
    assert_eq!(res.output, "found the issue");
    assert_eq!(res.metadata["agent_type"], json!("subagent"));
    assert_eq!(
        res.metadata["findings"],
        json!(["src/lib.rs:10", "src/main.rs:22"])
    );
    assert_eq!(res.metadata["references"], json!(["design.md#tooling"]));
}

#[tokio::test]
async fn submit_subagent_result_rejects_blank_summary() {
    let res = execute(
        ToolName::SubmitSubagentResult,
        obj(&[("summary", json!(" "))]),
    )
    .await;

    assert!(res.is_error);
    assert_eq!(res.output, "summary must be nonblank");
}
