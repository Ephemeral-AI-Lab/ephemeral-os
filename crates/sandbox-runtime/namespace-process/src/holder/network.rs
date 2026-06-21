#[cfg(target_os = "linux")]
use std::ffi::CString;
use std::fs;
use std::io;
use std::net::Ipv4Addr;
use std::path::Path;

const IPV6_CONF_ROOT: &str = "/proc/sys/net/ipv6/conf";
const FALLBACK_IPV6_CONF_INTERFACES: [&str; 4] = ["all", "default", "lo", "eth0"];

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NetworkConfig {
    pub(crate) iface: String,
    pub(crate) ns_ip: Ipv4Addr,
    pub(crate) prefix_len: u8,
    pub(crate) gateway: Ipv4Addr,
}

pub(crate) fn parse_network_config(buf: &[u8]) -> Option<NetworkConfig> {
    let line = std::str::from_utf8(buf).ok()?.trim();
    let mut parts = line.split_whitespace();
    if parts.next()? != "net-ready" {
        return None;
    }
    let iface = parts.next()?.to_owned();
    let ns_ip = parts.next()?.parse().ok()?;
    let prefix_len = parts.next()?.parse().ok()?;
    let gateway = parts.next()?.parse().ok()?;
    Some(NetworkConfig {
        iface,
        ns_ip,
        prefix_len,
        gateway,
    })
}

pub(crate) fn disable_ipv6_ra() {
    let mut interfaces: Vec<String> = fs::read_dir(IPV6_CONF_ROOT)
        .map(|entries| {
            entries
                .flatten()
                .filter_map(|entry| entry.file_name().into_string().ok())
                .collect()
        })
        .unwrap_or_default();
    if interfaces.is_empty() {
        interfaces = FALLBACK_IPV6_CONF_INTERFACES.map(str::to_owned).to_vec();
    }
    for iface in interfaces {
        let _ = fs::write(
            Path::new(IPV6_CONF_ROOT).join(iface).join("accept_ra"),
            b"0",
        );
    }
}

#[cfg(target_os = "linux")]
pub(crate) fn bring_loopback_up() {
    let _ = set_link_up("lo");
}

#[cfg(not(target_os = "linux"))]
pub(crate) const fn bring_loopback_up() {}

#[cfg(target_os = "linux")]
pub(crate) fn configure_namespace_veth(config: &NetworkConfig) -> io::Result<()> {
    with_step("lookup namespace veth", link_index(&config.iface))?;
    with_step("set namespace veth up", set_link_up(&config.iface))?;
    with_step(
        "add namespace veth address",
        add_ipv4_address(&config.iface, config.ns_ip, config.prefix_len),
    )?;
    with_step(
        "add namespace veth default route",
        add_ipv4_default_route(&config.iface, config.gateway),
    )
}

#[cfg(not(target_os = "linux"))]
pub(crate) fn configure_namespace_veth(_config: &NetworkConfig) -> io::Result<()> {
    Ok(())
}

#[cfg(target_os = "linux")]
fn link_index(name: &str) -> io::Result<libc::c_uint> {
    let name = CString::new(name)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "interface name has NUL byte"))?;
    // SAFETY: `name` is a valid NUL-terminated C string and `if_nametoindex`
    // does not retain the pointer after returning.
    let index = unsafe { libc::if_nametoindex(name.as_ptr()) };
    if index == 0 {
        let os_error = io::Error::last_os_error();
        let error = if os_error.raw_os_error().is_some() {
            os_error
        } else {
            io::Error::new(io::ErrorKind::NotFound, "interface not found")
        };
        Err(error)
    } else {
        Ok(index)
    }
}

#[cfg(target_os = "linux")]
fn with_step<T>(step: &'static str, result: io::Result<T>) -> io::Result<T> {
    result.map_err(|error| io::Error::new(error.kind(), format!("{step}: {error}")))
}

#[cfg(target_os = "linux")]
fn set_link_up(name: &str) -> io::Result<()> {
    let Some(iff_up) = libc_c_int_to_c_short(libc::IFF_UP) else {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid IFF_UP value",
        ));
    };
    let mut request = interface_request(name)?;
    let socket = ioctl_socket()?;
    let result = get_interface_flags(socket, &mut request).and_then(|()| {
        // SAFETY: `SIOCGIFFLAGS` initialized the `ifru_flags` union field on success.
        let flags = unsafe { request.ifr_ifru.ifru_flags };
        // SAFETY: `SIOCSIFFLAGS` reads `ifru_flags`; assigning this union field
        // selects that variant for the following ioctl.
        request.ifr_ifru.ifru_flags = flags | iff_up;
        set_interface_flags(socket, &request)
    });
    let close_result = close_fd(socket);
    if result.is_ok() {
        close_result?;
    }
    result
}

#[cfg(target_os = "linux")]
fn interface_request(name: &str) -> io::Result<libc::ifreq> {
    let name = CString::new(name)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "interface name has NUL byte"))?;
    let bytes = name.as_bytes_with_nul();
    if bytes.len() > libc::IFNAMSIZ {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "interface name exceeds IFNAMSIZ",
        ));
    }
    // SAFETY: Linux `ifreq` is a plain C request buffer, and zero initialization
    // is the documented starting state before filling `ifr_name` and one union field.
    let mut request = unsafe { std::mem::zeroed::<libc::ifreq>() };
    // SAFETY: `request.ifr_name` has IFNAMSIZ elements and we checked `bytes`
    // fits, including the NUL terminator. The kernel treats the copied bytes as
    // a C interface name and does not retain this pointer.
    unsafe {
        std::ptr::copy_nonoverlapping(
            bytes.as_ptr().cast::<libc::c_char>(),
            request.ifr_name.as_mut_ptr(),
            bytes.len(),
        );
    }
    Ok(request)
}

#[cfg(target_os = "linux")]
fn ioctl_socket() -> io::Result<libc::c_int> {
    // SAFETY: `socket` is called with constant arguments and returns an owned fd
    // on success, closed by the caller.
    let fd = unsafe { libc::socket(libc::AF_INET, libc::SOCK_DGRAM | libc::SOCK_CLOEXEC, 0) };
    if fd < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(fd)
    }
}

#[cfg(target_os = "linux")]
fn get_interface_flags(fd: libc::c_int, request: &mut libc::ifreq) -> io::Result<()> {
    ioctl_ifreq(fd, ioctl_request(libc::SIOCGIFFLAGS)?, request)
}

#[cfg(target_os = "linux")]
fn set_interface_flags(fd: libc::c_int, request: &libc::ifreq) -> io::Result<()> {
    ioctl_ifreq(fd, ioctl_request(libc::SIOCSIFFLAGS)?, request)
}

#[cfg(target_os = "linux")]
fn ioctl_request(value: libc::c_ulong) -> io::Result<libc::Ioctl> {
    libc::Ioctl::try_from(value)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "invalid ioctl request value"))
}

#[cfg(target_os = "linux")]
fn ioctl_ifreq<T>(fd: libc::c_int, request: libc::Ioctl, arg: &T) -> io::Result<()> {
    // SAFETY: `fd` is an open ioctl socket; `arg` points to a valid `ifreq`
    // buffer for the duration of the call, and the kernel copies from/to it
    // according to the request number.
    let rc = unsafe { libc::ioctl(fd, request, arg) };
    if rc < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(target_os = "linux")]
fn close_fd(fd: libc::c_int) -> io::Result<()> {
    // SAFETY: `fd` is owned by the caller and must be closed exactly once.
    let rc = unsafe { libc::close(fd) };
    if rc < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(target_os = "linux")]
fn add_ipv4_address(name: &str, ip: Ipv4Addr, prefix_len: u8) -> io::Result<()> {
    let netmask = prefix_netmask(prefix_len)?;
    let mut request = interface_request(name)?;
    request.ifr_ifru.ifru_addr = ipv4_sockaddr(ip)?;
    let socket = ioctl_socket()?;
    let result = ioctl_ifreq(socket, ioctl_request(libc::SIOCSIFADDR)?, &request).and_then(|()| {
        request.ifr_ifru.ifru_addr = ipv4_sockaddr(netmask)?;
        ioctl_ifreq(socket, ioctl_request(libc::SIOCSIFNETMASK)?, &request)
    });
    let close_result = close_fd(socket);
    if result.is_ok() {
        close_result?;
    }
    result
}

#[cfg(target_os = "linux")]
fn prefix_netmask(prefix_len: u8) -> io::Result<Ipv4Addr> {
    if prefix_len > 32 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            format!("invalid IPv4 prefix length {prefix_len}"),
        ));
    }
    let mask = if prefix_len == 0 {
        0
    } else {
        u32::MAX << (32 - prefix_len)
    };
    Ok(Ipv4Addr::from(mask))
}

#[cfg(target_os = "linux")]
fn ipv4_sockaddr(ip: Ipv4Addr) -> io::Result<libc::sockaddr> {
    let Some(sin_family) = libc_c_int_to_sock_family(libc::AF_INET) else {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid AF_INET value",
        ));
    };
    let sockaddr_in = libc::sockaddr_in {
        sin_family,
        sin_port: 0,
        sin_addr: libc::in_addr {
            s_addr: u32::from_ne_bytes(ip.octets()),
        },
        sin_zero: [0; 8],
    };
    // SAFETY: `sockaddr_in` and `sockaddr` are both 16-byte Linux socket
    // address buffers. All bytes in `sockaddr_in` are initialized above, and
    // the copied value is consumed immediately by ioctl.
    let mut sockaddr = unsafe { std::mem::zeroed::<libc::sockaddr>() };
    // SAFETY: both pointers are valid for `size_of::<sockaddr_in>()` bytes,
    // and `sockaddr` is large enough on Linux for an IPv4 socket address.
    unsafe {
        std::ptr::copy_nonoverlapping(
            std::ptr::from_ref(&sockaddr_in).cast::<u8>(),
            std::ptr::from_mut(&mut sockaddr).cast::<u8>(),
            std::mem::size_of::<libc::sockaddr_in>(),
        );
    }
    Ok(sockaddr)
}

#[cfg(target_os = "linux")]
fn add_ipv4_default_route(name: &str, gateway: Ipv4Addr) -> io::Result<()> {
    let device = CString::new(name)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "interface name has NUL byte"))?;
    // SAFETY: Linux `rtentry` is a plain C request buffer; zero is the
    // documented default for unused route fields.
    let mut route = unsafe { std::mem::zeroed::<libc::rtentry>() };
    route.rt_dst = ipv4_sockaddr(Ipv4Addr::UNSPECIFIED)?;
    route.rt_gateway = ipv4_sockaddr(gateway)?;
    route.rt_genmask = ipv4_sockaddr(Ipv4Addr::UNSPECIFIED)?;
    route.rt_flags = libc::RTF_UP | libc::RTF_GATEWAY;
    route.rt_dev = device.as_ptr().cast_mut();
    let socket = ioctl_socket()?;
    let result = ioctl_ifreq(socket, ioctl_request(libc::SIOCADDRT)?, &route);
    let close_result = close_fd(socket);
    if result.is_ok() {
        close_result?;
    }
    result
}

#[cfg(target_os = "linux")]
pub(crate) fn flush_ipv6_default_route() {
    let Some(rtm_family) = libc_c_int_to_u8(libc::AF_INET6) else {
        return;
    };
    let route = RouteMsg {
        rtm_family,
        rtm_dst_len: 0,
        rtm_src_len: 0,
        rtm_tos: 0,
        rtm_table: libc::RT_TABLE_MAIN,
        rtm_protocol: libc::RTPROT_UNSPEC,
        rtm_scope: libc::RT_SCOPE_UNIVERSE,
        rtm_type: libc::RTN_UNICAST,
        rtm_flags: 0,
    };
    let Some(flags) = libc_c_int_to_u16(libc::NLM_F_REQUEST) else {
        return;
    };
    let _ = send_netlink_message(libc::RTM_DELROUTE, flags, &route);
}

#[cfg(not(target_os = "linux"))]
pub(crate) const fn flush_ipv6_default_route() {}

#[cfg(target_os = "linux")]
fn send_netlink_message<T>(message_type: u16, flags: u16, payload: &T) -> io::Result<()> {
    let length = std::mem::size_of::<libc::nlmsghdr>() + std::mem::size_of::<T>();
    let nlmsg_len = u32::try_from(length)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "netlink message too large"))?;
    let mut message = Vec::with_capacity(length);
    let ack_flag = libc_c_int_to_u16(libc::NLM_F_ACK)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "invalid netlink ACK flag"))?;
    let header = libc::nlmsghdr {
        nlmsg_len,
        nlmsg_type: message_type,
        nlmsg_flags: flags | ack_flag,
        nlmsg_seq: 1,
        nlmsg_pid: 0,
    };
    append_struct_bytes(&mut message, &header);
    append_struct_bytes(&mut message, payload);
    let nl_family = libc_c_int_to_sock_family(libc::AF_NETLINK).ok_or_else(|| {
        io::Error::new(io::ErrorKind::InvalidInput, "invalid netlink socket family")
    })?;
    let addr = NetlinkSocketAddress {
        nl_family,
        nl_pad: 0,
        nl_pid: 0,
        nl_groups: 0,
    };
    // SAFETY: `socket` is called with constant arguments and returns an owned fd
    // on success, closed below before returning.
    let fd = unsafe {
        libc::socket(
            libc::AF_NETLINK,
            libc::SOCK_RAW | libc::SOCK_CLOEXEC,
            libc::NETLINK_ROUTE,
        )
    };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    let send_result = send_netlink_bytes(fd, &message, &addr).and_then(|()| read_netlink_ack(fd));
    // SAFETY: `fd` is owned by this function after a successful `socket` call.
    let close_result = unsafe { libc::close(fd) };
    if close_result < 0 && send_result.is_ok() {
        return Err(io::Error::last_os_error());
    }
    send_result
}

#[cfg(target_os = "linux")]
fn send_netlink_bytes(
    fd: libc::c_int,
    message: &[u8],
    addr: &NetlinkSocketAddress,
) -> io::Result<()> {
    // SAFETY: `message` and `addr` are valid for the duration of this call; the
    // kernel copies the bytes before returning. The fd is a netlink socket just
    // opened by this function.
    let rc = unsafe {
        libc::sendto(
            fd,
            message.as_ptr().cast(),
            message.len(),
            0,
            std::ptr::from_ref(&addr).cast(),
            libc_socklen(std::mem::size_of::<NetlinkSocketAddress>()).ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "netlink socket address too large",
                )
            })?,
        )
    };
    if rc < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(target_os = "linux")]
fn read_netlink_ack(fd: libc::c_int) -> io::Result<()> {
    let mut buf = [0_u8; 8192];
    loop {
        // SAFETY: `buf` is valid writable storage for `buf.len()` bytes, and fd
        // is an open netlink socket owned by the caller for the duration of recv.
        let read = unsafe { libc::recv(fd, buf.as_mut_ptr().cast(), buf.len(), 0) };
        if read < 0 {
            let err = io::Error::last_os_error();
            if err.kind() == io::ErrorKind::Interrupted {
                continue;
            }
            return Err(err);
        }
        let read = usize::try_from(read)
            .map_err(|_| io::Error::other("negative netlink recv byte count"))?;
        if read < NLMSG_HEADER_LEN {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "short netlink ACK",
            ));
        }
        let message_type = u16::from_ne_bytes([buf[4], buf[5]]);
        if i32::from(message_type) != libc::NLMSG_ERROR {
            continue;
        }
        if read < NLMSG_HEADER_LEN + NETLINK_ERROR_CODE_LEN {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "short netlink error ACK",
            ));
        }
        let error = i32::from_ne_bytes([
            buf[NLMSG_HEADER_LEN],
            buf[NLMSG_HEADER_LEN + 1],
            buf[NLMSG_HEADER_LEN + 2],
            buf[NLMSG_HEADER_LEN + 3],
        ]);
        if error == 0 {
            return Ok(());
        }
        return Err(io::Error::from_raw_os_error(error.saturating_abs()));
    }
}

#[cfg(target_os = "linux")]
fn append_struct_bytes<T>(buffer: &mut Vec<u8>, value: &T) {
    // SAFETY: every caller passes a fully-initialized, padding-free `#[repr(C)]`
    // netlink struct, so all `size_of::<T>()` bytes are initialized and reading
    // them as `u8` is sound. The bytes are copied into `buffer` before `value`
    // is dropped. Callers MUST NOT pass a type with compiler-inserted padding.
    let bytes = unsafe {
        std::slice::from_raw_parts(
            std::ptr::from_ref(value).cast::<u8>(),
            std::mem::size_of::<T>(),
        )
    };
    buffer.extend_from_slice(bytes);
}

#[cfg(target_os = "linux")]
fn libc_c_int_to_u8(value: libc::c_int) -> Option<u8> {
    u8::try_from(value).ok()
}

#[cfg(target_os = "linux")]
fn libc_c_int_to_u16(value: libc::c_int) -> Option<u16> {
    u16::try_from(value).ok()
}

#[cfg(target_os = "linux")]
fn libc_c_int_to_c_short(value: libc::c_int) -> Option<libc::c_short> {
    libc::c_short::try_from(value).ok()
}

#[cfg(target_os = "linux")]
fn libc_c_int_to_sock_family(value: libc::c_int) -> Option<libc::sa_family_t> {
    libc::sa_family_t::try_from(value).ok()
}

#[cfg(target_os = "linux")]
fn libc_socklen(value: usize) -> Option<libc::socklen_t> {
    libc::socklen_t::try_from(value).ok()
}

#[cfg(target_os = "linux")]
const NLMSG_HEADER_LEN: usize = 16;
#[cfg(target_os = "linux")]
const NETLINK_ERROR_CODE_LEN: usize = 4;

#[cfg(target_os = "linux")]
#[expect(
    clippy::struct_field_names,
    reason = "repr(C) layout mirrors the Linux rtmsg field names"
)]
#[repr(C)]
struct RouteMsg {
    rtm_family: u8,
    rtm_dst_len: u8,
    rtm_src_len: u8,
    rtm_tos: u8,
    rtm_table: u8,
    rtm_protocol: u8,
    rtm_scope: u8,
    rtm_type: u8,
    rtm_flags: u32,
}

#[cfg(target_os = "linux")]
#[expect(
    clippy::struct_field_names,
    reason = "repr(C) layout mirrors the Linux sockaddr_nl field names"
)]
#[repr(C)]
struct NetlinkSocketAddress {
    nl_family: libc::sa_family_t,
    nl_pad: u16,
    nl_pid: u32,
    nl_groups: u32,
}
