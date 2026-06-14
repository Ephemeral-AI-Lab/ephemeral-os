use super::RunnerVerb;

#[test]
fn runner_verb_preserves_wire_strings_and_unknowns() {
    assert_eq!(
        serde_json::to_value(&RunnerVerb::ExecCommand).expect("serialize"),
        serde_json::json!("exec_command")
    );
    assert_eq!(
        serde_json::from_value::<RunnerVerb>(serde_json::json!("plugin_service"))
            .expect("deserialize"),
        RunnerVerb::PluginService
    );
    assert_eq!(
        serde_json::from_value::<RunnerVerb>(serde_json::json!("plugin_setup"))
            .expect("deserialize"),
        RunnerVerb::PluginSetup
    );
    assert_eq!(
        serde_json::from_value::<RunnerVerb>(serde_json::json!("future_verb"))
            .expect("deserialize"),
        RunnerVerb::Unknown("future_verb".to_owned())
    );
}
