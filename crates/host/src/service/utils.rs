use std::fs;
use std::path::Path;

use anyhow::{Context, Result};

pub(crate) fn random_hex(bytes: usize) -> Result<String> {
    use std::io::Read;

    let mut buf = vec![0_u8; bytes];
    fs::File::open("/dev/urandom")
        .context("open /dev/urandom")?
        .read_exact(&mut buf)
        .context("read /dev/urandom")?;
    Ok(buf.iter().map(|byte| format!("{byte:02x}")).collect())
}

pub(crate) fn path_str<'a>(path: &'a Path, field: &str) -> Result<&'a str> {
    path.to_str()
        .with_context(|| format!("{field} must be valid UTF-8: {}", path.display()))
}
