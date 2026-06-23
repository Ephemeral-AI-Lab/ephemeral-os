use std::fs;
use std::path::Path;

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

    pub(crate) fn from_optional_path(path: Option<&Path>) -> Self {
        let Some(path) = path else {
            return Self::unavailable("cgroup path unavailable");
        };
        sample_cgroup_path(path)
    }
}

fn sample_cgroup_path(path: &Path) -> CgroupSample {
    let path_string = path.to_string_lossy().into_owned();
    if !path.exists() {
        return CgroupSample {
            cgroup_path: Some(path_string),
            ..CgroupSample::unavailable("cgroup path missing")
        };
    }

    let mut sample = CgroupSample {
        cgroup_path: Some(path_string),
        ..CgroupSample::default()
    };
    let mut recognized = false;
    let mut errors = Vec::<String>::new();

    match fs::read_to_string(path.join("cpu.stat")) {
        Ok(contents) => {
            recognized = true;
            sample.cpu_usage_usec = parse_cpu_usage(&contents).or_else(|| {
                errors.push("cpu.stat usage_usec parse failed".to_owned());
                None
            });
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => errors.push(format!("cpu.stat read failed: {error}")),
    }

    match fs::read_to_string(path.join("memory.current")) {
        Ok(contents) => {
            recognized = true;
            sample.memory_current_bytes = parse_i64(contents.trim()).or_else(|| {
                errors.push("memory.current parse failed".to_owned());
                None
            });
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => errors.push(format!("memory.current read failed: {error}")),
    }

    match fs::read_to_string(path.join("memory.max")) {
        Ok(contents) => {
            recognized = true;
            let value = contents.trim();
            if value == "max" {
                sample.memory_max_unlimited = Some(true);
            } else {
                sample.memory_max_unlimited = Some(false);
                sample.memory_max_bytes = parse_i64(value).or_else(|| {
                    errors.push("memory.max parse failed".to_owned());
                    None
                });
            }
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => errors.push(format!("memory.max read failed: {error}")),
    }

    sample.cgroup_available = recognized;
    if !recognized && errors.is_empty() {
        sample.cgroup_error = Some("cgroup files unavailable".to_owned());
    } else if !errors.is_empty() {
        sample.cgroup_error = Some(errors.join("; "));
    }
    sample
}

fn parse_cpu_usage(contents: &str) -> Option<i64> {
    contents.lines().find_map(|line| {
        let mut parts = line.split_whitespace();
        match (parts.next(), parts.next()) {
            (Some("usage_usec"), Some(value)) => parse_i64(value),
            _ => None,
        }
    })
}

fn parse_i64(value: &str) -> Option<i64> {
    value.parse::<i64>().ok()
}
