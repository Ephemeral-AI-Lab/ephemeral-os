use std::path::PathBuf;

use sandbox_runtime_workspace::CgroupMonitorConfig;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommandConfig {
    pub scratch_root: PathBuf,
    pub cgroup_monitor: CgroupMonitorConfig,
}

impl Default for CommandConfig {
    fn default() -> Self {
        Self {
            scratch_root: PathBuf::from("/eos/scratch/commands"),
            cgroup_monitor: CgroupMonitorConfig::default(),
        }
    }
}
