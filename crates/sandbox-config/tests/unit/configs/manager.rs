#[test]
fn manager_config_currently_has_no_persistent_fields() {
    ManagerConfig
        .validate()
        .expect("empty manager config validates");
}
