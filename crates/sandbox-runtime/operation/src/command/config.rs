use std::path::PathBuf;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandConfig {
    pub scratch_root: PathBuf,
}

impl Default for CommandConfig {
    fn default() -> Self {
        Self {
            scratch_root: PathBuf::from("/eos/scratch/commands"),
        }
    }
}
