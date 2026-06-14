use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};

const MAX_PPC_TRACE_EVENTS: usize = 1024;

#[derive(Debug, Clone, PartialEq)]
pub struct PpcTraceEvent {
    pub module: &'static str,
    pub name: &'static str,
    pub details: Value,
}

impl PpcTraceEvent {
    #[must_use]
    pub fn new(name: &'static str, details: Value) -> Self {
        Self::in_module("plugin", name, details)
    }

    #[must_use]
    pub fn in_module(module: &'static str, name: &'static str, details: Value) -> Self {
        Self {
            module,
            name,
            details,
        }
    }
}

#[derive(Clone, Default)]
pub struct PpcTraceEventSink {
    state: Arc<Mutex<PpcTraceEventState>>,
}

impl PpcTraceEventSink {
    pub(super) fn push(&self, event: PpcTraceEvent) {
        self.push_inner(None, event);
    }

    pub(super) fn push_for(&self, owner_message_id: &str, event: PpcTraceEvent) {
        self.push_inner(Some(owner_message_id.to_owned()), event);
    }

    fn push_inner(&self, owner_message_id: Option<String>, event: PpcTraceEvent) {
        if let Ok(mut state) = self.state.lock() {
            if state.events.len() >= MAX_PPC_TRACE_EVENTS {
                *state.dropped.entry(owner_message_id).or_default() += 1;
                return;
            }
            state.events.push(StoredPpcTraceEvent {
                owner_message_id,
                event,
            });
        }
    }

    #[must_use]
    pub fn drain(&self) -> Vec<PpcTraceEvent> {
        let Ok(mut state) = self.state.lock() else {
            return Vec::new();
        };
        let mut drained = state
            .events
            .drain(..)
            .map(|stored| stored.event)
            .collect::<Vec<_>>();
        drained.extend(drain_dropped_matching(&mut state.dropped, |_| true));
        drained
    }

    #[must_use]
    pub fn drain_for(&self, owner_message_id: &str) -> Vec<PpcTraceEvent> {
        self.drain_matching(
            |stored| stored.owner_message_id.as_deref() == Some(owner_message_id),
            |owner| owner == Some(owner_message_id),
        )
    }

    #[must_use]
    pub fn drain_unowned(&self) -> Vec<PpcTraceEvent> {
        self.drain_matching(
            |stored| stored.owner_message_id.is_none(),
            |owner| owner.is_none(),
        )
    }

    fn drain_matching(
        &self,
        mut matches: impl FnMut(&StoredPpcTraceEvent) -> bool,
        matches_owner: impl FnMut(Option<&str>) -> bool,
    ) -> Vec<PpcTraceEvent> {
        let Ok(mut state) = self.state.lock() else {
            return Vec::new();
        };
        let mut drained = Vec::new();
        let mut index = 0;
        while index < state.events.len() {
            if matches(&state.events[index]) {
                drained.push(state.events.remove(index).event);
            } else {
                index += 1;
            }
        }
        drained.extend(drain_dropped_matching(&mut state.dropped, matches_owner));
        drained
    }
}

#[derive(Default)]
struct PpcTraceEventState {
    events: Vec<StoredPpcTraceEvent>,
    dropped: BTreeMap<Option<String>, usize>,
}

#[derive(Debug, Clone)]
struct StoredPpcTraceEvent {
    owner_message_id: Option<String>,
    event: PpcTraceEvent,
}

fn drain_dropped_matching(
    dropped: &mut BTreeMap<Option<String>, usize>,
    mut matches_owner: impl FnMut(Option<&str>) -> bool,
) -> Vec<PpcTraceEvent> {
    let owners = dropped
        .keys()
        .filter(|owner| matches_owner(owner.as_deref()))
        .cloned()
        .collect::<Vec<_>>();
    owners
        .into_iter()
        .filter_map(|owner| {
            let count = dropped.remove(&owner)?;
            Some(dropped_event(owner, count))
        })
        .collect()
}

fn dropped_event(owner_message_id: Option<String>, dropped_count: usize) -> PpcTraceEvent {
    PpcTraceEvent::new(
        "ppc_trace_events_dropped",
        json!({
            "owner_message_id": owner_message_id,
            "dropped_count": dropped_count,
            "max_events": MAX_PPC_TRACE_EVENTS,
        }),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unowned_trace_sink_reports_dropped_events_after_limit() {
        let sink = PpcTraceEventSink::default();
        for index in 0..(MAX_PPC_TRACE_EVENTS + 2) {
            sink.push(PpcTraceEvent::new(
                "ppc_test_event",
                json!({ "index": index }),
            ));
        }

        let events = sink.drain_unowned();
        assert_eq!(
            events
                .iter()
                .filter(|event| event.name == "ppc_test_event")
                .count(),
            MAX_PPC_TRACE_EVENTS
        );
        let dropped = events
            .iter()
            .find(|event| event.name == "ppc_trace_events_dropped")
            .expect("sink should report unowned dropped events");
        assert!(dropped.details["owner_message_id"].is_null());
        assert_eq!(dropped.details["dropped_count"], json!(2));
        assert_eq!(dropped.details["max_events"], json!(MAX_PPC_TRACE_EVENTS));
        assert!(sink.drain_unowned().is_empty());
    }

    #[test]
    fn owned_trace_sink_reports_dropped_events_for_matching_owner() {
        let sink = PpcTraceEventSink::default();
        for index in 0..(MAX_PPC_TRACE_EVENTS + 3) {
            sink.push_for(
                "op-1",
                PpcTraceEvent::new("ppc_test_event", json!({ "index": index })),
            );
        }

        let events = sink.drain_for("op-1");
        assert_eq!(
            events
                .iter()
                .filter(|event| event.name == "ppc_test_event")
                .count(),
            MAX_PPC_TRACE_EVENTS
        );
        let dropped = events
            .iter()
            .find(|event| event.name == "ppc_trace_events_dropped")
            .expect("sink should report owner dropped events");
        assert_eq!(dropped.details["owner_message_id"], "op-1");
        assert_eq!(dropped.details["dropped_count"], json!(3));
        assert_eq!(dropped.details["max_events"], json!(MAX_PPC_TRACE_EVENTS));
        assert!(sink.drain_for("op-1").is_empty());
    }
}
