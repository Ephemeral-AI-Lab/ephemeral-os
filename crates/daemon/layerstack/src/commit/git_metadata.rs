use crate::model::LayerPath;

pub(crate) fn relative_parts(path: &LayerPath) -> Option<Vec<&str>> {
    let mut parts = Vec::new();
    let mut found_git = false;
    for part in path.as_str().split('/') {
        if found_git {
            parts.push(part);
        } else if part == ".git" {
            found_git = true;
        }
    }
    found_git.then_some(parts)
}

pub(crate) fn is_canonical_loose_object_path(parts: &[&str]) -> bool {
    matches!(parts, ["objects", dir, file] if is_lower_hex_len(dir, 2) && is_lower_hex_len(file, 38))
}

fn is_lower_hex_len(value: &str, len: usize) -> bool {
    value.len() == len
        && value
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
}
