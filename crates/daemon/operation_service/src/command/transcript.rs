use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

use serde_json::Value;

use crate::command::{
    CommandId, CommandLinesOutput, CommandStatus, CommandStream, CommandTranscriptRow,
};

use super::process_store::{CommandTranscriptStore, RetainedCommandTranscript};

const MAX_TRANSCRIPT_WINDOW_BYTES: u64 = 1024 * 1024;

#[derive(Debug)]
pub(crate) struct CommandTranscriptWindow {
    offset: u64,
    next_offset: u64,
    total_lines: u64,
    truncated_before: u64,
    output_truncated: bool,
    output: Vec<CommandTranscriptRow>,
}

impl CommandTranscriptStore {
    #[must_use]
    pub(crate) fn window(&self, offset: u64, limit: usize) -> CommandTranscriptWindow {
        transcript_window(self.transcript_path.as_deref(), offset, limit)
    }
}

impl RetainedCommandTranscript {
    #[must_use]
    pub(crate) fn window(&self, offset: u64, limit: usize) -> CommandTranscriptWindow {
        transcript_window(self.transcript_path.as_deref(), offset, limit)
    }
}

impl CommandTranscriptWindow {
    #[must_use]
    pub(crate) fn into_output(
        self,
        command_id: CommandId,
        status: CommandStatus,
        exit_code: Option<i64>,
    ) -> CommandLinesOutput {
        CommandLinesOutput {
            command_id,
            status,
            exit_code,
            offset: self.offset,
            next_offset: self.next_offset,
            total_lines: self.total_lines,
            truncated_before: self.truncated_before,
            output_truncated: self.output_truncated,
            output: self.output,
        }
    }
}

fn transcript_window(path: Option<&Path>, offset: u64, limit: usize) -> CommandTranscriptWindow {
    let retained = path.and_then(read_transcript);
    let (rows, truncated_before) = retained.map_or_else(
        || (Vec::new(), 0),
        |retained| {
            (
                parse_transcript_rows(&retained.text, retained.truncated_before),
                retained.truncated_before,
            )
        },
    );
    window_rows(rows, offset, limit, truncated_before)
}

fn window_rows(
    rows: Vec<CommandTranscriptRow>,
    offset: u64,
    limit: usize,
    truncated_before: u64,
) -> CommandTranscriptWindow {
    let total_lines = rows
        .iter()
        .map(|row| row.offset.saturating_add(1))
        .max()
        .unwrap_or(truncated_before)
        .max(truncated_before);
    let output = rows
        .into_iter()
        .filter(|row| row.offset >= offset)
        .take(limit)
        .collect::<Vec<_>>();
    let next_offset = output.last().map_or_else(
        || {
            if offset < truncated_before {
                truncated_before
            } else {
                offset
            }
        },
        |row| row.offset.saturating_add(1),
    );
    CommandTranscriptWindow {
        offset,
        next_offset,
        total_lines,
        truncated_before,
        output_truncated: offset < truncated_before || next_offset < total_lines,
        output,
    }
}

struct RetainedTranscript {
    text: String,
    truncated_before: u64,
}

fn read_transcript(path: &Path) -> Option<RetainedTranscript> {
    if path.as_os_str().is_empty() {
        return None;
    }
    let mut file = File::open(path).ok()?;
    let len = file.metadata().ok()?.len();
    let bounded_start = len.saturating_sub(MAX_TRANSCRIPT_WINDOW_BYTES);
    let retained_start = line_aligned_retained_start(&mut file, bounded_start, len)?;
    let truncated_before = count_rows_before(&mut file, retained_start)?;
    file.seek(SeekFrom::Start(retained_start)).ok()?;
    let read_len = len.saturating_sub(retained_start);
    let mut bytes = Vec::new();
    file.take(read_len).read_to_end(&mut bytes).ok()?;
    Some(RetainedTranscript {
        text: String::from_utf8_lossy(&bytes).into_owned(),
        truncated_before,
    })
}

fn line_aligned_retained_start(file: &mut File, bounded_start: u64, len: u64) -> Option<u64> {
    if bounded_start == 0 {
        return Some(0);
    }
    file.seek(SeekFrom::Start(bounded_start.saturating_sub(1)))
        .ok()?;
    let mut previous = [0_u8; 1];
    file.read_exact(&mut previous).ok()?;
    if previous[0] == b'\n' {
        return Some(bounded_start);
    }

    file.seek(SeekFrom::Start(bounded_start)).ok()?;
    let mut current = bounded_start;
    let mut buffer = [0_u8; 8192];
    while current < len {
        let read = file.read(&mut buffer).ok()?;
        if read == 0 {
            break;
        }
        if let Some(index) = buffer[..read].iter().position(|byte| *byte == b'\n') {
            return Some(
                current
                    .saturating_add(u64::try_from(index).ok()?)
                    .saturating_add(1),
            );
        }
        current = current.saturating_add(u64::try_from(read).ok()?);
    }
    Some(len)
}

fn count_rows_before(file: &mut File, end: u64) -> Option<u64> {
    if end == 0 {
        return Some(0);
    }
    file.seek(SeekFrom::Start(0)).ok()?;
    let mut remaining = end;
    let mut rows = 0_u64;
    let mut last_byte = None;
    let mut buffer = [0_u8; 8192];
    while remaining > 0 {
        let read_limit = usize::try_from(remaining.min(buffer.len() as u64)).ok()?;
        let read = file.read(&mut buffer[..read_limit]).ok()?;
        if read == 0 {
            break;
        }
        rows = rows.saturating_add(
            u64::try_from(buffer[..read].iter().filter(|byte| **byte == b'\n').count()).ok()?,
        );
        last_byte = Some(buffer[read - 1]);
        remaining = remaining.saturating_sub(u64::try_from(read).ok()?);
    }
    if last_byte.is_some_and(|byte| byte != b'\n') {
        rows = rows.saturating_add(1);
    }
    Some(rows)
}

fn parse_transcript_rows(text: &str, fallback_start_offset: u64) -> Vec<CommandTranscriptRow> {
    let mut rows = Vec::new();
    for raw_line in text.lines() {
        if let Some(row) = parse_json_row(raw_line) {
            rows.push(row);
            continue;
        }
        rows.push(CommandTranscriptRow {
            offset: fallback_start_offset
                .saturating_add(u64::try_from(rows.len()).unwrap_or(u64::MAX)),
            stream: CommandStream::Stdout,
            text: strip_transcript_timestamp(raw_line).to_owned(),
        });
    }
    rows
}

fn parse_json_row(raw_line: &str) -> Option<CommandTranscriptRow> {
    let value = serde_json::from_str::<Value>(raw_line).ok()?;
    let offset = value.get("offset")?.as_u64()?;
    let stream = match value.get("stream")?.as_str()? {
        "stdout" => CommandStream::Stdout,
        "stderr" => CommandStream::Stderr,
        _ => return None,
    };
    let text = value.get("text")?.as_str()?.to_owned();
    Some(CommandTranscriptRow {
        offset,
        stream,
        text,
    })
}

fn strip_transcript_timestamp(line: &str) -> &str {
    let Some(rest) = line.strip_prefix('[') else {
        return line;
    };
    let Some((timestamp, text)) = rest.split_once("] ") else {
        return line;
    };
    if is_transcript_timestamp(timestamp) {
        text
    } else {
        line
    }
}

fn is_transcript_timestamp(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.len() < 24 {
        return false;
    }
    let has_timestamp_shape = bytes[4] == b'-'
        && bytes[7] == b'-'
        && bytes[10] == b'T'
        && bytes[13] == b':'
        && bytes[16] == b':'
        && bytes[19] == b'.';

    has_timestamp_shape && (value.ends_with('Z') || has_fixed_offset_suffix(value))
}

fn has_fixed_offset_suffix(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.len() < 29 {
        return false;
    }
    let suffix = &bytes[bytes.len() - 6..];
    matches!(suffix[0], b'+' | b'-')
        && suffix[3] == b':'
        && suffix[1].is_ascii_digit()
        && suffix[2].is_ascii_digit()
        && suffix[4].is_ascii_digit()
        && suffix[5].is_ascii_digit()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn transcript_window_parses_json_rows_with_streams() {
        let rows = parse_transcript_rows(
            "{\"offset\":0,\"stream\":\"stdout\",\"text\":\"out\"}\n\
             {\"offset\":1,\"stream\":\"stderr\",\"text\":\"err\"}\n",
            0,
        );

        assert_eq!(
            rows,
            vec![
                CommandTranscriptRow {
                    offset: 0,
                    stream: CommandStream::Stdout,
                    text: "out".to_owned(),
                },
                CommandTranscriptRow {
                    offset: 1,
                    stream: CommandStream::Stderr,
                    text: "err".to_owned(),
                },
            ]
        );
    }

    #[test]
    fn transcript_window_strips_pty_timestamp_prefixes() {
        let rows = parse_transcript_rows(
            "[2026-06-18T01:02:03.004Z] first\n\
             [2026-06-18T09:02:03.004+08:00] second\n",
            0,
        );

        assert_eq!(
            rows,
            vec![
                CommandTranscriptRow {
                    offset: 0,
                    stream: CommandStream::Stdout,
                    text: "first".to_owned(),
                },
                CommandTranscriptRow {
                    offset: 1,
                    stream: CommandStream::Stdout,
                    text: "second".to_owned(),
                },
            ]
        );
    }

    #[test]
    fn transcript_window_reports_offsets_and_truncation() {
        let rows = vec![
            CommandTranscriptRow {
                offset: 0,
                stream: CommandStream::Stdout,
                text: "one".to_owned(),
            },
            CommandTranscriptRow {
                offset: 1,
                stream: CommandStream::Stdout,
                text: "two".to_owned(),
            },
            CommandTranscriptRow {
                offset: 2,
                stream: CommandStream::Stdout,
                text: "three".to_owned(),
            },
        ];

        let window = window_rows(rows, 1, 1, 0);

        assert_eq!(window.offset, 1);
        assert_eq!(window.next_offset, 2);
        assert_eq!(window.total_lines, 3);
        assert_eq!(window.truncated_before, 0);
        assert!(window.output_truncated);
        assert_eq!(
            window.output,
            vec![CommandTranscriptRow {
                offset: 1,
                stream: CommandStream::Stdout,
                text: "two".to_owned(),
            }]
        );
    }

    #[test]
    fn empty_transcript_window_keeps_requested_next_offset() {
        let window = window_rows(Vec::new(), 7, 10, 0);

        assert_eq!(window.offset, 7);
        assert_eq!(window.next_offset, 7);
        assert_eq!(window.total_lines, 0);
        assert!(!window.output_truncated);
        assert!(window.output.is_empty());
    }

    #[test]
    fn transcript_window_reports_bounded_file_truncation() {
        let path = std::env::temp_dir().join(format!(
            "operation-service-transcript-window-{}-bounded.log",
            std::process::id()
        ));
        let mut transcript = String::from("old-one\nold-two\n");
        transcript.push_str(&"x".repeat(MAX_TRANSCRIPT_WINDOW_BYTES as usize + 128));
        transcript.push('\n');
        transcript.push_str("kept-one\nkept-two\n");
        std::fs::write(&path, transcript).expect("test transcript write succeeds");

        let window = transcript_window(Some(&path), 0, 10);

        assert_eq!(window.offset, 0);
        assert_eq!(window.truncated_before, 3);
        assert_eq!(window.total_lines, 5);
        assert_eq!(window.next_offset, 5);
        assert!(window.output_truncated);
        assert_eq!(
            window.output,
            vec![
                CommandTranscriptRow {
                    offset: 3,
                    stream: CommandStream::Stdout,
                    text: "kept-one".to_owned(),
                },
                CommandTranscriptRow {
                    offset: 4,
                    stream: CommandStream::Stdout,
                    text: "kept-two".to_owned(),
                },
            ]
        );

        let _ = std::fs::remove_file(path);
    }
}
