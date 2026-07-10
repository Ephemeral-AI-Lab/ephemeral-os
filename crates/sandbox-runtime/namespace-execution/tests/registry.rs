use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use sandbox_runtime_namespace_execution::{
    ExecutionRegistry, NamespaceExecutionError, NamespaceExecutionId,
    NamespaceExecutionTerminalStatus,
};

fn id(n: u32) -> NamespaceExecutionId {
    NamespaceExecutionId(format!("namespace_execution_{n}"))
}

#[test]
fn admits_up_to_capacity_then_refuses() {
    let registry = ExecutionRegistry::<()>::new(2, 512);
    registry.try_reserve(&id(1)).expect("first slot");
    registry.try_reserve(&id(2)).expect("second slot");
    let refused = registry.try_reserve(&id(3)).expect_err("over capacity");
    assert!(matches!(
        refused,
        NamespaceExecutionError::Admission { max_active: 2 }
    ));
}

#[test]
fn complete_moves_live_to_completed() {
    let registry = ExecutionRegistry::<()>::new(1, 512);
    registry.try_reserve(&id(1)).expect("slot");
    assert!(registry.is_live(&id(1)));
    assert!(!registry.is_completed(&id(1)));

    registry.complete(&id(1), NamespaceExecutionTerminalStatus::Ok, Some(0));

    assert!(!registry.is_live(&id(1)));
    assert!(registry.is_completed(&id(1)));
}

#[test]
fn attach_records_the_caller_value() {
    let registry = ExecutionRegistry::new(1, 512);
    registry.try_reserve(&id(1)).expect("slot");
    registry.attach(&id(1), "command-handle".to_owned());
    assert_eq!(
        registry.with_value(&id(1), Clone::clone),
        Some("command-handle".to_owned())
    );
    assert_eq!(
        registry.live_values(|value| Some(value.clone())),
        vec!["command-handle".to_owned()]
    );
}

#[test]
fn abort_releases_a_reservation() {
    let registry = ExecutionRegistry::<()>::new(1, 512);
    registry.try_reserve(&id(1)).expect("slot");
    registry.abort(&id(1));
    assert!(!registry.is_live(&id(1)));
    registry
        .try_reserve(&id(2))
        .expect("slot freed after abort");
}

struct DropProbe(Arc<AtomicUsize>);

impl Drop for DropProbe {
    fn drop(&mut self) {
        self.0.fetch_add(1, Ordering::SeqCst);
    }
}

#[test]
fn terminal_retention_evicts_oldest_terminal_entry_and_drops_its_value() {
    let drops = Arc::new(AtomicUsize::new(0));
    let registry = ExecutionRegistry::new(8, 512);
    registry.set_terminal_retention(2);
    for n in 1..=4 {
        registry.try_reserve(&id(n)).expect("slot");
        registry.attach(&id(n), DropProbe(Arc::clone(&drops)));
        registry.complete(&id(n), NamespaceExecutionTerminalStatus::Ok, Some(0));
    }

    assert_eq!(
        drops.load(Ordering::SeqCst),
        2,
        "the two oldest terminal values were dropped"
    );
    assert!(
        !registry.is_completed(&id(1)) && !registry.is_completed(&id(2)),
        "evicted entries are gone entirely"
    );
    assert!(registry.with_value(&id(1), |_| ()).is_none());
    assert!(registry.is_completed(&id(3)) && registry.is_completed(&id(4)));
    assert!(registry.with_value(&id(4), |_| ()).is_some());
}

#[test]
fn terminal_retention_never_evicts_live_entries() {
    let drops = Arc::new(AtomicUsize::new(0));
    let registry = ExecutionRegistry::new(8, 512);
    registry.set_terminal_retention(1);
    registry.try_reserve(&id(1)).expect("slot");
    registry.attach(&id(1), DropProbe(Arc::clone(&drops)));
    registry.try_reserve(&id(2)).expect("slot");
    registry.attach(&id(2), DropProbe(Arc::clone(&drops)));
    registry.try_reserve(&id(3)).expect("slot");
    registry.attach(&id(3), DropProbe(Arc::clone(&drops)));

    registry.complete(&id(2), NamespaceExecutionTerminalStatus::Ok, Some(0));
    registry.complete(&id(3), NamespaceExecutionTerminalStatus::Ok, Some(0));

    assert!(registry.is_live(&id(1)), "live entries are never evicted");
    assert_eq!(drops.load(Ordering::SeqCst), 1, "only id 2 was evicted");
    assert!(!registry.is_completed(&id(2)));
    assert!(registry.is_completed(&id(3)));
}
