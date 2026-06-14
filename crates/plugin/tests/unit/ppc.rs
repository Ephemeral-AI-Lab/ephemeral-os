use super::*;

type TestResult = std::result::Result<(), Box<dyn std::error::Error + Send + Sync>>;

#[test]
fn ppc_message_round_trips_through_wire_codec() -> TestResult {
    let message = PpcMessage {
        message_id: "msg-1".to_owned(),
        parent_message_id: None,
        direction: PpcDirection::Request,
        op: "plugin.generic.hover".to_owned(),
        body: r#"{"path":"main.py"}"#.to_owned(),
    };

    let encoded = message.encode()?;
    assert!(encoded.ends_with(b"\n"));
    let decoded = PpcMessage::decode(&encoded)?;

    assert_eq!(decoded, message);
    Ok(())
}

#[test]
fn ppc_message_round_trips_typed_parent_message_id() -> TestResult {
    let message = PpcMessage {
        message_id: "callback-1".to_owned(),
        parent_message_id: Some("msg-1".to_owned()),
        direction: PpcDirection::Request,
        op: "daemon.occ.apply_changeset".to_owned(),
        body: r#"{"changes":[]}"#.to_owned(),
    };

    let decoded = PpcMessage::decode(&message.encode()?)?;

    assert_eq!(decoded.parent_message_id.as_deref(), Some("msg-1"));
    assert_eq!(decoded, message);
    Ok(())
}

#[test]
fn ppc_decode_rejects_non_request_messages() -> TestResult {
    let encoded = encode(&Message::Other(json!({"success": true})))?;

    assert!(matches!(
        PpcMessage::decode(&encoded),
        Err(PluginError::Ppc(message)) if message.contains("request message")
    ));
    Ok(())
}

#[test]
fn ppc_decode_rejects_unknown_direction() -> TestResult {
    let encoded = encode(&Message::Request(RequestMessage {
        op: "plugin.generic.hover".to_owned(),
        invocation_id: "msg-1".to_owned(),
        args: json!({"direction": "sideways", "body": "{}"}),
    }))?;

    assert!(matches!(
        PpcMessage::decode(&encoded),
        Err(PluginError::Ppc(message)) if message.contains("unknown ppc direction")
    ));
    Ok(())
}
