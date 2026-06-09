use std::num::NonZeroU32;

use eos_types::{AgentName, AgentType};

use super::*;

fn agent_def(terminals: Vec<&str>) -> AgentDefinition {
    AgentDefinition {
        name: AgentName::new("coder").expect("agent name"),
        description: "coder".to_owned(),
        system_prompt: None,
        model: None,
        tool_call_limit: NonZeroU32::new(8).expect("nonzero"),
        agent_type: AgentType::Agent,
        allowed_tools: Vec::new(),
        terminals: terminals.into_iter().map(ToOwned::to_owned).collect(),
        notification_triggers: Vec::new(),
        skill: None,
        context_recipe: Some("generator".to_owned()),
    }
}

#[test]
fn terminal_selection_uses_terminal_catalog_format() {
    let terminal = ToolName::SubmitGeneratorOutcome;
    let expected_catalog =
        render_tool_instruction(&[terminal], ToolInstructions::SelectionGuidance);

    let block =
        terminal_selection_block(&agent_def(vec![terminal.as_str()])).expect("terminal block");

    assert_eq!(
        block,
        format!("<terminal_tool_selection>\n{expected_catalog}\n</terminal_tool_selection>")
    );
    assert!(!block.contains("Pick exactly one"));
}

fn engine() -> ContextEngine {
    let stores = Arc::new(crate::support::MemoryStores::default());
    ContextEngine::new(crate::context::ContextEngineStores {
        workflow_store: stores.clone(),
        iteration_store: stores.clone(),
        attempt_store: stores.clone(),
        task_store: stores,
    })
}

#[test]
fn strip_frontmatter_removes_yaml_block_and_passes_plain_through() {
    assert_eq!(strip_frontmatter("---\nname: x\n---\nBODY").trim(), "BODY");
    // No frontmatter fence -> returned unchanged.
    assert_eq!(strip_frontmatter("plain body"), "plain body");
    // An opening fence with no closing fence -> returned unchanged.
    assert_eq!(strip_frontmatter("---\nunterminated"), "---\nunterminated");
}

#[test]
fn build_skill_message_reads_strips_frontmatter_and_derives_name() {
    let root = std::env::temp_dir().join(format!("eos-skill-{}", std::process::id()));
    let skill_dir = root.join("my-skill");
    std::fs::create_dir_all(&skill_dir).expect("mkdir skill dir");
    let skill_file = skill_dir.join("SKILL.md");
    std::fs::write(&skill_file, "---\nname: ignored\n---\nDo the thing.").expect("write skill");

    let mut def = agent_def(vec![]);
    def.skill = Some(skill_file);
    let message = build_skill_message(&def)
        .expect("build skill message")
        .expect("a skill message");
    assert!(message.contains("Load skill: my-skill"), "{message}");
    assert!(message.contains("<skill>"));
    assert!(message.contains("Do the thing."));
    assert!(message.contains("</skill>"));
    assert!(
        !message.contains("name: ignored"),
        "frontmatter is stripped from the skill body"
    );

    // No skill declared -> None.
    assert!(build_skill_message(&agent_def(vec![]))
        .expect("no skill is ok")
        .is_none());

    // A declared-but-missing skill file -> error.
    let mut missing = agent_def(vec![]);
    missing.skill = Some(root.join("absent").join("SKILL.md"));
    assert!(build_skill_message(&missing).is_err());

    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn wrap_task_guidance_wraps_body_and_appends_terminal_block_when_present() {
    let plain = wrap_task_guidance("BODY", &agent_def(vec![]));
    assert!(plain.starts_with("<Task Guidance>"));
    assert!(plain.contains("BODY"));
    assert!(plain.ends_with("</Task Guidance>"));
    assert!(
        !plain.contains("<terminal_tool_selection>"),
        "no terminals -> no terminal block"
    );

    let with_terminal = wrap_task_guidance(
        "BODY",
        &agent_def(vec![ToolName::SubmitGeneratorOutcome.as_str()]),
    );
    assert!(with_terminal.contains("<terminal_tool_selection>"));
}

#[tokio::test]
async fn compose_rejects_missing_agent_and_missing_recipe() {
    let scope = ContextScope::for_planner(
        eos_types::WorkflowId::new_v4(),
        eos_types::IterationId::new_v4(),
        eos_types::AttemptId::new_v4(),
    );

    // Agent absent from the registry -> error (before the engine is touched).
    let empty = AgentEntryComposer::new(
        engine(),
        Arc::new(AgentRegistry::from_iter(Vec::<AgentDefinition>::new())),
    );
    assert!(empty.compose("ghost", &scope).await.is_err());

    // Agent present but with no context_recipe -> error.
    let mut no_recipe = agent_def(vec![]);
    no_recipe.name = AgentName::new("solo").expect("name");
    no_recipe.context_recipe = None;
    let composer =
        AgentEntryComposer::new(engine(), Arc::new(AgentRegistry::from_iter([no_recipe])));
    assert!(composer.compose("solo", &scope).await.is_err());
}
