// AC-eos-agent-def-10: loading the real bundled `agents/profile/` tree (root,
// planner, executor, reducer, explorer, advisor) succeeds and the `executor`
// profile resolves to `role == AgentRole::Generator`.
//
// This reads the live Python profile tree (read-only) so the Rust loader is
// validated against the same source the runtime consumes. The path is relative
// to this crate's manifest: agent-core sits at the repo root, so three `..`
// segments reach `backend/src/agents/profile`.
#![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)

use std::path::PathBuf;

use eos_agent_def::{load_agents_tree, AgentName, AgentRegistry, AgentRole, AgentType};

fn profile_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../backend/src/agents/profile")
}

#[test]
fn loads_bundled_profiles() {
    let dir = profile_dir();
    assert!(
        dir.is_dir(),
        "bundled profile tree not found at {}",
        dir.display()
    );

    let definitions = load_agents_tree(&dir).expect("load bundled profiles");
    let registry: AgentRegistry = definitions.into_iter().collect();

    // The six bundled profiles all load.
    for name in [
        "root", "planner", "executor", "reducer", "advisor", "explorer",
    ] {
        let key = AgentName::new(name).expect("non-empty name");
        assert!(
            registry.get(&key).is_some(),
            "missing bundled profile {name}"
        );
    }

    // `executor` is a profile-name alias carrying role: generator.
    let executor = registry
        .get(&AgentName::new("executor").unwrap())
        .expect("executor present");
    assert_eq!(executor.role, AgentRole::Generator);
    assert_eq!(executor.agent_type, AgentType::Agent);
    // executor declares a skill; the loader resolved it to an absolute file.
    let skill = executor.skill.as_ref().expect("executor skill resolved");
    assert!(skill.is_absolute());

    // The explorer is the only dispatchable subagent in the bundled set.
    let dispatchable: Vec<String> = registry
        .dispatchable_subagent_names()
        .iter()
        .map(|n| n.as_str().to_owned())
        .collect();
    assert_eq!(dispatchable, vec!["explorer".to_owned()]);

    // The `_main_role_contract.md` private include was skipped, and a main/
    // profile carries the prepended contract.
    let root = registry.get(&AgentName::new("root").unwrap()).unwrap();
    assert!(
        root.system_prompt
            .as_deref()
            .is_some_and(|p| p.contains("Main-Agent Operating Contract")),
        "root should have the main-role contract prepended"
    );
}
