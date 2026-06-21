use std::thread;
use std::time::{Duration, Instant};

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

const QUIET_MS: u64 = 50;

pub fn wait_for_yield<T, S>(command: &S, yield_time_ms: u64, start_offset: u64) -> WaitOutcome<T>
where
    S: CommandWaitTarget<T> + ?Sized,
{
    let started = Instant::now();
    let deadline = started + Duration::from_millis(yield_time_ms);
    let (mut last_off, mut last_change) = (start_offset, started);
    loop {
        if let Some(result) = command.take_exit() {
            return WaitOutcome::Completed(result);
        }
        let now = Instant::now();
        let off = command.transcript_len();
        if off != last_off {
            last_off = off;
            last_change = now;
        }
        if off > start_offset && now.duration_since(last_change) >= Duration::from_millis(QUIET_MS)
        {
            return WaitOutcome::Running(command.read_output_since(start_offset));
        }
        if now >= deadline {
            return WaitOutcome::Running(command.read_output_since(start_offset));
        }
        thread::sleep(Duration::from_millis(5));
    }
}
