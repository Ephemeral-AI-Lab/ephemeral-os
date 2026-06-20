use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::OnceLock;
use std::time::{SystemTime, UNIX_EPOCH};

use trace::BootId;

static CONNECTION_SEQ: AtomicU64 = AtomicU64::new(1);
static DAEMON_BOOT_ID: OnceLock<BootId> = OnceLock::new();

pub(crate) fn daemon_boot_id() -> &'static BootId {
    DAEMON_BOOT_ID.get_or_init(BootId::new)
}

#[derive(Debug, Clone)]
pub(crate) struct RequestTraceFacts {
    pub connection_id: String,
    pub accepted_at_unix_ms: u64,
    pub listener_kind: &'static str,
    pub peer_addr: Option<String>,
    pub local_addr: Option<String>,
    pub is_tcp: bool,
    pub request_bytes: usize,
    pub read_duration_us: u64,
    pub auth_required: bool,
    pub auth_ok: bool,
    pub protocol_version: Option<i64>,
}

pub(crate) fn next_connection_id() -> String {
    format!(
        "daemon-conn-{}",
        CONNECTION_SEQ.fetch_add(1, Ordering::Relaxed)
    )
}

pub(crate) fn now_ms() -> u64 {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    u64::try_from(millis).unwrap_or(u64::MAX)
}
