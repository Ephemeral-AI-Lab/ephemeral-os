#[test]
fn prd_gateway_section_deserializes_and_validates() {
    let doc = crate::load_prd().expect("prd config loads");

    GatewayConfig::from_document(&doc).expect("gateway section deserializes");
}
