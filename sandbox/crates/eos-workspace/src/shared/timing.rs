use std::collections::HashMap;
use std::time::Instant;

pub fn record_phase_ms(phases_ms: &mut HashMap<String, f64>, phase: &str, started_at: Instant) {
    phases_ms.insert(
        phase.to_owned(),
        started_at.elapsed().as_secs_f64() * 1000.0,
    );
}
