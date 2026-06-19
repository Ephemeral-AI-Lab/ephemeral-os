use prost::Message;
use trace::budget::{BoundedJson, DetailBudget};
use trace::codec::proto;
use trace::sha256_hex;

use super::audit::{append_audit_entry_tx, AuditAppend, AUDIT_SCHEMA, REQUEST_START_SCHEMA};
use super::payload::{encode_audit_payload, TraceDegradedPayload};
use super::projection::{project_request_start_tx, project_trace_degraded_tx, ProjectRequestStart};
use super::types::DegradedRequestInput;
use super::{
    now_ms, usize_to_u64, write_transaction, ForwardTraceDecision, RequestStartInput, TraceStore,
    TraceStoreError,
};

impl TraceStore {
    pub fn prepare_forward(
        &self,
        input: RequestStartInput<'_>,
    ) -> Result<ForwardTraceDecision, TraceStoreError> {
        let degraded_input = DegradedRequestInput {
            sandbox_id: input.sandbox_id,
            trace_id: input.trace_id.clone(),
            request_id: input.request_id.clone(),
            op: input.op,
            caller_id: input.caller_id,
            args: input.args.clone(),
        };
        let trace_id = input.trace_id.clone();
        let request_id = input.request_id.clone();
        let mutates_state = input.mutates_state;
        match self.append_request_start(input) {
            Ok(()) => Ok(ForwardTraceDecision {
                trace_id,
                request_id,
                degraded: false,
            }),
            Err(err) if !mutates_state && err.allows_read_only_degraded() => {
                // Best-effort marker: a read-only op proceeds degraded even when the
                // store is too unavailable to record the marker. An untraceable read
                // is acceptable; an untraceable mutation is not, and fails closed in
                // the catch-all arm below.
                let _ = self.append_trace_degraded(&degraded_input, &err);
                Ok(ForwardTraceDecision {
                    trace_id,
                    request_id,
                    degraded: true,
                })
            }
            Err(err) => Err(err),
        }
    }

    pub fn append_request_start(
        &self,
        input: RequestStartInput<'_>,
    ) -> Result<(), TraceStoreError> {
        if self
            .fail_next_request_start
            .swap(false, std::sync::atomic::Ordering::SeqCst)
        {
            return Err(TraceStoreError::InjectedRequestStartFailure);
        }

        let args_summary =
            BoundedJson::capture(input.args.clone(), DetailBudget::RequestArgsSummary);
        let redacted_args = trace::budget::redact_for_audit(input.args.clone());
        // Digest/length describe the request args only. They are never computed
        // over the forwarded TCP frame, which carries `_eos_daemon_auth_token`;
        // the security rule forbids recording, hashing, or length-recording the
        // auth token (SPEC: Transport connection lifecycle -> Security rules).
        let args_bytes = serde_json::to_vec(&redacted_args).unwrap_or_default();
        let payload = proto::RequestStart {
            trace_id: input.trace_id.to_string(),
            request_id: input.request_id.to_string(),
            sandbox_id: input.sandbox_id.to_owned(),
            op: input.op.to_owned(),
            mutates_state: input.mutates_state,
            args_summary_json: args_summary.encoded_value(),
            args_summary_truncated: args_summary.truncated,
            args_summary_sha256: args_summary.sha256.clone().unwrap_or_default(),
            args_summary_original_len: usize_to_u64(args_summary.original_len),
            started_at_unix_ms: now_ms(),
            caller_id: input.caller_id.unwrap_or_default().to_owned(),
            host_boot_id: self.host_boot_id.to_string(),
            args_len: usize_to_u64(args_bytes.len()),
            args_digest: sha256_hex(&args_bytes),
        }
        .encode_to_vec();

        let mut conn = self.lock();
        let tx = write_transaction(&mut conn)?;
        append_audit_entry_tx(
            &tx,
            AuditAppend {
                sandbox_id: input.sandbox_id,
                trace_id: input.trace_id.as_str(),
                request_id: Some(input.request_id.as_str()),
                entry_kind: "request_start",
                schema_name: REQUEST_START_SCHEMA,
                schema_version: 1,
                received_at_ms: now_ms(),
                payload: &payload,
            },
        )?;
        project_request_start_tx(
            &tx,
            ProjectRequestStart {
                sandbox_id: input.sandbox_id,
                trace_id: input.trace_id.as_str(),
                request_id: input.request_id.as_str(),
                op: input.op,
                caller_id: input.caller_id,
                args_summary: &args_summary.encoded_value(),
                args_digest: &sha256_hex(&args_bytes),
                sent_at_ms: now_ms(),
                host_boot_id: self.host_boot_id.as_str(),
            },
        )?;
        tx.commit()?;
        Ok(())
    }

    fn append_trace_degraded(
        &self,
        input: &DegradedRequestInput<'_>,
        error: &TraceStoreError,
    ) -> Result<(), TraceStoreError> {
        let args_summary =
            BoundedJson::capture(input.args.clone(), DetailBudget::RequestArgsSummary);
        let redacted_args = trace::budget::redact_for_audit(input.args.clone());
        // Token-free args digest only; never the forwarded auth-bearing frame.
        let args_bytes = serde_json::to_vec(&redacted_args).unwrap_or_default();
        let payload = TraceDegradedPayload {
            trace_id: input.trace_id.to_string(),
            request_id: input.request_id.to_string(),
            sandbox_id: input.sandbox_id.to_owned(),
            op: input.op.to_owned(),
            caller_id: input.caller_id.map(ToOwned::to_owned),
            args_summary: args_summary.encoded_value(),
            args_digest: sha256_hex(&args_bytes),
            sent_at_ms: now_ms(),
            host_boot_id: self.host_boot_id.to_string(),
            error_kind: "trace_degraded".to_owned(),
            message: error.to_string(),
        };
        let payload_bytes = encode_audit_payload(&payload);
        let mut conn = self.lock();
        let tx = write_transaction(&mut conn)?;
        append_audit_entry_tx(
            &tx,
            AuditAppend {
                sandbox_id: input.sandbox_id,
                trace_id: input.trace_id.as_str(),
                request_id: Some(input.request_id.as_str()),
                entry_kind: "trace_degraded",
                schema_name: AUDIT_SCHEMA,
                schema_version: 1,
                received_at_ms: now_ms(),
                payload: &payload_bytes,
            },
        )?;
        project_trace_degraded_tx(&tx, &payload)?;
        tx.commit()?;
        Ok(())
    }
}

impl TraceStoreError {
    /// Read-only ops proceed with a `trace_degraded` marker when the
    /// request-start append fails because the store itself is unavailable: the
    /// test injection or a real sqlite error (disk-full, lock contention, I/O).
    /// Schema/decode errors are not request-start append failures and never
    /// reach this path; mutating ops always fail closed regardless.
    const fn allows_read_only_degraded(&self) -> bool {
        matches!(self, Self::InjectedRequestStartFailure | Self::Sqlite(_))
    }
}
