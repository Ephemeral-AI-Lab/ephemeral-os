#![allow(dead_code)]

pub use sandbox_e2e_live_test::assertion;
pub use sandbox_e2e_live_test::fixtures::{Harness, Sandbox};

/// Skip-safe handle. `None` => the test must early-return (run outside eos-e2e).
pub fn harness() -> Option<&'static Harness> {
    Harness::get()
}
