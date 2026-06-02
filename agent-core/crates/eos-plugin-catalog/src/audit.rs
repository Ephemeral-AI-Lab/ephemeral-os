//! The plugin audit **wrapper** — the only surviving concept from `loader.py`.
//!
//! `eos-audit` owns the `plugin.*` event family ([`PluginSection`],
//! [`plugin_event`], the `PLUGIN_*` constants — its GC-audit-06). This crate
//! owns *wrapping a tool call*: [`audit_plugin_call`] times the awaited call and
//! emits `plugin.tool_invoked` → `plugin.tool_completed`/`plugin.error` around
//! it (loader.py 106-173, GC-plugin-catalog-03). [`plugin_section`] builds the
//! base section, applying the `Custom` fallback when a manifest declares no
//! `kind` (loader.py 117).
//!
//! `duration_ms` is **wall-clock** elapsed between two [`Clock::now`] reads — the
//! `eos-types` [`Clock`] exposes no monotonic instant, so wall-clock is the
//! measurement the seam can provide (a deliberate deviation from Python's
//! `monotonic_now()`; deterministic under a `TestClock`).

use std::future::Future;

use eos_audit::{
    plugin_event, AuditNode, AuditSink, PluginSection, PLUGIN_ERROR, PLUGIN_TOOL_COMPLETED,
    PLUGIN_TOOL_INVOKED,
};
use eos_types::{Clock, UtcDateTime};

use crate::manifest::PluginKind;
use crate::names::{PluginName, PluginToolName};

/// Build the base [`PluginSection`] for a plugin tool call.
///
/// Applies the `Custom` fallback (`manifest.kind or "custom"`, loader.py 117):
/// an unset `kind` records `plugin_kind = "custom"`.
#[must_use]
#[allow(clippy::field_reassign_with_default)] // PluginSection is #[non_exhaustive] upstream: a struct literal is impossible cross-crate, so fields are reassigned onto Default.
pub fn plugin_section(
    plugin_id: &PluginName,
    kind: Option<PluginKind>,
    tool: &PluginToolName,
) -> PluginSection {
    let mut section = PluginSection::default(); // plugin_kind = "custom"
    section.plugin_id = plugin_id.as_str().to_owned();
    if let Some(kind) = kind {
        section.plugin_kind = kind.as_wire().to_owned();
    }
    section.plugin_tool_name = Some(tool.as_str().to_owned());
    section
}

/// Time and audit a plugin tool call, emitting the three-event sequence around
/// the awaited `call` and re-returning its `Result` unchanged.
///
/// On success: `plugin.tool_invoked` then `plugin.tool_completed` (`status =
/// "ok"`, `duration_ms`). On failure: `plugin.tool_invoked` then `plugin.error`
/// (`status = "error"`, `error_kind`, `duration_ms`), re-returning the `Err`.
/// Audit publish failures are swallowed — auditing never breaks the call path
/// (Python's `safe_emit`).
pub async fn audit_plugin_call<T, E, Fut>(
    sink: &dyn AuditSink,
    clock: &dyn Clock,
    node: AuditNode,
    section: PluginSection,
    call: Fut,
) -> Result<T, E>
where
    Fut: Future<Output = Result<T, E>>,
{
    let started = clock.now();
    let _ = sink.publish(&plugin_event(
        PLUGIN_TOOL_INVOKED,
        &section,
        node.clone(),
        clock,
    ));

    match call.await {
        Ok(value) => {
            let mut completed = section;
            completed.duration_ms = Some(elapsed_ms(started, clock.now()));
            completed.status = Some("ok".to_owned());
            let _ = sink.publish(&plugin_event(
                PLUGIN_TOOL_COMPLETED,
                &completed,
                node,
                clock,
            ));
            Ok(value)
        }
        Err(err) => {
            let mut failed = section;
            failed.duration_ms = Some(elapsed_ms(started, clock.now()));
            failed.status = Some("error".to_owned());
            failed.error_kind = Some(short_type_name::<E>());
            let _ = sink.publish(&plugin_event(PLUGIN_ERROR, &failed, node, clock));
            Err(err)
        }
    }
}

/// Wall-clock milliseconds between two instants.
fn elapsed_ms(start: UtcDateTime, end: UtcDateTime) -> f64 {
    (end.into_inner() - start.into_inner()).as_seconds_f64() * 1000.0
}

/// The final `::`-segment of a type name — the Rust analogue of Python's
/// `type(exc).__name__` for the `error_kind` audit field.
fn short_type_name<E>() -> String {
    let full = std::any::type_name::<E>();
    full.rsplit("::").next().unwrap_or(full).to_owned()
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
    use super::*;
    use std::sync::Mutex;

    use eos_audit::{AuditError, AuditEvent};
    use eos_types::TestClock;

    #[derive(Default)]
    struct VecSink {
        events: Mutex<Vec<AuditEvent>>,
    }

    impl AuditSink for VecSink {
        fn publish(&self, event: &AuditEvent) -> Result<(), AuditError> {
            self.events.lock().expect("sink lock").push(event.clone());
            Ok(())
        }
    }

    impl VecSink {
        fn events(&self) -> Vec<AuditEvent> {
            self.events.lock().expect("sink lock").clone()
        }
    }

    #[derive(Debug)]
    struct BoomError;

    fn at(s: &str) -> UtcDateTime {
        UtcDateTime::parse_rfc3339(s).expect("rfc3339")
    }

    fn names() -> (PluginName, PluginToolName) {
        (
            PluginName::parse("lsp").expect("name"),
            PluginToolName::new("lsp.hover"),
        )
    }

    // AC-plugin-catalog-08: the success and failure event sequences, the
    // wall-clock duration, and the unset-kind `Custom` fallback (proves
    // GC-plugin-catalog-03).
    #[tokio::test]
    async fn wrapper_emits_invoked_completed_error() {
        let (plugin, tool) = names();

        // Success path: kind unset -> "custom"; duration = 1000 ms; status ok.
        let sink = VecSink::default();
        let clock = TestClock::new(at("2026-06-02T00:00:00Z"));
        let section = plugin_section(&plugin, None, &tool);
        let result: Result<i32, BoomError> =
            audit_plugin_call(&sink, &clock, AuditNode::default(), section, async {
                clock.set(at("2026-06-02T00:00:01Z"));
                Ok(7)
            })
            .await;
        assert_eq!(result.unwrap(), 7);

        let events = sink.events();
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].event_type, PLUGIN_TOOL_INVOKED);
        assert_eq!(events[1].event_type, PLUGIN_TOOL_COMPLETED);
        let invoked = events[0].payload["plugin"].as_object().unwrap();
        assert_eq!(invoked["plugin_kind"], serde_json::json!("custom"));
        assert_eq!(invoked["plugin_id"], serde_json::json!("lsp"));
        assert_eq!(invoked["plugin_tool_name"], serde_json::json!("lsp.hover"));
        assert!(!invoked.contains_key("status")); // invoked carries no status
        let completed = events[1].payload["plugin"].as_object().unwrap();
        assert_eq!(completed["status"], serde_json::json!("ok"));
        assert_eq!(completed["duration_ms"], serde_json::json!(1000.0));

        // Failure path: status error; error_kind is the type name; Err re-returned.
        let sink = VecSink::default();
        let clock = TestClock::new(at("2026-06-02T00:00:00Z"));
        let section = plugin_section(&plugin, Some(PluginKind::LanguageServer), &tool);
        let result: Result<i32, BoomError> =
            audit_plugin_call(&sink, &clock, AuditNode::default(), section, async {
                clock.set(at("2026-06-02T00:00:00.500Z"));
                Err(BoomError)
            })
            .await;
        assert!(result.is_err());

        let events = sink.events();
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].event_type, PLUGIN_TOOL_INVOKED);
        assert_eq!(events[1].event_type, PLUGIN_ERROR);
        let invoked = events[0].payload["plugin"].as_object().unwrap();
        assert_eq!(invoked["plugin_kind"], serde_json::json!("language_server"));
        let errored = events[1].payload["plugin"].as_object().unwrap();
        assert_eq!(errored["status"], serde_json::json!("error"));
        assert_eq!(errored["error_kind"], serde_json::json!("BoomError"));
        assert_eq!(errored["duration_ms"], serde_json::json!(500.0));
    }
}
