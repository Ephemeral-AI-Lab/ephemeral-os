use serde::Serialize;
use serde_json::Value;

pub(crate) fn encode_json_line(value: &Value) -> Result<Vec<u8>, serde_json::Error> {
    let mut line = serde_json::to_vec(value)?;
    push_json_line_delimiter(&mut line);
    Ok(line)
}

pub(crate) fn encode_serializable_json_line(
    value: &impl Serialize,
) -> Result<Vec<u8>, serde_json::Error> {
    let mut line = serde_json::to_vec(value)?;
    push_json_line_delimiter(&mut line);
    Ok(line)
}

fn push_json_line_delimiter(line: &mut Vec<u8>) {
    line.push(b'\n');
}
