use super::*;

#[test]
fn peer_isolation_rule_builds_drop_verdict() {
    let expressions = nft_peer_isolation_rule_exprs().expect("peer isolation rule");

    assert!(expressions.len() > 8);
    let verdict = expressions.last().expect("drop verdict expression");
    assert!(String::from_utf8_lossy(verdict).contains("immediate"));
}
