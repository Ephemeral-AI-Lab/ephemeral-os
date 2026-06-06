//! Small process resource sampler for agent-core obs rows.

#[cfg(unix)]
use std::process::Command;
use std::sync::OnceLock;
use std::time::Instant;

use eos_types::JsonObject;
use serde_json::json;

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct ProcessResourceSample {
    sampled_at_monotonic_s: f64,
    rss_bytes: Option<i64>,
    cpu_user_s: Option<f64>,
    cpu_system_s: Option<f64>,
    io_read_bytes: Option<i64>,
    io_write_bytes: Option<i64>,
    io_read_ops: Option<i64>,
    io_write_ops: Option<i64>,
}

impl ProcessResourceSample {
    fn new(sampled_at_monotonic_s: f64) -> Self {
        Self {
            sampled_at_monotonic_s,
            rss_bytes: None,
            cpu_user_s: None,
            cpu_system_s: None,
            io_read_bytes: None,
            io_write_bytes: None,
            io_read_ops: None,
            io_write_ops: None,
        }
    }

    fn has_values(&self) -> bool {
        self.rss_bytes.is_some()
            || self.cpu_user_s.is_some()
            || self.cpu_system_s.is_some()
            || self.io_read_bytes.is_some()
            || self.io_write_bytes.is_some()
            || self.io_read_ops.is_some()
            || self.io_write_ops.is_some()
    }

    pub(crate) fn into_payload(self) -> JsonObject {
        let mut payload = JsonObject::new();
        payload.insert(
            "sampled_at_monotonic_s".to_owned(),
            json!(self.sampled_at_monotonic_s),
        );
        insert_i64(&mut payload, "rss_bytes", self.rss_bytes);
        insert_f64(&mut payload, "cpu_user_s", self.cpu_user_s);
        insert_f64(&mut payload, "cpu_system_s", self.cpu_system_s);
        insert_i64(&mut payload, "io_read_bytes", self.io_read_bytes);
        insert_i64(&mut payload, "io_write_bytes", self.io_write_bytes);
        insert_i64(&mut payload, "io_read_ops", self.io_read_ops);
        insert_i64(&mut payload, "io_write_ops", self.io_write_ops);
        payload
    }
}

pub(crate) fn capture_process_resource_sample() -> Option<ProcessResourceSample> {
    let mut sample = ProcessResourceSample::new(monotonic_s());
    fill_from_ps(&mut sample);
    fill_from_linux_proc(&mut sample);
    sample.has_values().then_some(sample)
}

fn insert_i64(payload: &mut JsonObject, key: &str, value: Option<i64>) {
    if let Some(value) = value {
        payload.insert(key.to_owned(), json!(value));
    }
}

fn insert_f64(payload: &mut JsonObject, key: &str, value: Option<f64>) {
    if let Some(value) = value {
        payload.insert(key.to_owned(), json!(value));
    }
}

fn monotonic_s() -> f64 {
    static START: OnceLock<Instant> = OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_secs_f64()
}

#[cfg(unix)]
fn fill_from_ps(sample: &mut ProcessResourceSample) {
    let pid = std::process::id().to_string();
    let Ok(output) = Command::new("ps")
        .args(["-o", "rss=", "-o", "utime=", "-o", "stime=", "-p"])
        .arg(pid)
        .output()
    else {
        return;
    };
    if !output.status.success() {
        return;
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    let Some(line) = stdout.lines().find(|line| !line.trim().is_empty()) else {
        return;
    };
    apply_ps_line(sample, line);
}

#[cfg(not(unix))]
fn fill_from_ps(_sample: &mut ProcessResourceSample) {}

fn apply_ps_line(sample: &mut ProcessResourceSample, line: &str) {
    let mut fields = line.split_whitespace();
    let Some(rss_kib) = fields.next().and_then(parse_i64) else {
        return;
    };
    sample.rss_bytes = rss_kib.checked_mul(1024);
    sample.cpu_user_s = fields.next().and_then(parse_ps_time);
    sample.cpu_system_s = fields.next().and_then(parse_ps_time);
}

#[cfg(target_os = "linux")]
fn fill_from_linux_proc(sample: &mut ProcessResourceSample) {
    if sample.rss_bytes.is_none() {
        fill_linux_rss(sample);
    }
    let Ok(raw) = std::fs::read_to_string("/proc/self/io") else {
        return;
    };
    for line in raw.lines() {
        let Some((name, value)) = line.split_once(':') else {
            continue;
        };
        let Some(value) = parse_i64(value.trim()) else {
            continue;
        };
        match name {
            "read_bytes" => sample.io_read_bytes = Some(value),
            "write_bytes" => sample.io_write_bytes = Some(value),
            "syscr" => sample.io_read_ops = Some(value),
            "syscw" => sample.io_write_ops = Some(value),
            _ => {}
        }
    }
}

#[cfg(not(target_os = "linux"))]
fn fill_from_linux_proc(_sample: &mut ProcessResourceSample) {}

#[cfg(target_os = "linux")]
fn fill_linux_rss(sample: &mut ProcessResourceSample) {
    let Ok(raw) = std::fs::read_to_string("/proc/self/status") else {
        return;
    };
    for line in raw.lines() {
        let Some(value) = line.strip_prefix("VmRSS:") else {
            continue;
        };
        let Some(kib) = value.split_whitespace().next().and_then(parse_i64) else {
            continue;
        };
        sample.rss_bytes = kib.checked_mul(1024);
        return;
    }
}

fn parse_i64(raw: &str) -> Option<i64> {
    raw.parse::<i64>().ok()
}

fn parse_ps_time(raw: &str) -> Option<f64> {
    let (day_s, clock) = if let Some((days, rest)) = raw.split_once('-') {
        (days.parse::<f64>().ok()? * 86_400.0, rest)
    } else {
        (0.0, raw)
    };
    let parts = clock.split(':').collect::<Vec<_>>();
    let seconds = match parts.as_slice() {
        [seconds] => seconds.parse::<f64>().ok()?,
        [minutes, seconds] => minutes.parse::<f64>().ok()? * 60.0 + seconds.parse::<f64>().ok()?,
        [hours, minutes, seconds] => {
            hours.parse::<f64>().ok()? * 3600.0
                + minutes.parse::<f64>().ok()? * 60.0
                + seconds.parse::<f64>().ok()?
        }
        _ => return None,
    };
    Some(day_s + seconds)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_ps_time_accepts_common_ps_shapes() {
        assert_eq!(parse_ps_time("0:00.01"), Some(0.01));
        assert_eq!(parse_ps_time("01:02:03"), Some(3723.0));
        assert_eq!(parse_ps_time("2-01:02:03.5"), Some(176_523.5));
    }

    #[test]
    fn ps_line_populates_rss_and_cpu_fields() {
        let mut sample = ProcessResourceSample::new(7.0);

        apply_ps_line(&mut sample, "  1360   0:00.02   0:00.01");

        assert_eq!(sample.rss_bytes, Some(1_392_640));
        assert_eq!(sample.cpu_user_s, Some(0.02));
        assert_eq!(sample.cpu_system_s, Some(0.01));
        assert!(sample.has_values());
    }

    #[test]
    fn payload_omits_unavailable_values() {
        let mut sample = ProcessResourceSample::new(3.0);
        sample.rss_bytes = Some(4096);

        let payload = sample.into_payload();

        assert_eq!(payload.get("sampled_at_monotonic_s"), Some(&json!(3.0)));
        assert_eq!(payload.get("rss_bytes"), Some(&json!(4096)));
        assert!(payload.get("io_read_bytes").is_none());
    }
}
