#[must_use]
pub fn tail_lines(text: &str, last_n_lines: usize) -> String {
    if text.is_empty() || last_n_lines == 0 {
        return String::new();
    }
    let mut line_starts = vec![0_usize];
    for (idx, byte) in text.bytes().enumerate() {
        if byte == b'\n' && idx + 1 < text.len() {
            line_starts.push(idx + 1);
        }
    }
    let start_idx = line_starts
        .len()
        .saturating_sub(last_n_lines)
        .min(line_starts.len().saturating_sub(1));
    text[line_starts[start_idx]..].to_owned()
}

#[cfg(test)]
#[path = "../tests/unit/tail.rs"]
mod tests;
