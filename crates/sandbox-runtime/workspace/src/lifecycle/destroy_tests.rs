use std::collections::HashMap;

use super::destroy::{TeardownLedger, TeardownStep, TeardownStepExecutor};

#[derive(Default)]
struct CountingExecutor {
    calls: HashMap<TeardownStep, usize>,
    fail_once: Option<TeardownStep>,
}

impl TeardownStepExecutor for CountingExecutor {
    fn execute(&mut self, step: TeardownStep) -> Result<(), String> {
        *self.calls.entry(step).or_default() += 1;
        if self.fail_once == Some(step) {
            self.fail_once = None;
            return Err("injected failure".to_owned());
        }
        Ok(())
    }
}

#[test]
fn every_teardown_step_retries_only_its_failure_and_never_double_releases() {
    for failed_step in TeardownStep::ORDER {
        let mut ledger = TeardownLedger::default();
        let mut executor = CountingExecutor {
            fail_once: Some(failed_step),
            ..CountingExecutor::default()
        };

        let first = ledger
            .run(&mut executor)
            .expect_err("injected teardown failure remains visible");
        assert_eq!(first.len(), 1);
        assert_eq!(first[0].step, failed_step);
        assert!(!ledger.is_complete());

        ledger.run(&mut executor).expect("bounded retry completes");
        assert!(ledger.is_complete());
        for step in TeardownStep::ORDER {
            let expected = if step == failed_step { 2 } else { 1 };
            assert_eq!(
                executor.calls.get(&step).copied(),
                Some(expected),
                "failed={failed_step:?}, observed={step:?}"
            );
        }
    }
}

#[test]
fn teardown_attempts_independent_steps_after_one_failure() {
    let mut ledger = TeardownLedger::default();
    let mut executor = CountingExecutor {
        fail_once: Some(TeardownStep::Holder),
        ..CountingExecutor::default()
    };

    let failures = ledger
        .run(&mut executor)
        .expect_err("holder failure is reported");

    assert_eq!(failures[0].step, TeardownStep::Holder);
    assert_eq!(executor.calls.get(&TeardownStep::Persistence), Some(&1));
    assert_eq!(executor.calls.get(&TeardownStep::Scratch), Some(&1));
}

#[test]
fn lease_accounting_failure_retries_without_repeating_raw_teardown() {
    #[derive(Default)]
    struct AccountingFailureExecutor {
        calls: HashMap<TeardownStep, usize>,
        accounting_failed: bool,
    }

    impl TeardownStepExecutor for AccountingFailureExecutor {
        fn execute(&mut self, step: TeardownStep) -> Result<(), String> {
            *self.calls.entry(step).or_default() += 1;
            if step == TeardownStep::LeaseAccounting && !self.accounting_failed {
                self.accounting_failed = true;
                return Err("injected active-lease accounting failure".to_owned());
            }
            if step == TeardownStep::Persistence
                && self.calls.get(&TeardownStep::LeaseAccounting) == Some(&1)
            {
                return Err("deferred until lease accounting succeeds".to_owned());
            }
            Ok(())
        }
    }

    let mut ledger = TeardownLedger::default();
    let mut executor = AccountingFailureExecutor::default();

    let first = ledger
        .run(&mut executor)
        .expect_err("post-close accounting failure must retain the transaction");
    assert_eq!(
        first.iter().map(|failure| failure.step).collect::<Vec<_>>(),
        vec![TeardownStep::LeaseAccounting, TeardownStep::Persistence]
    );

    ledger
        .run(&mut executor)
        .expect("retry joins the retained transaction");
    assert!(ledger.is_complete());
    for raw_step in [
        TeardownStep::Holder,
        TeardownStep::Commands,
        TeardownStep::NamespaceFds,
        TeardownStep::Network,
        TeardownStep::Mounts,
        TeardownStep::Scratch,
        TeardownStep::Leases,
    ] {
        assert_eq!(
            executor.calls.get(&raw_step),
            Some(&1),
            "raw teardown step {raw_step:?} repeated"
        );
    }
    assert_eq!(executor.calls.get(&TeardownStep::LeaseAccounting), Some(&2));
    assert_eq!(executor.calls.get(&TeardownStep::Persistence), Some(&2));
}
