use time::OffsetDateTime;

pub(crate) struct TranscriptTimestampPrefixer {
    at_line_start: bool,
}

impl TranscriptTimestampPrefixer {
    pub(crate) const fn new() -> Self {
        Self {
            at_line_start: true,
        }
    }

    pub(crate) fn prefix(&mut self, bytes: &[u8]) -> Vec<u8> {
        self.prefix_at(bytes, OffsetDateTime::now_utc())
    }

    fn prefix_at(&mut self, bytes: &[u8], now: OffsetDateTime) -> Vec<u8> {
        let mut out = Vec::with_capacity(bytes.len());
        for byte in bytes {
            if self.at_line_start {
                out.extend_from_slice(format_timestamp_prefix_at(now).as_bytes());
                self.at_line_start = false;
            }
            out.push(*byte);
            if *byte == b'\n' {
                self.at_line_start = true;
            }
        }
        out
    }
}

fn format_timestamp_prefix_at(now: OffsetDateTime) -> String {
    format!(
        "[{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{millisecond:03}Z] ",
        year = now.year(),
        month = now.month() as u8,
        day = now.day(),
        hour = now.hour(),
        minute = now.minute(),
        second = now.second(),
        millisecond = now.millisecond(),
    )
}
