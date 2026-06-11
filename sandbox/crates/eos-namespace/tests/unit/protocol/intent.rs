use super::*;
use serde_json::Value;

type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[test]
fn intent_wire_values() -> TestResult {
    assert_eq!(
        serde_json::to_value(Intent::ReadOnly)?,
        Value::String("read_only".to_owned())
    );
    assert_eq!(
        serde_json::to_value(Intent::WriteAllowed)?,
        Value::String("write_allowed".to_owned())
    );
    assert_eq!(
        serde_json::to_value(Intent::Lifecycle)?,
        Value::String("lifecycle".to_owned())
    );
    Ok(())
}
