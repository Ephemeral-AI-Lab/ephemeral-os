use std::path::Path;

#[test]
fn generated_console_bindings_are_current() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("workspace root")
        .join(xtask::console_api::BINDINGS_RELATIVE_PATH);
    let expected = xtask::console_api::rendered_bindings().expect("render bindings");
    let current = std::fs::read_to_string(&path)
        .unwrap_or_else(|_| panic!("missing generated bindings at {}", path.display()));
    assert_eq!(
        current, expected,
        "stale console bindings; run `cargo run -p xtask -- gen-console-api`"
    );
}
