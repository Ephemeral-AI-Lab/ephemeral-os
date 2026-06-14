use std::io::{BufRead, Write};

use serde_json::Value;

pub(super) fn read_lsp_message(
    reader: &mut impl BufRead,
    max_response_bytes: usize,
) -> Result<Option<Value>, String> {
    let mut content_length = None;
    loop {
        let mut line = String::new();
        let read = reader.read_line(&mut line).map_err(|err| err.to_string())?;
        if read == 0 {
            return Ok(None);
        }
        let line = line.trim_end_matches(['\r', '\n']);
        if line.is_empty() {
            break;
        }
        if let Some(value) = line.strip_prefix("Content-Length:") {
            let length = value
                .trim()
                .parse::<usize>()
                .map_err(|err| format!("invalid LSP Content-Length: {err}"))?;
            content_length = Some(length);
        }
    }
    let content_length = content_length.ok_or_else(|| "missing LSP Content-Length".to_owned())?;
    if content_length > max_response_bytes {
        return Err(format!(
            "pyright_lsp response exceeds {} byte limit",
            max_response_bytes
        ));
    }
    let mut body = vec![0_u8; content_length];
    reader
        .read_exact(&mut body)
        .map_err(|err| err.to_string())?;
    serde_json::from_slice(&body).map_err(|err| err.to_string())
}

pub(super) fn write_lsp_message(writer: &mut impl Write, message: &Value) -> std::io::Result<()> {
    let body = serde_json::to_vec(message).map_err(std::io::Error::other)?;
    write!(writer, "Content-Length: {}\r\n\r\n", body.len())?;
    writer.write_all(&body)?;
    writer.flush()
}

pub(super) fn lsp_id_key(id: &Value) -> String {
    match id {
        Value::String(value) => value.clone(),
        Value::Number(value) => value.to_string(),
        _ => id.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use std::io::{BufReader, Cursor};

    use serde_json::{json, Value};

    use super::*;

    fn framed_message(value: &Value) -> Vec<u8> {
        let body = serde_json::to_vec(value).expect("message serializes");
        format!("Content-Length: {}\r\n\r\n", body.len())
            .into_bytes()
            .into_iter()
            .chain(body)
            .collect()
    }

    #[test]
    fn read_lsp_message_parses_response_frame() {
        let expected = json!({"jsonrpc": "2.0", "id": 7, "result": {"ok": true}});
        let bytes = framed_message(&expected);
        let mut reader = BufReader::new(Cursor::new(bytes));

        let parsed = read_lsp_message(&mut reader, 1024)
            .expect("frame parses")
            .expect("message exists");

        assert_eq!(parsed, expected);
    }

    #[test]
    fn read_lsp_message_rejects_oversized_response() {
        let expected = json!({"jsonrpc": "2.0", "id": 7, "result": {"ok": true}});
        let bytes = framed_message(&expected);
        let mut reader = BufReader::new(Cursor::new(bytes));

        let error = read_lsp_message(&mut reader, 1).expect_err("oversized frame should fail");

        assert!(error.contains("response exceeds 1 byte limit"), "{error}");
    }

    #[test]
    fn read_lsp_message_returns_none_on_eof() {
        let mut reader = BufReader::new(Cursor::new(Vec::new()));

        let parsed = read_lsp_message(&mut reader, 1024).expect("eof is not malformed");

        assert_eq!(parsed, None);
    }
}
