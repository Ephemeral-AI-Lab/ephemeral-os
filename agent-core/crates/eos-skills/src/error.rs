//! [`SkillLoadError`] — this crate's single library error enum (`err-thiserror-lib`).

use std::path::PathBuf;

/// Failures raised while loading skills from the configured skill root.
///
/// There is deliberately **no** malformed-frontmatter variant: matching Rust's
/// `parse_markdown_frontmatter`, broken YAML is swallowed and the loader falls
/// back to the heading/first-paragraph metadata scan rather than failing the load.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum SkillLoadError {
    /// The skill root exists but is not a directory — a config error, so the
    /// loader fails fast rather than treating it as "no skills".
    #[error("skill root is not a directory: {0}")]
    RootNotDir(PathBuf),
    /// Listing a directory (the root or a `references/` subdirectory) failed.
    #[error("failed to read skill directory {path}")]
    ReadDir {
        /// The directory whose listing failed.
        path: PathBuf,
        /// The underlying I/O error.
        #[source]
        cause: std::io::Error,
    },
    /// Reading a `SKILL.md` or `references/*.md` file failed.
    #[error("failed to read skill file {path}")]
    ReadFile {
        /// The file whose read failed.
        path: PathBuf,
        /// The underlying I/O error.
        #[source]
        cause: std::io::Error,
    },
    /// A parsed skill or reference name was empty or carried a path component
    /// (defense-in-depth; see [`crate::SkillName`]).
    #[error("invalid skill name {0:?}")]
    InvalidName(String),
}
