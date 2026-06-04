use std::net::Ipv4Addr;

use netlink_sys::{Socket as NlSocket, SocketAddr as NlSocketAddr};

use crate::caps::Rfc1918Egress;
use crate::error::IsolatedError;

use super::{BRIDGE_PREFIX_LEN, IMDS_ADDR, NFT_FILTER_TABLE, NFT_NAT_TABLE, RFC1918_NETS};

const NFT_BRIDGE_FILTER_TABLE: &str = "eos_iws_bridge_filter";

pub(super) fn install_static_rules(
    rfc1918_egress: Rfc1918Egress,
    bridge_index: u32,
) -> Result<(), IsolatedError> {
    add_nft_table(NFT_NAT_TABLE)?;
    add_nft_base_chain(
        NFT_NAT_TABLE,
        "postrouting",
        "nat",
        libc_c_int_to_u32(libc::NF_INET_POST_ROUTING, "NF_INET_POST_ROUTING")?,
        100,
    )?;
    add_nft_rule(
        NFT_NAT_TABLE,
        "postrouting",
        nft_masquerade_rule_exprs(bridge_index)?,
    )?;

    add_nft_table(NFT_FILTER_TABLE)?;
    add_nft_base_chain(
        NFT_FILTER_TABLE,
        "forward",
        "filter",
        libc_c_int_to_u32(libc::NF_INET_FORWARD, "NF_INET_FORWARD")?,
        0,
    )?;
    add_nft_rule(NFT_FILTER_TABLE, "forward", nft_imds_drop_rule_exprs()?)?;
    add_nft_rule(
        NFT_FILTER_TABLE,
        "forward",
        nft_peer_isolation_rule_exprs()?,
    )?;
    install_bridge_peer_isolation_rule()?;
    if rfc1918_egress == Rfc1918Egress::Deny {
        for cidr in RFC1918_NETS {
            add_nft_rule(
                NFT_FILTER_TABLE,
                "forward",
                nft_rfc1918_drop_rule_exprs(cidr)?,
            )?;
        }
    }
    Ok(())
}

fn install_bridge_peer_isolation_rule() -> Result<(), IsolatedError> {
    let family = libc_c_int_to_u8(libc::NFPROTO_BRIDGE, "NFPROTO_BRIDGE")?;
    add_nft_table_in_family(family, NFT_BRIDGE_FILTER_TABLE)?;
    add_nft_base_chain_in_family(
        family,
        NFT_BRIDGE_FILTER_TABLE,
        "forward",
        "filter",
        libc_c_int_to_u32(libc::NF_BR_FORWARD, "NF_BR_FORWARD")?,
        0,
    )?;
    add_nft_rule_in_family(
        family,
        NFT_BRIDGE_FILTER_TABLE,
        "forward",
        nft_bridge_peer_isolation_rule_exprs()?,
    )
}

fn add_nft_table(name: &str) -> Result<(), IsolatedError> {
    add_nft_table_in_family(libc_c_int_to_u8(libc::NFPROTO_INET, "NFPROTO_INET")?, name)
}

fn add_nft_table_in_family(family: u8, name: &str) -> Result<(), IsolatedError> {
    let mut attrs = Vec::new();
    append_cstr_attr(&mut attrs, NFTA_TABLE_NAME, name);
    append_be_u32_attr(&mut attrs, NFTA_TABLE_FLAGS, 0);
    send_nft_command(
        family,
        format!("create nft table {name}"),
        libc_c_int_to_u16(libc::NFT_MSG_NEWTABLE, "NFT_MSG_NEWTABLE")?,
        nft_create_flags(),
        &attrs,
        true,
    )
}

fn add_nft_base_chain(
    table: &str,
    name: &str,
    chain_type: &str,
    hook: u32,
    priority: i32,
) -> Result<(), IsolatedError> {
    add_nft_base_chain_in_family(
        libc_c_int_to_u8(libc::NFPROTO_INET, "NFPROTO_INET")?,
        table,
        name,
        chain_type,
        hook,
        priority,
    )
}

fn add_nft_base_chain_in_family(
    family: u8,
    table: &str,
    name: &str,
    chain_type: &str,
    hook: u32,
    priority: i32,
) -> Result<(), IsolatedError> {
    let mut hook_attrs = Vec::new();
    append_be_u32_attr(&mut hook_attrs, NFTA_HOOK_HOOKNUM, hook);
    append_be_i32_attr(&mut hook_attrs, NFTA_HOOK_PRIORITY, priority);

    let mut attrs = Vec::new();
    append_cstr_attr(&mut attrs, NFTA_CHAIN_TABLE, table);
    append_cstr_attr(&mut attrs, NFTA_CHAIN_NAME, name);
    append_cstr_attr(&mut attrs, NFTA_CHAIN_TYPE, chain_type);
    append_nested_attr(&mut attrs, NFTA_CHAIN_HOOK, &hook_attrs);
    send_nft_command(
        family,
        format!("create nft chain {table}/{name}"),
        libc_c_int_to_u16(libc::NFT_MSG_NEWCHAIN, "NFT_MSG_NEWCHAIN")?,
        nft_create_flags(),
        &attrs,
        true,
    )
}

fn add_nft_rule(table: &str, chain: &str, expressions: Vec<Vec<u8>>) -> Result<(), IsolatedError> {
    add_nft_rule_in_family(
        libc_c_int_to_u8(libc::NFPROTO_INET, "NFPROTO_INET")?,
        table,
        chain,
        expressions,
    )
}

fn add_nft_rule_in_family(
    family: u8,
    table: &str,
    chain: &str,
    expressions: Vec<Vec<u8>>,
) -> Result<(), IsolatedError> {
    let mut expression_list = Vec::new();
    for expression in expressions {
        append_nested_attr(&mut expression_list, NFTA_LIST_ELEM, &expression);
    }

    let mut attrs = Vec::new();
    append_cstr_attr(&mut attrs, NFTA_RULE_TABLE, table);
    append_cstr_attr(&mut attrs, NFTA_RULE_CHAIN, chain);
    append_nested_attr(&mut attrs, NFTA_RULE_EXPRESSIONS, &expression_list);
    send_nft_command(
        family,
        format!("add nft rule {table}/{chain}"),
        libc_c_int_to_u16(libc::NFT_MSG_NEWRULE, "NFT_MSG_NEWRULE")?,
        nft_rule_flags(),
        &attrs,
        true,
    )
}

fn nft_masquerade_rule_exprs(bridge_index: u32) -> Result<Vec<Vec<u8>>, IsolatedError> {
    let (bridge_net, bridge_prefix) = bridge_network();
    let mut expressions =
        nft_ipv4_network_match(IPV4_SADDR_OFFSET, bridge_net, bridge_prefix, NFT_CMP_EQ)?;
    expressions.push(nft_meta_expr(libc_c_int_to_u32(
        libc::NFT_META_OIF,
        "NFT_META_OIF",
    )?));
    expressions.push(nft_cmp_expr(
        NFT_CMP_NEQ,
        bridge_index.to_ne_bytes().as_slice(),
    ));
    expressions.push(nft_expr("masq", &[]));
    Ok(expressions)
}

fn nft_imds_drop_rule_exprs() -> Result<Vec<Vec<u8>>, IsolatedError> {
    let imds = parse_ipv4_addr(IMDS_ADDR)?;
    let mut expressions = nft_ipv4_guard_exprs()?;
    expressions.push(nft_payload_ipv4_expr(IPV4_DADDR_OFFSET)?);
    expressions.push(nft_cmp_expr(NFT_CMP_EQ, &imds.octets()));
    expressions.push(nft_drop_expr()?);
    Ok(expressions)
}

fn nft_peer_isolation_rule_exprs() -> Result<Vec<Vec<u8>>, IsolatedError> {
    let gateway = gateway_addr();
    let (bridge_net, bridge_prefix) = bridge_network();
    let mut expressions =
        nft_ipv4_network_match(IPV4_SADDR_OFFSET, bridge_net, bridge_prefix, NFT_CMP_EQ)?;
    expressions.extend(nft_ipv4_addr_match(
        IPV4_SADDR_OFFSET,
        gateway,
        NFT_CMP_NEQ,
    )?);
    expressions.extend(nft_ipv4_network_match(
        IPV4_DADDR_OFFSET,
        bridge_net,
        bridge_prefix,
        NFT_CMP_EQ,
    )?);
    expressions.extend(nft_ipv4_addr_match(
        IPV4_DADDR_OFFSET,
        gateway,
        NFT_CMP_NEQ,
    )?);
    expressions.push(nft_drop_expr()?);
    Ok(expressions)
}

fn nft_bridge_peer_isolation_rule_exprs() -> Result<Vec<Vec<u8>>, IsolatedError> {
    let gateway = gateway_addr();
    let (bridge_net, bridge_prefix) = bridge_network();
    let mut expressions =
        nft_bridge_ipv4_network_match(IPV4_SADDR_OFFSET, bridge_net, bridge_prefix, NFT_CMP_EQ)?;
    expressions.extend(nft_bridge_ipv4_addr_match(
        IPV4_SADDR_OFFSET,
        gateway,
        NFT_CMP_NEQ,
    )?);
    expressions.extend(nft_bridge_ipv4_network_match(
        IPV4_DADDR_OFFSET,
        bridge_net,
        bridge_prefix,
        NFT_CMP_EQ,
    )?);
    expressions.extend(nft_bridge_ipv4_addr_match(
        IPV4_DADDR_OFFSET,
        gateway,
        NFT_CMP_NEQ,
    )?);
    expressions.push(nft_drop_expr()?);
    Ok(expressions)
}

fn nft_rfc1918_drop_rule_exprs(cidr: &str) -> Result<Vec<Vec<u8>>, IsolatedError> {
    let (private_net, private_prefix) = parse_ipv4_cidr(cidr)?;
    let (bridge_net, bridge_prefix) = bridge_network();
    let mut expressions =
        nft_ipv4_network_match(IPV4_DADDR_OFFSET, private_net, private_prefix, NFT_CMP_EQ)?;
    expressions.extend(nft_ipv4_network_match(
        IPV4_DADDR_OFFSET,
        bridge_net,
        bridge_prefix,
        NFT_CMP_NEQ,
    )?);
    expressions.push(nft_drop_expr()?);
    Ok(expressions)
}

fn nft_ipv4_network_match(
    offset: u32,
    network: Ipv4Addr,
    prefix_len: u8,
    op: u32,
) -> Result<Vec<Vec<u8>>, IsolatedError> {
    let mut expressions = nft_ipv4_guard_exprs()?;
    expressions.push(nft_payload_ipv4_expr(offset)?);
    expressions.push(nft_bitwise_mask_expr(ipv4_mask(prefix_len)?));
    expressions.push(nft_cmp_expr(op, &network.octets()));
    Ok(expressions)
}

fn nft_bridge_ipv4_network_match(
    offset: u32,
    network: Ipv4Addr,
    prefix_len: u8,
    op: u32,
) -> Result<Vec<Vec<u8>>, IsolatedError> {
    let mut expressions = nft_bridge_ipv4_guard_exprs()?;
    expressions.push(nft_payload_ipv4_expr(offset)?);
    expressions.push(nft_bitwise_mask_expr(ipv4_mask(prefix_len)?));
    expressions.push(nft_cmp_expr(op, &network.octets()));
    Ok(expressions)
}

fn nft_ipv4_addr_match(
    offset: u32,
    address: Ipv4Addr,
    op: u32,
) -> Result<Vec<Vec<u8>>, IsolatedError> {
    let mut expressions = nft_ipv4_guard_exprs()?;
    expressions.push(nft_payload_ipv4_expr(offset)?);
    expressions.push(nft_cmp_expr(op, &address.octets()));
    Ok(expressions)
}

fn nft_bridge_ipv4_addr_match(
    offset: u32,
    address: Ipv4Addr,
    op: u32,
) -> Result<Vec<Vec<u8>>, IsolatedError> {
    let mut expressions = nft_bridge_ipv4_guard_exprs()?;
    expressions.push(nft_payload_ipv4_expr(offset)?);
    expressions.push(nft_cmp_expr(op, &address.octets()));
    Ok(expressions)
}

fn nft_bridge_ipv4_guard_exprs() -> Result<Vec<Vec<u8>>, IsolatedError> {
    let eth_p_ip = libc_c_int_to_u16(libc::ETH_P_IP, "ETH_P_IP")?;
    Ok(vec![
        nft_payload_expr(
            libc_c_int_to_u32(libc::NFT_PAYLOAD_LL_HEADER, "NFT_PAYLOAD_LL_HEADER")?,
            ETHER_TYPE_OFFSET,
            ETHER_TYPE_LEN,
        )?,
        nft_cmp_expr(NFT_CMP_EQ, &eth_p_ip.to_be_bytes()),
    ])
}

fn nft_ipv4_guard_exprs() -> Result<Vec<Vec<u8>>, IsolatedError> {
    let mut data = Vec::new();
    append_be_u32_attr(&mut data, NFTA_META_DREG, NFT_REG_1);
    append_be_u32_attr(
        &mut data,
        NFTA_META_KEY,
        libc_c_int_to_u32(libc::NFT_META_NFPROTO, "NFT_META_NFPROTO")?,
    );
    Ok(vec![
        nft_expr("meta", &data),
        nft_cmp_expr(
            NFT_CMP_EQ,
            &[libc_c_int_to_u8(libc::NFPROTO_IPV4, "NFPROTO_IPV4")?],
        ),
    ])
}

fn nft_payload_ipv4_expr(offset: u32) -> Result<Vec<u8>, IsolatedError> {
    nft_payload_expr(
        libc_c_int_to_u32(
            libc::NFT_PAYLOAD_NETWORK_HEADER,
            "NFT_PAYLOAD_NETWORK_HEADER",
        )?,
        offset,
        IPV4_ADDR_LEN,
    )
}

fn nft_payload_expr(base: u32, offset: u32, len: u32) -> Result<Vec<u8>, IsolatedError> {
    let mut data = Vec::new();
    append_be_u32_attr(&mut data, NFTA_PAYLOAD_DREG, NFT_REG_1);
    append_be_u32_attr(&mut data, NFTA_PAYLOAD_BASE, base);
    append_be_u32_attr(&mut data, NFTA_PAYLOAD_OFFSET, offset);
    append_be_u32_attr(&mut data, NFTA_PAYLOAD_LEN, len);
    Ok(nft_expr("payload", &data))
}

fn nft_meta_expr(key: u32) -> Vec<u8> {
    let mut data = Vec::new();
    append_be_u32_attr(&mut data, NFTA_META_DREG, NFT_REG_1);
    append_be_u32_attr(&mut data, NFTA_META_KEY, key);
    nft_expr("meta", &data)
}

fn nft_bitwise_mask_expr(mask: [u8; 4]) -> Vec<u8> {
    let mut data = Vec::new();
    append_be_u32_attr(&mut data, NFTA_BITWISE_SREG, NFT_REG_1);
    append_be_u32_attr(&mut data, NFTA_BITWISE_DREG, NFT_REG_1);
    append_be_u32_attr(&mut data, NFTA_BITWISE_LEN, IPV4_ADDR_LEN);
    append_data_value_attr(&mut data, NFTA_BITWISE_MASK, &mask);
    append_data_value_attr(&mut data, NFTA_BITWISE_XOR, &[0, 0, 0, 0]);
    nft_expr("bitwise", &data)
}

fn nft_cmp_expr(op: u32, value: &[u8]) -> Vec<u8> {
    let mut data = Vec::new();
    append_be_u32_attr(&mut data, NFTA_CMP_SREG, NFT_REG_1);
    append_be_u32_attr(&mut data, NFTA_CMP_OP, op);
    append_data_value_attr(&mut data, NFTA_CMP_DATA, value);
    nft_expr("cmp", &data)
}

fn nft_drop_expr() -> Result<Vec<u8>, IsolatedError> {
    let mut verdict = Vec::new();
    append_be_u32_attr(
        &mut verdict,
        NFTA_VERDICT_CODE,
        libc_c_int_to_u32(libc::NF_DROP, "NF_DROP")?,
    );

    let mut data_value = Vec::new();
    append_nested_attr(&mut data_value, NFTA_DATA_VERDICT, &verdict);

    let mut data = Vec::new();
    append_be_u32_attr(&mut data, NFTA_IMMEDIATE_DREG, NFT_REG_VERDICT);
    append_nested_attr(&mut data, NFTA_IMMEDIATE_DATA, &data_value);
    Ok(nft_expr("immediate", &data))
}

fn nft_expr(name: &str, data: &[u8]) -> Vec<u8> {
    let mut expression = Vec::new();
    append_cstr_attr(&mut expression, NFTA_EXPR_NAME, name);
    if !data.is_empty() {
        append_nested_attr(&mut expression, NFTA_EXPR_DATA, data);
    }
    expression
}

fn send_nft_command(
    family: u8,
    step: impl Into<String>,
    message_type: u16,
    flags: u16,
    attrs: &[u8],
    ignore_exists: bool,
) -> Result<(), IsolatedError> {
    let step = step.into();
    let batch_start_seq = 1;
    let operation_seq = 2;
    let batch_end_seq = 3;
    let mut message = nft_batch_message(
        libc_c_int_to_u16(libc::NFNL_MSG_BATCH_BEGIN, "NFNL_MSG_BATCH_BEGIN")?,
        batch_start_seq,
    )?;
    message.extend(nft_message(
        message_type,
        flags,
        operation_seq,
        family,
        attrs,
    )?);
    message.extend(nft_batch_message(
        libc_c_int_to_u16(libc::NFNL_MSG_BATCH_END, "NFNL_MSG_BATCH_END")?,
        batch_end_seq,
    )?);
    let mut socket = NlSocket::new(libc_c_int_to_isize(
        libc::NETLINK_NETFILTER,
        "NETLINK_NETFILTER",
    )?)
    .map_err(|err| network_error_at(step.as_str(), err))?;
    socket
        .bind_auto()
        .map_err(|err| network_error_at(step.as_str(), err))?;
    socket
        .connect(&NlSocketAddr::new(0, 0))
        .map_err(|err| network_error_at(step.as_str(), err))?;
    socket
        .send(&message, 0)
        .map_err(|err| network_error_at(step.as_str(), err))?;
    recv_nft_ack(
        &socket,
        operation_seq,
        batch_start_seq,
        batch_end_seq,
        ignore_exists,
    )
    .map_err(|err| network_error_with_context(&step, err))
}

fn recv_nft_ack(
    socket: &NlSocket,
    operation_seq: u32,
    batch_start_seq: u32,
    batch_end_seq: u32,
    ignore_exists: bool,
) -> Result<(), IsolatedError> {
    let mut buffer = vec![0_u8; 8192];
    loop {
        let received = socket
            .recv(&mut &mut buffer[..], 0)
            .map_err(network_error)?;
        let mut offset = 0;
        while offset + NLMSG_HEADER_LEN <= received {
            let Some(message_len) = read_u32_ne(&buffer[offset..]) else {
                return Err(IsolatedError::NetworkUnavailable(
                    "short nftables netlink header".to_owned(),
                ));
            };
            let message_len = usize::try_from(message_len).map_err(|_| {
                IsolatedError::NetworkUnavailable(
                    "nftables netlink message length does not fit usize".to_owned(),
                )
            })?;
            if message_len < NLMSG_HEADER_LEN || offset + message_len > received {
                return Err(IsolatedError::NetworkUnavailable(
                    "invalid nftables netlink message length".to_owned(),
                ));
            }
            let Some(message_type) = read_u16_ne(&buffer[offset + 4..]) else {
                return Err(IsolatedError::NetworkUnavailable(
                    "short nftables netlink message type".to_owned(),
                ));
            };
            let Some(message_seq) = read_u32_ne(&buffer[offset + 8..]) else {
                return Err(IsolatedError::NetworkUnavailable(
                    "short nftables netlink sequence".to_owned(),
                ));
            };
            if message_type == NLMSG_ERROR {
                let errno =
                    parse_nft_ack_errno(&buffer[offset + NLMSG_HEADER_LEN..offset + message_len])?;
                if message_seq == operation_seq {
                    return handle_nft_ack_errno(errno, ignore_exists);
                }
                if (batch_start_seq..=batch_end_seq).contains(&message_seq) && errno != 0 {
                    return handle_nft_ack_errno(errno, ignore_exists);
                }
            }
            offset += align4(message_len);
        }
    }
}

fn parse_nft_ack_errno(payload: &[u8]) -> Result<i32, IsolatedError> {
    let Some(errno) = read_i32_ne(payload) else {
        return Err(IsolatedError::NetworkUnavailable(
            "short nftables netlink ack".to_owned(),
        ));
    };
    Ok(errno)
}

fn handle_nft_ack_errno(errno: i32, ignore_exists: bool) -> Result<(), IsolatedError> {
    if errno == 0 || (ignore_exists && errno == -libc::EEXIST) {
        return Ok(());
    }
    let code = -errno;
    let message = if code > 0 {
        std::io::Error::from_raw_os_error(code).to_string()
    } else {
        format!("unexpected errno {errno}")
    };
    Err(IsolatedError::NetworkUnavailable(format!(
        "nftables netlink error: {message}"
    )))
}

fn nft_message(
    message_type: u16,
    flags: u16,
    seq: u32,
    family: u8,
    attrs: &[u8],
) -> Result<Vec<u8>, IsolatedError> {
    nfnetlink_message(nft_msg_type(message_type)?, flags, seq, family, 0, attrs)
}

fn nft_batch_message(message_type: u16, seq: u32) -> Result<Vec<u8>, IsolatedError> {
    nfnetlink_message(
        message_type,
        NLM_F_REQUEST,
        seq,
        libc_c_int_to_u8(libc::AF_UNSPEC, "AF_UNSPEC")?,
        libc_c_int_to_u16(libc::NFNL_SUBSYS_NFTABLES, "NFNL_SUBSYS_NFTABLES")?,
        &[],
    )
}

fn nfnetlink_message(
    message_type: u16,
    flags: u16,
    seq: u32,
    family: u8,
    res_id: u16,
    attrs: &[u8],
) -> Result<Vec<u8>, IsolatedError> {
    let total_len = NLMSG_HEADER_LEN + NFGENMSG_LEN + attrs.len();
    let total_len_wire = u32::try_from(total_len).map_err(|_| {
        IsolatedError::NetworkUnavailable("nftables netlink message too large".to_owned())
    })?;
    let mut message = Vec::with_capacity(total_len);
    message.extend_from_slice(&total_len_wire.to_ne_bytes());
    message.extend_from_slice(&message_type.to_ne_bytes());
    message.extend_from_slice(&flags.to_ne_bytes());
    message.extend_from_slice(&seq.to_ne_bytes());
    message.extend_from_slice(&0_u32.to_ne_bytes());
    message.push(family);
    message.push(NFNETLINK_V0);
    message.extend_from_slice(&res_id.to_be_bytes());
    message.extend_from_slice(attrs);
    Ok(message)
}

fn append_cstr_attr(buffer: &mut Vec<u8>, kind: u16, value: &str) {
    let mut bytes = value.as_bytes().to_vec();
    bytes.push(0);
    append_attr(buffer, kind, &bytes);
}

fn append_be_u32_attr(buffer: &mut Vec<u8>, kind: u16, value: u32) {
    append_attr(buffer, kind, &value.to_be_bytes());
}

fn append_be_i32_attr(buffer: &mut Vec<u8>, kind: u16, value: i32) {
    append_attr(buffer, kind, &value.to_be_bytes());
}

fn append_data_value_attr(buffer: &mut Vec<u8>, kind: u16, value: &[u8]) {
    let mut nested = Vec::new();
    append_attr(&mut nested, NFTA_DATA_VALUE, value);
    append_nested_attr(buffer, kind, &nested);
}

fn append_nested_attr(buffer: &mut Vec<u8>, kind: u16, value: &[u8]) {
    append_attr(buffer, kind | NFA_F_NESTED, value);
}

fn append_attr(buffer: &mut Vec<u8>, kind: u16, value: &[u8]) {
    let length = NLA_HEADER_LEN + value.len();
    buffer.extend_from_slice(&usize_to_u16_saturating(length).to_ne_bytes());
    buffer.extend_from_slice(&kind.to_ne_bytes());
    buffer.extend_from_slice(value);
    buffer.resize(buffer.len() + align4(length) - length, 0);
}

fn parse_ipv4_cidr(cidr: &str) -> Result<(Ipv4Addr, u8), IsolatedError> {
    let Some((addr, prefix_len)) = cidr.split_once('/') else {
        return Err(IsolatedError::NetworkUnavailable(format!(
            "invalid IPv4 CIDR {cidr}"
        )));
    };
    let addr = parse_ipv4_addr(addr)?;
    let prefix_len = prefix_len.parse::<u8>().map_err(|err| {
        IsolatedError::NetworkUnavailable(format!("invalid IPv4 CIDR prefix {cidr}: {err}"))
    })?;
    if prefix_len > 32 {
        return Err(IsolatedError::NetworkUnavailable(format!(
            "invalid IPv4 CIDR prefix {cidr}"
        )));
    }
    Ok((addr, prefix_len))
}

fn parse_ipv4_addr(addr: &str) -> Result<Ipv4Addr, IsolatedError> {
    addr.parse::<Ipv4Addr>().map_err(|err| {
        IsolatedError::NetworkUnavailable(format!("invalid IPv4 address {addr}: {err}"))
    })
}

fn ipv4_mask(prefix_len: u8) -> Result<[u8; 4], IsolatedError> {
    if prefix_len > 32 {
        return Err(IsolatedError::NetworkUnavailable(format!(
            "invalid IPv4 prefix length {prefix_len}"
        )));
    }
    let mask = if prefix_len == 0 {
        0
    } else {
        u32::MAX << (32 - prefix_len)
    };
    Ok(mask.to_be_bytes())
}

const fn gateway_addr() -> Ipv4Addr {
    Ipv4Addr::new(10, 244, 0, 1)
}

const fn bridge_network() -> (Ipv4Addr, u8) {
    (Ipv4Addr::new(10, 244, 0, 0), BRIDGE_PREFIX_LEN)
}

fn nft_msg_type(message_type: u16) -> Result<u16, IsolatedError> {
    Ok(
        (libc_c_int_to_u16(libc::NFNL_SUBSYS_NFTABLES, "NFNL_SUBSYS_NFTABLES")? << 8)
            | message_type,
    )
}

const fn nft_create_flags() -> u16 {
    NLM_F_REQUEST | NLM_F_ACK | NLM_F_EXCL | NLM_F_CREATE
}

const fn nft_rule_flags() -> u16 {
    NLM_F_REQUEST | NLM_F_ACK | NLM_F_CREATE | NLM_F_APPEND
}

fn libc_c_int_to_u8(value: libc::c_int, name: &str) -> Result<u8, IsolatedError> {
    u8::try_from(value).map_err(|_| {
        IsolatedError::NetworkUnavailable(format!("invalid libc {name} value {value}"))
    })
}

fn libc_c_int_to_u16(value: libc::c_int, name: &str) -> Result<u16, IsolatedError> {
    u16::try_from(value).map_err(|_| {
        IsolatedError::NetworkUnavailable(format!("invalid libc {name} value {value}"))
    })
}

fn libc_c_int_to_u32(value: libc::c_int, name: &str) -> Result<u32, IsolatedError> {
    u32::try_from(value).map_err(|_| {
        IsolatedError::NetworkUnavailable(format!("invalid libc {name} value {value}"))
    })
}

fn libc_c_int_to_isize(value: libc::c_int, name: &str) -> Result<isize, IsolatedError> {
    isize::try_from(value).map_err(|_| {
        IsolatedError::NetworkUnavailable(format!("invalid libc {name} value {value}"))
    })
}

fn usize_to_u16_saturating(value: usize) -> u16 {
    u16::try_from(value).unwrap_or(u16::MAX)
}

fn read_u16_ne(bytes: &[u8]) -> Option<u16> {
    let bytes = bytes.get(..2)?;
    Some(u16::from_ne_bytes([bytes[0], bytes[1]]))
}

fn read_u32_ne(bytes: &[u8]) -> Option<u32> {
    let bytes = bytes.get(..4)?;
    Some(u32::from_ne_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]))
}

fn read_i32_ne(bytes: &[u8]) -> Option<i32> {
    let bytes = bytes.get(..4)?;
    Some(i32::from_ne_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]))
}

const fn align4(length: usize) -> usize {
    (length + 3) & !3
}

fn network_error(error: impl std::fmt::Display) -> IsolatedError {
    IsolatedError::NetworkUnavailable(error.to_string())
}

fn network_error_at(step: impl Into<String>, error: impl std::fmt::Display) -> IsolatedError {
    IsolatedError::NetworkUnavailable(format!("{}: {error}", step.into()))
}

fn network_error_with_context(step: &str, error: IsolatedError) -> IsolatedError {
    match error {
        IsolatedError::NetworkUnavailable(message) => {
            IsolatedError::NetworkUnavailable(format!("{step}: {message}"))
        }
        other => other,
    }
}

const NFNETLINK_V0: u8 = 0;
const NLMSG_HEADER_LEN: usize = 16;
const NFGENMSG_LEN: usize = 4;
const NLA_HEADER_LEN: usize = 4;
const NLMSG_ERROR: u16 = 0x2;
const NLM_F_REQUEST: u16 = 0x01;
const NLM_F_ACK: u16 = 0x04;
const NLM_F_EXCL: u16 = 0x0200;
const NLM_F_CREATE: u16 = 0x0400;
const NLM_F_APPEND: u16 = 0x0800;
const NFA_F_NESTED: u16 = 0x8000;
const NFT_REG_VERDICT: u32 = 0;
const NFT_REG_1: u32 = 1;
const NFT_CMP_EQ: u32 = 0;
const NFT_CMP_NEQ: u32 = 1;
const IPV4_SADDR_OFFSET: u32 = 12;
const IPV4_DADDR_OFFSET: u32 = 16;
const IPV4_ADDR_LEN: u32 = 4;
const ETHER_TYPE_OFFSET: u32 = 12;
const ETHER_TYPE_LEN: u32 = 2;

const NFTA_TABLE_NAME: u16 = 1;
const NFTA_TABLE_FLAGS: u16 = 2;
const NFTA_CHAIN_TABLE: u16 = 1;
const NFTA_CHAIN_NAME: u16 = 3;
const NFTA_CHAIN_HOOK: u16 = 4;
const NFTA_CHAIN_TYPE: u16 = 7;
const NFTA_HOOK_HOOKNUM: u16 = 1;
const NFTA_HOOK_PRIORITY: u16 = 2;
const NFTA_RULE_TABLE: u16 = 1;
const NFTA_RULE_CHAIN: u16 = 2;
const NFTA_RULE_EXPRESSIONS: u16 = 4;
const NFTA_LIST_ELEM: u16 = 1;
const NFTA_EXPR_NAME: u16 = 1;
const NFTA_EXPR_DATA: u16 = 2;
const NFTA_PAYLOAD_DREG: u16 = 1;
const NFTA_PAYLOAD_BASE: u16 = 2;
const NFTA_PAYLOAD_OFFSET: u16 = 3;
const NFTA_PAYLOAD_LEN: u16 = 4;
const NFTA_META_DREG: u16 = 1;
const NFTA_META_KEY: u16 = 2;
const NFTA_CMP_SREG: u16 = 1;
const NFTA_CMP_OP: u16 = 2;
const NFTA_CMP_DATA: u16 = 3;
const NFTA_DATA_VALUE: u16 = 1;
const NFTA_DATA_VERDICT: u16 = 2;
const NFTA_IMMEDIATE_DREG: u16 = 1;
const NFTA_IMMEDIATE_DATA: u16 = 2;
const NFTA_VERDICT_CODE: u16 = 1;
const NFTA_BITWISE_SREG: u16 = 1;
const NFTA_BITWISE_DREG: u16 = 2;
const NFTA_BITWISE_LEN: u16 = 3;
const NFTA_BITWISE_MASK: u16 = 4;
const NFTA_BITWISE_XOR: u16 = 5;

#[cfg(test)]
mod tests;
