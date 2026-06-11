use super::*;

#[test]
fn request_frames_round_trip_byte_stable() {
    let raw = b"{\"op\":\"plugin.lsp.query\",\"invocation_id\":\"m1\",\"args\":{\"direction\":\"request\",\"body\":\"{}\"}}\n";
    let envelope = decode(raw).expect("decode");
    assert!(matches!(envelope, Envelope::Request(_)));
    assert_eq!(encode(&envelope).expect("encode"), raw);
}

#[test]
fn non_request_objects_decode_as_other() {
    let envelope = decode(b"{\"success\":true}\n").expect("decode");
    assert!(matches!(envelope, Envelope::Other(_)));
    assert!(decode(b"[1,2]\n").is_err());
}
