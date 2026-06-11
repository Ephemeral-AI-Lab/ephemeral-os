use super::*;

#[test]
fn request_messages_round_trip_byte_stable() {
    let raw = b"{\"op\":\"plugin.lsp.query\",\"invocation_id\":\"m1\",\"args\":{\"direction\":\"request\",\"body\":\"{}\"}}\n";
    let message = decode(raw).expect("decode");
    assert!(matches!(message, Message::Request(_)));
    assert_eq!(encode(&message).expect("encode"), raw);
}

#[test]
fn non_request_objects_decode_as_other() {
    let message = decode(b"{\"success\":true}\n").expect("decode");
    assert!(matches!(message, Message::Other(_)));
    assert!(decode(b"[1,2]\n").is_err());
}
