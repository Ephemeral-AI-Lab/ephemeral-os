use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

use serde_json::Value;

use crate::command::{
    CommandId, CommandLinesOutput, CommandServiceError, CommandStatus, CommandStream,
    CommandTranscriptRow,
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
    pub(crate) fn window(
        &self,
        command_id: &CommandId,
        offset: u64,
        limit: usize,
    ) -> Result<CommandTranscriptWindow, CommandServiceError> {
        required_transcript_window(self.transcript_path.as_deref(), offset, limit).map_err(
            |error| CommandServiceError::CommandTranscriptUnavailable {
                command_id: command_id.clone(),
                path: self.transcript_path.clone(),
                error,
            },
        )
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
    let retained = path.and_then(|path| read_transcript(path).ok());
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

fn required_transcript_window(
    path: Option<&Path>,
    offset: u64,
    limit: usize,
) -> Result<CommandTranscriptWindow, String> {
    let path = path.ok_or_else(|| "retained transcript path is missing".to_owned())?;
    let retained = read_transcript(path)?;
    Ok(window_rows(
        parse_transcript_rows(&retained.text, retained.truncated_before),
        offset,
        limit,
        retained.truncated_before,
    ))
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

fn read_transcript(path: &Path) -> Result<RetainedTranscript, String> {
    if path.as_os_str().is_empty() {
        return Err("transcript path is empty".to_owned());
    }
    let mut file =
        File::open(path).map_err(|error| format!("open transcript {}: {error}", path.display()))?;
    let len = file
        .metadata()
        .map_err(|error| format!("read transcript metadata {}: {error}", path.display()))?
        .len();
    let bounded_start = len.saturating_sub(MAX_TRANSCRIPT_WINDOW_BYTES);
    let retained_start = line_aligned_retained_start(&mut file, bounded_start, len)
        .map_err(|error| format!("align transcript window {}: {error}", path.display()))?;
    let truncated_before = count_rows_before(&mut file, retained_start)
        .map_err(|error| format!("count transcript rows {}: {error}", path.display()))?;
    file.seek(SeekFrom::Start(retained_start))
        .map_err(|error| format!("seek transcript {}: {error}", path.display()))?;
    let read_len = len.saturating_sub(retained_start);
    let mut bytes = Vec::new();
    file.take(read_len)
        .read_to_end(&mut bytes)
        .map_err(|error| format!("read transcript {}: {error}", path.display()))?;
    Ok(RetainedTranscript {
        text: String::from_utf8_lossy(&bytes).into_owned(),
        truncated_before,
    })
}

fn line_aligned_retained_start(
    file: &mut File,
    bounded_start: u64,
    len: u64,
) -> Result<u64, String> {
    if bounded_start == 0 {
        return Ok(0);
    }
    file.seek(SeekFrom::Start(bounded_start.saturating_sub(1)))
        .map_err(|error| error.to_string())?;
    let mut previous = [0_u8; 1];
    file.read_exact(&mut previous)
        .map_err(|error| error.to_string())?;
    if previous[0] == b'\n' {
        return Ok(bounded_start);
    }

    file.seek(SeekFrom::Start(bounded_start))
        .map_err(|error| error.to_string())?;
    let mut current = bounded_start;
    let mut buffer = [0_u8; 8192];
    while current < len {
        let read = file.read(&mut buffer).map_err(|error| error.to_string())?;
        if read == 0 {
            break;
        }
        if let Some(index) = buffer[..read].iter().position(|byte| *byte == b'\n') {
            return Ok(current
                .saturating_add(u64::try_from(index).map_err(|error| error.to_string())?)
                .saturating_add(1));
        }
        current = current.saturating_add(u64::try_from(read).map_err(|error| error.to_string())?);
    }
    Ok(len)
}

fn count_rows_before(file: &mut File, end: u64) -> Result<u64, String> {
    if end == 0 {
        return Ok(0);
    }
    file.seek(SeekFrom::Start(0))
        .map_err(|error| error.to_string())?;
    let mut remaining = end;
    let mut rows = 0_u64;
    let mut last_byte = None;
    let mut buffer = [0_u8; 8192];
    while remaining > 0 {
        let read_limit = usize::try_from(remaining.min(buffer.len() as u64))
            .map_err(|error| error.to_string())?;
        let read = file
            .read(&mut buffer[..read_limit])
            .map_err(|error| error.to_string())?;
        if read == 0 {
            break;
        }
        rows = rows.saturating_add(
            u64::try_from(buffer[..read].iter().filter(|byte| **byte == b'\n').count())
                .map_err(|error| error.to_string())?,
        );
        last_byte = Some(buffer[read - 1]);
        remaining =
            remaining.saturating_sub(u64::try_from(read).map_err(|error| error.to_string())?);
    }
    if last_byte.is_some_and(|byte| byte != b'\n') {
        rows = rows.saturating_add(1);
    }
    Ok(rows)
}

fn parse_transcript_rows(text: &str, fallback_start_offset: u64) -> Vec<CommandTranscriptRow> {
    let mut rows = Vec::new();
    for raw_line in text.lines() {
        match parse_json_row(raw_line) {
            Ok(Some(row)) => rows.push(row),
            Ok(None) => rows.push(CommandTranscriptRow {
                offset: fallback_start_offset
                    .saturating_add(u64::try_from(rows.len()).unwrap_or(u64::MAX)),
                stream: CommandStream::Stdout,
                text: strip_transcript_timestamp(raw_line).to_owned(),
            }),
            Err(()) => {}
        }
    }
    rows
}

fn parse_json_row(raw_line: &str) -> Result<Option<CommandTranscriptRow>, ()> {
    let value = match serde_json::from_str::<Value>(raw_line) {
        Ok(value) => value,
        Err(_) if looks_like_structured_row(raw_line) => return Err(()),
        Err(_) => return Ok(None),
    };
    let Value::Object(object) = &value else {
        return Ok(None);
    };
    if !(object.contains_key("offset")
        && object.contains_key("stream")
        && object.contains_key("text"))
    {
        return Ok(None);
    }
    let offset = value.get("offset").and_then(Value::as_u64).ok_or(())?;
    let stream = match value.get("stream").and_then(Value::as_str).ok_or(())? {
        "stdout" => CommandStream::Stdout,
        "stderr" => CommandStream::Stderr,
        _ => return Err(()),
    };
    let text = value
        .get("text")
        .and_then(Value::as_str)
        .ok_or(())?
        .to_owned();
    Ok(Some(CommandTranscriptRow {
        offset,
        stream,
        text,
    }))
}

fn looks_like_structured_row(raw_line: &str) -> bool {
    let trimmed = raw_line.trim_start();
    trimmed.starts_with('{')
        && trimmed.contains("\"offset\"")
        && trimmed.contains("\"stream\"")
        && trimmed.contains("\"text\"")
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
