use super::*;

type TestResult = std::result::Result<(), Box<dyn std::error::Error + Send + Sync>>;

#[test]
fn ppc_message_round_trips_through_wire_codec() -> TestResult {
    let message = PpcMessage {
        message_id: "msg-1".to_owned(),
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
