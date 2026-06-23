#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct CgroupSample {
    pub(crate) cgroup_path: Option<String>,
    pub(crate) cgroup_available: bool,
    pub(crate) cgroup_error: Option<String>,
    pub(crate) cpu_usage_usec: Option<i64>,
    pub(crate) memory_current_bytes: Option<i64>,
    pub(crate) memory_max_bytes: Option<i64>,
    pub(crate) memory_max_unlimited: Option<bool>,
}

impl CgroupSample {
    pub(crate) fn unavailable(message: impl Into<String>) -> Self {
        Self {
            cgroup_available: false,
            cgroup_error: Some(message.into()),
            ..Self::default()
        }
    }
}
