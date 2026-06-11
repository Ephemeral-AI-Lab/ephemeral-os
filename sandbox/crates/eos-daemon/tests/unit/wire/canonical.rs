use super::*;

#[test]
fn drops_timings_pid_uptime_and_sorts_keys() {
    let a = serde_json::json!({
        "b": 2, "a": 1,
        "timings": {"x": 0.1},
        "daemon_pid": 1234,
        "uptime_s": 3.5,
        "nested": {"timings": {"y": 9.0}, "k": "v"}
    });
    let b = serde_json::json!({
        "a": 1, "b": 2,
        "timings": {"x": 999.9},
        "daemon_pid": 4321,
        "uptime_s": 88.0,
        "nested": {"k": "v", "timings": {"y": 0.0}}
    });
    assert_eq!(canonicalize(&a), canonicalize(&b));
}

#[test]
fn integers_preserved() {
    let v = serde_json::json!({"n": 0});
    assert_eq!(canonicalize(&v), serde_json::json!({"n": 0}));
}
