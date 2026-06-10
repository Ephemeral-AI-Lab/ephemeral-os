//! The box-hop forward path: build the daemon envelope (auth stamped
//! top-level, protocol version inside `args`, `sandbox_id` already stripped
//! by the caller) and drive it through the recovery ladder.

use serde_json::Value;

use crate::client::stamped_envelope_bytes;
use crate::lifecycle::HostConfig;
use crate::recovery::{self, ForwardAttempt, ForwardError};
use crate::registry::SandboxRecord;

pub(crate) fn forward(
    record: &SandboxRecord,
    config: &HostConfig,
    mutates_state: bool,
    op: &str,
    invocation_id: &str,
    args: &Value,
) -> Result<Value, ForwardError> {
    let mut tcp_line = stamped_envelope_bytes(op, invocation_id, args, Some(&record.token));
    tcp_line.push(b'\n');
    let attempt = ForwardAttempt {
        record,
        config,
        mutates_state,
        tcp_line,
        op,
        invocation_id,
        args,
    };
    recovery::run(&attempt)
}
