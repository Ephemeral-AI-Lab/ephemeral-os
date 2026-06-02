//! Incremental Server-Sent-Events frame splitting.
//!
//! [`SseFrameSplitter`] is a pure, allocation-light state machine: bytes are
//! pushed in arbitrary chunks and complete frames (the lines between blank-line
//! boundaries) are emitted as they close, with a final [`SseFrameSplitter::finish`]
//! flush for a stream that ends without a trailing blank line — every captured
//! provider fixture does. It tolerates both `\n` and `\r\n` line endings. No
//! full-body buffering: only the bytes of the in-progress frame are retained
//! (`mem-zero-copy` intent — the carry buffer never grows past one frame).
//!
//! [`frame_stream`] adapts the splitter onto a byte `Stream`, and [`frame_data`]
//! extracts the SSE `data:` payload from a framed block. JSON decoding of that
//! payload is the provider modules' job, not this one's.

use bytes::Bytes;
use eos_types::JsonObject;
use futures::{Stream, StreamExt};
use serde_json::Value;

use crate::error::ProviderError;

/// A pushable SSE frame splitter.
///
/// A *frame* is the ordered non-blank logical lines between two blank lines,
/// joined by `\n`. State persists across `push` calls so a frame may span any
/// number of byte chunks.
#[derive(Debug, Default)]
pub(crate) struct SseFrameSplitter {
    /// Bytes received but not yet terminated by a `\n`.
    buf: Vec<u8>,
    /// Logical lines of the frame currently being assembled.
    current: Vec<String>,
}

impl SseFrameSplitter {
    /// Push a byte chunk, appending any newly-completed frames to `out`.
    pub(crate) fn push(&mut self, chunk: &[u8], out: &mut Vec<String>) {
        self.buf.extend_from_slice(chunk);
        while let Some(nl) = self.buf.iter().position(|&b| b == b'\n') {
            let line_bytes: Vec<u8> = self.buf.drain(..=nl).collect();
            // Drop the terminating '\n'; a multibyte UTF-8 char never contains a
            // '\n' (0x0A) byte, so splitting here never bisects a code point.
            let line = decode_line(&line_bytes[..line_bytes.len() - 1]);
            self.consume_line(line, out);
        }
    }

    /// Flush a trailing partial line and any in-progress frame at end-of-stream.
    pub(crate) fn finish(&mut self, out: &mut Vec<String>) {
        if !self.buf.is_empty() {
            let line = decode_line(&self.buf);
            self.buf.clear();
            if !line.is_empty() {
                self.current.push(line);
            }
        }
        if !self.current.is_empty() {
            out.push(self.current.join("\n"));
            self.current.clear();
        }
    }

    fn consume_line(&mut self, line: String, out: &mut Vec<String>) {
        if line.is_empty() {
            if !self.current.is_empty() {
                out.push(self.current.join("\n"));
                self.current.clear();
            }
        } else {
            self.current.push(line);
        }
    }
}

/// Decode one logical line, stripping a trailing `\r` (CRLF tolerance).
fn decode_line(bytes: &[u8]) -> String {
    let mut line = String::from_utf8_lossy(bytes).into_owned();
    if line.ends_with('\r') {
        line.pop();
    }
    line
}

/// Extract the concatenated SSE `data:` payload from a framed block, if any.
///
/// Multiple `data:` lines are joined with `\n` (SSE multi-line data). `event:`,
/// comment (`:`), and other field lines are ignored — provider payloads carry
/// their own `type` discriminator in the JSON.
pub(crate) fn frame_data(frame: &str) -> Option<String> {
    let mut data: Option<String> = None;
    for line in frame.split('\n') {
        if let Some(rest) = line.strip_prefix("data:") {
            let rest = rest.strip_prefix(' ').unwrap_or(rest);
            match &mut data {
                Some(acc) => {
                    acc.push('\n');
                    acc.push_str(rest);
                }
                None => data = Some(rest.to_owned()),
            }
        }
    }
    data
}

/// Parse one SSE frame's `data:` payload into a JSON value.
///
/// Returns `Ok(None)` to skip the frame (no `data:` line, or the `[DONE]`
/// sentinel), `Ok(Some(value))` for a parsed payload, or `Err` for malformed
/// JSON. On the error path it logs content-free context only (frame index +
/// request-id; never the payload, system prompt, tool input, or auth — §8.7)
/// and the returned `Decode` error carries `request_id` (§8.8). Shared by both
/// provider decoders so the per-frame preamble is defined once.
pub(crate) fn parse_sse_value(
    frame: &str,
    request_id: &Option<String>,
    provider: &str,
    frame_index: usize,
) -> Result<Option<Value>, ProviderError> {
    let Some(data) = frame_data(frame) else {
        return Ok(None);
    };
    if data == "[DONE]" {
        return Ok(None);
    }
    serde_json::from_str(&data).map(Some).map_err(|_| {
        tracing::warn!(
            request_id = request_id.as_deref().unwrap_or_default(),
            frame_index,
            "{provider} sse frame failed to parse"
        );
        ProviderError::decode(
            request_id.clone(),
            format!("{provider} sse frame is not valid json"),
        )
    })
}

/// Adapt a byte `Stream` into a stream of SSE frames, flushing the final frame
/// at end-of-stream. A byte-stream error is forwarded verbatim and ends the
/// frame stream.
pub(crate) fn frame_stream<S>(bytes: S) -> impl Stream<Item = Result<String, ProviderError>>
where
    S: Stream<Item = Result<Bytes, ProviderError>>,
{
    async_stream::stream! {
        let mut splitter = SseFrameSplitter::default();
        futures::pin_mut!(bytes);
        while let Some(chunk) = bytes.next().await {
            match chunk {
                Ok(b) => {
                    let mut out = Vec::new();
                    splitter.push(&b, &mut out);
                    for frame in out {
                        yield Ok(frame);
                    }
                }
                Err(e) => {
                    yield Err(e);
                    return;
                }
            }
        }
        let mut out = Vec::new();
        splitter.finish(&mut out);
        for frame in out {
            yield Ok(frame);
        }
    }
}

/// Borrow a nested JSON value by string-key path, returning `Value::Null` for
/// any missing segment (never panics — `serde_json::Value` indexing is total).
fn at<'v>(value: &'v Value, path: &[&str]) -> &'v Value {
    let mut cursor = value;
    for key in path {
        cursor = &cursor[*key];
    }
    cursor
}

/// Read a string at a JSON path, defaulting to empty.
pub(crate) fn json_str(value: &Value, path: &[&str]) -> String {
    at(value, path).as_str().unwrap_or_default().to_owned()
}

/// Read a `u32` at a JSON path, defaulting to 0 (out-of-range is clamped).
pub(crate) fn json_u32(value: &Value, path: &[&str]) -> u32 {
    at(value, path)
        .as_u64()
        .unwrap_or(0)
        .try_into()
        .unwrap_or(u32::MAX)
}

/// Read a `usize` at a JSON path, defaulting to 0.
pub(crate) fn json_usize(value: &Value, path: &[&str]) -> usize {
    usize::try_from(at(value, path).as_u64().unwrap_or(0)).unwrap_or(usize::MAX)
}

/// Parse accumulated tool-call argument JSON into an object. A malformed or
/// non-object payload yields `{}` (Anthropic parity — `_stream_once` falls back
/// to `{}` on `JSONDecodeError`; spec §8.4), never an error.
pub(crate) fn parse_tool_args(raw: &str) -> JsonObject {
    if raw.is_empty() {
        return JsonObject::new();
    }
    match serde_json::from_str::<Value>(raw) {
        Ok(Value::Object(map)) => map,
        _ => JsonObject::new(),
    }
}

/// Split a complete buffer into frames in one shot (test/utility helper).
#[cfg(test)]
pub(crate) fn split_all(buf: &[u8]) -> Vec<String> {
    let mut splitter = SseFrameSplitter::default();
    let mut out = Vec::new();
    splitter.push(buf, &mut out);
    splitter.finish(&mut out);
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    #[test]
    fn splits_blank_line_delimited_frames() {
        let buf = b"event: a\ndata: {\"x\":1}\n\nevent: b\ndata: {\"y\":2}\n\n";
        let frames = split_all(buf);
        assert_eq!(frames.len(), 2);
        assert_eq!(frames[0], "event: a\ndata: {\"x\":1}");
        assert_eq!(frame_data(&frames[0]).as_deref(), Some("{\"x\":1}"));
    }

    #[test]
    fn flushes_final_frame_without_trailing_blank_line() {
        // Fixtures end with `...message_stop\n` and no terminating blank line.
        let buf = b"data: {\"type\":\"message_stop\"}\n";
        let frames = split_all(buf);
        assert_eq!(frames, vec!["data: {\"type\":\"message_stop\"}".to_owned()]);
    }

    #[test]
    fn tolerates_crlf_line_endings() {
        let buf = b"event: a\r\ndata: hi\r\n\r\ndata: bye\r\n";
        let frames = split_all(buf);
        assert_eq!(
            frames,
            vec!["event: a\ndata: hi".to_owned(), "data: bye".to_owned()]
        );
        assert_eq!(frame_data(&frames[0]).as_deref(), Some("hi"));
    }

    #[test]
    fn concatenates_multi_line_data() {
        let frame = "data: line1\ndata: line2";
        assert_eq!(frame_data(frame).as_deref(), Some("line1\nline2"));
    }

    #[test]
    fn never_bisects_a_multibyte_char_across_chunks() {
        // `é` is 2 bytes, the emoji is 4 bytes. Splitting at every byte offset —
        // including mid-codepoint — must still reconstruct the identical frame.
        let frame = "data: caf\u{e9} \u{1f600}";
        let buf = format!("{frame}\n\n");
        let bytes = buf.as_bytes();
        for cut in 1..bytes.len() {
            let mut splitter = SseFrameSplitter::default();
            let mut out = Vec::new();
            splitter.push(&bytes[..cut], &mut out);
            splitter.push(&bytes[cut..], &mut out);
            splitter.finish(&mut out);
            assert_eq!(out, vec![frame.to_owned()], "cut at byte {cut}");
        }
    }

    // AC-llm-client-11: the splitter is invariant to chunk boundaries — feeding
    // the same buffer split at arbitrary positions reconstructs the identical
    // ordered frame list as a single-shot split.
    proptest! {
        #[test]
        fn frame_split_is_boundary_invariant(
            frames in prop::collection::vec("[a-zA-Z0-9 :{}\"]+", 1..6),
            cut_points in prop::collection::vec(any::<u16>(), 0..8),
        ) {
            // Build a valid multi-frame buffer: each frame is one non-blank line,
            // frames separated and terminated by blank lines.
            let mut buf = String::new();
            for f in &frames {
                buf.push_str(f);
                buf.push_str("\n\n");
            }
            let bytes = buf.as_bytes();
            let reference = split_all(bytes);

            // Feed the same bytes split at arbitrary cut points.
            let mut cuts: Vec<usize> = cut_points
                .iter()
                .map(|c| (*c as usize) % (bytes.len() + 1))
                .collect();
            cuts.push(0);
            cuts.push(bytes.len());
            cuts.sort_unstable();
            cuts.dedup();

            let mut splitter = SseFrameSplitter::default();
            let mut chunked = Vec::new();
            for win in cuts.windows(2) {
                splitter.push(&bytes[win[0]..win[1]], &mut chunked);
            }
            splitter.finish(&mut chunked);

            prop_assert_eq!(chunked, reference);
        }
    }
}
