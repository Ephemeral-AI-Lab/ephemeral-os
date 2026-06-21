use std::sync::Mutex;

use super::*;

#[derive(Default)]
struct FakeWaitTarget {
    output: Mutex<String>,
    offsets: Mutex<Vec<u64>>,
}

impl CommandWaitTarget<&'static str> for FakeWaitTarget {
    fn take_exit(&self) -> Option<&'static str> {
        None
    }

    fn transcript_len(&self) -> u64 {
        self.offsets
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .pop()
            .unwrap_or(1)
    }

    fn read_output_since(&self, _start_offset: u64) -> String {
        self.output
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone()
    }
}

#[test]
fn wait_returns_running_after_quiet_output() {
    let target = FakeWaitTarget {
        output: Mutex::new("ready\n".to_owned()),
        offsets: Mutex::new(vec![1, 1, 0]),
    };
    let result = wait_for_yield(&target, 100, 0);

    assert_eq!(result, WaitOutcome::Running("ready\n".to_owned()));
}

#[test]
fn wait_reports_first_output_and_quiet_reason() {
    let target = FakeWaitTarget {
        output: Mutex::new("ready\n".to_owned()),
        offsets: Mutex::new(vec![1, 1, 0]),
    };
    let report = wait_for_yield_with_timing(&target, 100, 0);

    assert_eq!(report.outcome, WaitOutcome::Running("ready\n".to_owned()));
    assert_eq!(report.timing.reason, WaitYieldReason::OutputQuiet);
    assert!(report.timing.first_output_ms.is_some());
    assert!(report.timing.last_output_ms.is_some());
    assert!(report.timing.quiet_ms.is_some());
}
