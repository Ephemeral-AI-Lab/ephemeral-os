//! File wrapper tests for the public sandbox tool API.
#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Mutex;

use async_trait::async_trait;
use eos_sandbox_port::{
    edit_file, read_file, write_file, DaemonOp, EditFileRequest, ReadFileRequest, SandboxPortError,
    SandboxRequestBase, SandboxTransport, SearchReplaceEdit, WriteFileRequest, EDIT_FILE_TIMEOUT_S,
    READ_FILE_TIMEOUT_S, WRITE_FILE_TIMEOUT_S,
};
use eos_types::{JsonObject, SandboxId};
use serde_json::{json, Value};

#[derive(Debug, Clone, PartialEq)]
struct RecordedCall {
    sandbox_id: SandboxId,
    op: DaemonOp,
    payload: JsonObject,
    timeout_s: u32,
}

#[derive(Debug)]
struct FileToolApiTestTransport {
    response: Result<JsonObject, SandboxPortError>,
    calls: Mutex<Vec<RecordedCall>>,
}

impl FileToolApiTestTransport {
    fn ok(response: Value) -> Self {
        Self {
            response: Ok(obj(response)),
            calls: Mutex::new(Vec::new()),
        }
    }

    fn err(error: SandboxPortError) -> Self {
        Self {
            response: Err(error),
            calls: Mutex::new(Vec::new()),
        }
    }

    fn calls(&self) -> Vec<RecordedCall> {
        self.calls.lock().unwrap().clone()
    }
}

#[async_trait]
impl SandboxTransport for FileToolApiTestTransport {
    async fn call(
        &self,
        sandbox_id: &SandboxId,
        op: DaemonOp,
        payload: JsonObject,
        timeout_s: u32,
    ) -> Result<JsonObject, SandboxPortError> {
        self.calls.lock().unwrap().push(RecordedCall {
            sandbox_id: sandbox_id.clone(),
            op,
            payload,
            timeout_s,
        });
        self.response.clone()
    }
}

fn obj(value: Value) -> JsonObject {
    match value {
        Value::Object(map) => map,
        _ => panic!("test value is not an object"),
    }
}

fn base(description: &str) -> SandboxRequestBase {
    SandboxRequestBase::new(
        "agent-1",
        description,
        Some("inv-file".parse().expect("invocation id")),
    )
}

fn sandbox_id() -> SandboxId {
    "sandbox-file".parse().expect("sandbox id")
}

#[tokio::test]
async fn read_file_builds_identity_and_line_range_payload() {
    let transport = FileToolApiTestTransport::ok(json!({
        "success": true,
        "exists": true,
        "content": "hello",
        "encoding": "utf-8",
    }));
    let request = ReadFileRequest {
        base: base("read custom"),
        path: "src/lib.rs".to_owned(),
    };

    let result = read_file(&transport, &sandbox_id(), &request)
        .await
        .expect("read file");

    assert!(result.base.success);
    assert!(result.exists);
    assert_eq!(result.content, "hello");
    let calls = transport.calls();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].op, DaemonOp::ReadFile);
    assert_eq!(calls[0].timeout_s, READ_FILE_TIMEOUT_S);
    assert_eq!(calls[0].payload["caller_id"], json!("agent-1"));
    assert_eq!(calls[0].payload["invocation_id"], json!("inv-file"));
    assert_eq!(calls[0].payload["path"], json!("src/lib.rs"));
    assert!(
        !calls[0].payload.contains_key("line_range"),
        "the current ReadFileRequest DTO has no line-range field"
    );
}

#[tokio::test]
async fn write_file_builds_payload_and_parses_guarded_result() {
    let transport = FileToolApiTestTransport::ok(json!({
        "success": true,
        "changed_paths": ["a.txt", ""],
        "changed_path_kinds": {"a.txt": "modified", "": "ignored"},
        "mutation_source": "overlay",
        "status": "ok",
    }));
    let request = WriteFileRequest {
        base: base(""),
        path: "a.txt".to_owned(),
        content: "new".to_owned(),
        overwrite: false,
    };

    let result = write_file(&transport, &sandbox_id(), &request)
        .await
        .expect("write file");

    assert!(result.base.success);
    assert_eq!(result.base.changed_paths, vec!["a.txt"]);
    assert_eq!(
        result.changed_path_kinds.get("a.txt"),
        Some(&"modified".to_owned())
    );
    assert_eq!(result.mutation_source, "overlay");
    let calls = transport.calls();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].op, DaemonOp::WriteFile);
    assert_eq!(calls[0].timeout_s, WRITE_FILE_TIMEOUT_S);
    assert_eq!(calls[0].payload["path"], json!("a.txt"));
    assert_eq!(calls[0].payload["content"], json!("new"));
    assert_eq!(calls[0].payload["description"], json!("write a.txt"));
    assert_eq!(calls[0].payload["overwrite"], json!(false));
}

#[tokio::test]
async fn edit_file_builds_payload_and_maps_conflict_to_ok_result() {
    let transport = FileToolApiTestTransport::err(SandboxPortError::transport(
        Some("aborted_overlap".to_owned()),
        "internal_error: anchor not found",
    ));
    let request = EditFileRequest {
        base: base("edit custom"),
        path: "a.txt".to_owned(),
        edits: vec![SearchReplaceEdit {
            old_text: "old".to_owned(),
            new_text: "new".to_owned(),
            replace_all: true,
        }],
    };

    let result = edit_file(&transport, &sandbox_id(), &request)
        .await
        .expect("conflict maps to result");

    assert!(!result.base.success);
    assert_eq!(result.status, "aborted_overlap");
    assert_eq!(result.base.changed_paths, vec!["a.txt"]);
    assert_eq!(
        result.base.conflict_reason.as_deref(),
        Some("anchor not found")
    );
    let conflict = result.base.conflict.expect("conflict");
    assert_eq!(conflict.conflict_file.as_deref(), Some("a.txt"));
    let calls = transport.calls();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].op, DaemonOp::EditFile);
    assert_eq!(calls[0].timeout_s, EDIT_FILE_TIMEOUT_S);
    assert_eq!(calls[0].payload["path"], json!("a.txt"));
    assert_eq!(calls[0].payload["description"], json!("edit custom"));
    assert_eq!(
        calls[0].payload["edits"],
        json!([{"old_text": "old", "new_text": "new", "replace_all": true}])
    );
}
