use std::collections::VecDeque;
use std::sync::{Mutex, MutexGuard};

use crate::CommandSessionConfig;

pub struct CommandSessionOutput {
    chunks: Mutex<VecDeque<CommandSessionOutputChunk>>,
    bytes: Mutex<usize>,
    next_byte_offset: Mutex<u64>,
    spool_bytes: Mutex<u64>,
    spool_truncated: Mutex<bool>,
    ring_max_bytes: usize,
    spool_max_bytes: u64,
}

struct CommandSessionOutputChunk {
    start: u64,
    end: u64,
    text: String,
}

#[derive(Debug, Clone, Copy, Default)]
pub struct CommandSessionOutputCursor {
    next_seq: u64,
    next_byte_offset: u64,
}

impl CommandSessionOutput {
    #[must_use]
    pub fn new(config: &CommandSessionConfig) -> Self {
        Self {
            chunks: Mutex::new(VecDeque::new()),
            bytes: Mutex::new(0),
            next_byte_offset: Mutex::new(0),
            spool_bytes: Mutex::new(0),
            spool_truncated: Mutex::new(false),
            ring_max_bytes: config.output_ring_max_bytes,
            spool_max_bytes: config.output_spool_max_bytes,
        }
    }

    pub fn append(&self, text: String) {
        let byte_len = text.len();
        let mut next_byte_offset = lock(&self.next_byte_offset);
        let start = *next_byte_offset;
        let end = start.saturating_add(u64::try_from(byte_len).unwrap_or(u64::MAX));
        *next_byte_offset = end;
        drop(next_byte_offset);

        let mut chunks = lock(&self.chunks);
        let mut bytes = lock(&self.bytes);
        chunks.push_back(CommandSessionOutputChunk { start, end, text });
        *bytes += byte_len;
        while *bytes > self.ring_max_bytes {
            let Some(chunk) = chunks.pop_front() else {
                break;
            };
            *bytes = bytes.saturating_sub(chunk.text.len());
        }
    }

    pub fn read_since(
        &self,
        cursor: &mut CommandSessionOutputCursor,
        max_tokens: Option<u64>,
    ) -> String {
        let chunks = lock(&self.chunks);
        let Some(first) = chunks.front() else {
            return String::new();
        };
        let mut out = String::new();
        if cursor.next_byte_offset < first.start {
            out.push_str("[output truncated before cursor]\n");
            cursor.next_byte_offset = first.start;
        }
        let max_bytes = max_output_bytes(max_tokens);
        for chunk in chunks.iter() {
            if chunk.end <= cursor.next_byte_offset {
                continue;
            }
            let start_offset = cursor.next_byte_offset.saturating_sub(chunk.start);
            let start = usize::try_from(start_offset).unwrap_or(usize::MAX);
            let text = slice_from_byte(&chunk.text, start);
            if text.is_empty() {
                continue;
            }
            let remaining = max_bytes.saturating_sub(out.len());
            if remaining == 0 {
                break;
            }
            let take = floor_char_boundary(text, text.len().min(remaining));
            if take == 0 {
                break;
            }
            out.push_str(&text[..take]);
            cursor.next_byte_offset = cursor
                .next_byte_offset
                .saturating_add(u64::try_from(take).unwrap_or(u64::MAX));
            cursor.next_seq = cursor.next_seq.saturating_add(1);
            if take < text.len() {
                break;
            }
        }
        out
    }

    pub fn all_recent(&self, max_tokens: Option<u64>) -> String {
        let chunks = lock(&self.chunks);
        let mut out = String::new();
        let max_bytes = max_output_bytes(max_tokens);
        for chunk in chunks.iter() {
            let remaining = max_bytes.saturating_sub(out.len());
            if remaining == 0 {
                break;
            }
            let take = floor_char_boundary(&chunk.text, chunk.text.len().min(remaining));
            if take == 0 {
                break;
            }
            out.push_str(&chunk.text[..take]);
        }
        out
    }

    pub fn note_spooled(&self, bytes: u64) -> bool {
        let mut spool_bytes = lock(&self.spool_bytes);
        if *spool_bytes >= self.spool_max_bytes {
            *lock(&self.spool_truncated) = true;
            return false;
        }
        *spool_bytes = (*spool_bytes + bytes).min(self.spool_max_bytes);
        true
    }

    #[must_use]
    pub fn spool_truncated(&self) -> bool {
        *lock(&self.spool_truncated)
    }

    #[must_use]
    pub fn next_byte_offset(&self) -> u64 {
        *lock(&self.next_byte_offset)
    }
}

#[must_use]
pub fn utf8_consumable_prefix_len(bytes: &[u8]) -> usize {
    let mut offset = 0;
    while offset < bytes.len() {
        match std::str::from_utf8(&bytes[offset..]) {
            Ok(_) => return bytes.len(),
            Err(err) if err.error_len().is_none() => return offset + err.valid_up_to(),
            Err(err) => {
                offset += err.valid_up_to() + err.error_len().unwrap_or(1);
            }
        }
    }
    bytes.len()
}

fn max_output_bytes(max_tokens: Option<u64>) -> usize {
    max_tokens
        .and_then(|tokens| usize::try_from(tokens.saturating_mul(4)).ok())
        .filter(|value| *value > 0)
        .unwrap_or(80_000)
}

fn floor_char_boundary(text: &str, mut index: usize) -> usize {
    index = index.min(text.len());
    while index > 0 && !text.is_char_boundary(index) {
        index -= 1;
    }
    index
}

fn slice_from_byte(text: &str, start: usize) -> &str {
    if start >= text.len() {
        return "";
    }
    let start = floor_char_boundary(text, start);
    &text[start..]
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}
