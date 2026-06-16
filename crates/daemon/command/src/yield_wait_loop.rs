use std::thread;
use std::time::{Duration, Instant};

use crate::CommandConfig;

pub trait CommandWaitTarget<T> {
    fn take_exit(&self) -> Option<T>;
    fn transcript_len(&self) -> u64;
    fn read_output_since(&self, start_offset: u64) -> String;
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WaitOutcome<T> {
    Completed(T),
    Running(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WaitYieldReason {
    Completed,
    OutputQuiet,
    Deadline,
}

impl WaitYieldReason {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Completed => "completed",
            Self::OutputQuiet => "output_quiet",
            Self::Deadline => "deadline",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WaitTiming {
    pub reason: WaitYieldReason,
    pub elapsed_ms: u64,
    pub first_output_ms: Option<u64>,
    pub last_output_ms: Option<u64>,
    pub quiet_ms: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WaitReport<T> {
    pub outcome: WaitOutcome<T>,
    pub timing: WaitTiming,
}

pub fn wait_for_yield<T, S>(
    command: &S,
    config: &CommandConfig,
    yield_time_ms: u64,
    start_offset: u64,
) -> WaitOutcome<T>
where
    S: CommandWaitTarget<T> + ?Sized,
{
    wait_for_yield_with_timing(command, config, yield_time_ms, start_offset).outcome
}

pub fn wait_for_yield_with_timing<T, S>(
    command: &S,
    config: &CommandConfig,
    yield_time_ms: u64,
    start_offset: u64,
) -> WaitReport<T>
where
    S: CommandWaitTarget<T> + ?Sized,
{
    let started = Instant::now();
    let deadline = started + Duration::from_millis(yield_time_ms);
    let (mut last_off, mut last_change) = (start_offset, started);
    let mut first_output = None;
    loop {
        if let Some(result) = command.take_exit() {
            return WaitReport {
                outcome: WaitOutcome::Completed(result),
                timing: timing(
                    started,
                    first_output,
                    last_change,
                    WaitYieldReason::Completed,
                ),
            };
        }
        let now = Instant::now();
        let off = command.transcript_len();
        if off != last_off {
            last_off = off;
            last_change = now;
            if off > start_offset && first_output.is_none() {
                first_output = Some(now);
            }
        }
        if off > start_offset
            && now.duration_since(last_change) >= Duration::from_millis(config.quiet_ms)
        {
            return WaitReport {
                outcome: WaitOutcome::Running(command.read_output_since(start_offset)),
                timing: timing(
                    started,
                    first_output,
                    last_change,
                    WaitYieldReason::OutputQuiet,
                ),
            };
        }
        if now >= deadline {
            return WaitReport {
                outcome: WaitOutcome::Running(command.read_output_since(start_offset)),
                timing: timing(
                    started,
                    first_output,
                    last_change,
                    WaitYieldReason::Deadline,
                ),
            };
        }
        thread::sleep(Duration::from_millis(5));
    }
}

fn timing(
    started: Instant,
    first_output: Option<Instant>,
    last_change: Instant,
    reason: WaitYieldReason,
) -> WaitTiming {
    let now = Instant::now();
    WaitTiming {
        reason,
        elapsed_ms: elapsed_ms(started, now),
        first_output_ms: first_output.map(|instant| elapsed_ms(started, instant)),
        last_output_ms: first_output.map(|_| elapsed_ms(started, last_change)),
        quiet_ms: first_output.map(|_| elapsed_ms(last_change, now)),
    }
}

fn elapsed_ms(from: Instant, to: Instant) -> u64 {
    u64::try_from(to.duration_since(from).as_millis()).unwrap_or(u64::MAX)
}

#[cfg(test)]
#[path = "../tests/unit/yield_wait_loop.rs"]
mod tests;
