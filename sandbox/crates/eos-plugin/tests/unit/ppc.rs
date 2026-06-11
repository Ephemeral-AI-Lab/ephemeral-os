use super::*;

type TestResult = std::result::Result<(), Box<dyn std::error::Error + Send + Sync>>;

#[test]
fn ppc_envelope_round_trips_through_protocol_framing() -> TestResult {
    let envelope = PpcEnvelope {
        message_id: "msg-1".to_owned(),
        direction: PpcDirection::Request,
        op: "plugin.generic.hover".to_owned(),
        body: r#"{"path":"main.py"}"#.to_owned(),
    };

    let encoded = envelope.encode()?;
    assert!(encoded.ends_with(b"\n"));
    let decoded = PpcEnvelope::decode(&encoded)?;

    assert_eq!(decoded, envelope);
    Ok(())
}

#[test]
fn ppc_decode_rejects_non_request_frames() -> TestResult {
    let encoded = encode(&Envelope::Other(json!({"success": true})))?;

    assert!(matches!(
        PpcEnvelope::decode(&encoded),
        Err(PluginError::Ppc(message)) if message.contains("request envelope")
    ));
    Ok(())
}

#[test]
fn ppc_decode_rejects_unknown_direction() -> TestResult {
    let encoded = encode(&Envelope::Request(Request {
        op: "plugin.generic.hover".to_owned(),
        invocation_id: "msg-1".to_owned(),
        args: json!({"direction": "sideways", "body": "{}"}),
    }))?;

    assert!(matches!(
        PpcEnvelope::decode(&encoded),
        Err(PluginError::Ppc(message)) if message.contains("unknown ppc direction")
    ));
    Ok(())
}
