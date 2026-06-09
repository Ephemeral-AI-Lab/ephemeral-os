#![allow(clippy::unwrap_used)]

use std::num::NonZeroU32;

use super::*;

fn def(name: &str, agent_type: AgentType) -> AgentDefinition {
    AgentDefinition {
        name: AgentName::new(name).unwrap(),
        description: "d".to_owned(),
        system_prompt: None,
        model: None,
        tool_call_limit: NonZeroU32::new(10).unwrap(),
        agent_type,
        allowed_tools: vec![],
        terminals: vec!["submit_x".to_owned()],
        notification_triggers: vec![],
        context_recipe: None,
    }
}

#[test]
fn agent_type_serde_values() {
    let main = serde_json::to_value(AgentType::Main).unwrap();
    assert_eq!(main, serde_json::json!("main"));
    let planner = serde_json::to_value(AgentType::Planner).unwrap();
    assert_eq!(planner, serde_json::json!("planner"));
    let worker = serde_json::to_value(AgentType::Worker).unwrap();
    assert_eq!(worker, serde_json::json!("worker"));
    let subagent = serde_json::to_value(AgentType::Subagent).unwrap();
    assert_eq!(subagent, serde_json::json!("subagent"));
    let advisor = serde_json::to_value(AgentType::Advisor).unwrap();
    assert_eq!(advisor, serde_json::json!("advisor"));
}

#[test]
fn agent_name_trims_and_rejects_empty() {
    assert_eq!(AgentName::new("  root  ").unwrap().as_str(), "root");
    assert!(matches!(AgentName::new("   "), Err(AgentNameError::Empty)));

    let deserialized: AgentName = serde_json::from_value(serde_json::json!("  root  "))
        .expect("agent name serde trims like the constructor");
    assert_eq!(deserialized.as_str(), "root");
    assert!(serde_json::from_value::<AgentName>(serde_json::json!("   ")).is_err());
}

#[test]
fn registry_lists_dispatchable_subagents() {
    let registry: AgentRegistry = [
        def("zeta", AgentType::Subagent),
        def("root", AgentType::Main),
        def("alpha", AgentType::Subagent),
    ]
    .into_iter()
    .collect();

    let names: Vec<String> = registry
        .dispatchable_subagent_names()
        .iter()
        .map(|name| name.as_str().to_owned())
        .collect();
    assert_eq!(names, vec!["alpha".to_owned(), "zeta".to_owned()]);
}

#[test]
fn registry_get_and_replace() {
    let mut builder = AgentRegistryBuilder::new();
    builder.add(def("root", AgentType::Main));
    builder.add(def("root", AgentType::Main));
    let registry = builder.build();
    assert!(registry.get(&AgentName::new("root").unwrap()).is_some());
    assert_eq!(registry.list().count(), 1);
    assert!(registry.get(&AgentName::new("absent").unwrap()).is_none());
}
