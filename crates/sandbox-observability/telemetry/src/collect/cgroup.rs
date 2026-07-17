//! Pure cgroup v2 readers with no runtime or daemon dependency. The daemon packs
//! accounting samples into `Sample.metrics` and exposes topology on demand.

use std::path::{Path, PathBuf};

use serde::Serialize;

/// A cgroup v2 accounting reading, or an unavailable marker carrying the path and
/// the first failure reason.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CgroupSample {
    pub cgroup_path: Option<String>,
    pub cgroup_available: bool,
    pub cgroup_error: Option<String>,
    pub cpu_usage_usec: Option<i64>,
    pub memory_current_bytes: Option<i64>,
    pub memory_max_bytes: Option<i64>,
    pub memory_max_unlimited: Option<bool>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct CgroupTopology {
    pub available: bool,
    pub root: Option<String>,
    pub self_cgroup: Option<String>,
    pub error: Option<String>,
    pub controllers: Vec<String>,
    pub groups: Vec<CgroupGroup>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CgroupGroup {
    pub path: String,
    pub role: CgroupRole,
    pub cpu_usage_usec: Option<i64>,
    pub memory_current_bytes: Option<i64>,
    pub memory_max_bytes: Option<i64>,
    pub memory_max_unlimited: Option<bool>,
    pub error: Option<String>,
    pub processes: Vec<CgroupProcess>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CgroupRole {
    Root,
    Daemon,
    Workspace,
    Other,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CgroupProcess {
    pub pid: u32,
    pub name: String,
    pub membership: Option<String>,
}

impl CgroupSample {
    #[must_use]
    pub fn unavailable(message: impl Into<String>) -> Self {
        Self {
            cgroup_available: false,
            cgroup_error: Some(message.into()),
            ..Self::default()
        }
    }

    /// Sample cgroup v2 accounting from `cgroup_dir`'s controller files.
    /// Best-effort: any missing/unreadable required file degrades to an
    /// unavailable sample carrying the path and the first failure reason.
    #[must_use]
    pub fn read(cgroup_dir: &Path) -> Self {
        let path_text = cgroup_dir.to_string_lossy().into_owned();
        let sample = || -> Result<Self, String> {
            let cpu_usage_usec = read_cpu_usage_usec(cgroup_dir)?;
            let memory_current_bytes = read_u64_file(&cgroup_dir.join("memory.current"))?;
            let (memory_max_bytes, memory_max_unlimited) = read_memory_max(cgroup_dir)?;
            Ok(Self {
                cgroup_path: Some(path_text.clone()),
                cgroup_available: true,
                cgroup_error: None,
                cpu_usage_usec: Some(cpu_usage_usec),
                memory_current_bytes: Some(memory_current_bytes),
                memory_max_bytes,
                memory_max_unlimited: Some(memory_max_unlimited),
            })
        };
        match sample() {
            Ok(sample) => sample,
            Err(error) => Self {
                cgroup_path: Some(path_text),
                ..Self::unavailable(error)
            },
        }
    }
}

impl CgroupTopology {
    #[must_use]
    pub fn unavailable(proc_root: &Path, message: impl Into<String>) -> Self {
        Self {
            self_cgroup: read_membership(&proc_root.join("self/cgroup")),
            error: Some(message.into()),
            ..Self::default()
        }
    }

    #[must_use]
    pub fn read(cgroup_root: &Path, proc_root: &Path) -> Self {
        let root = cgroup_root.to_string_lossy().into_owned();
        match read_topology(cgroup_root, proc_root) {
            Ok((controllers, groups)) => Self {
                available: true,
                root: Some(root),
                self_cgroup: read_membership(&proc_root.join("self/cgroup")),
                error: None,
                controllers,
                groups,
            },
            Err(error) => Self {
                root: Some(root),
                ..Self::unavailable(proc_root, error)
            },
        }
    }
}

fn read_topology(
    cgroup_root: &Path,
    proc_root: &Path,
) -> Result<(Vec<String>, Vec<CgroupGroup>), String> {
    let mut controllers = read_file(&cgroup_root.join("cgroup.controllers"))?
        .split_whitespace()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    controllers.sort();

    let mut children = std::fs::read_dir(cgroup_root)
        .map_err(|error| format!("{}: {error}", cgroup_root.display()))?
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.is_dir())
        .collect::<Vec<PathBuf>>();
    children.sort();

    let mut groups = vec![read_group(cgroup_root, "/", CgroupRole::Root, proc_root)];
    groups.extend(children.into_iter().map(|path| {
        let name = path
            .file_name()
            .map_or_else(String::new, |name| name.to_string_lossy().into_owned());
        let role = match name.as_str() {
            "_daemon" => CgroupRole::Daemon,
            name if name.starts_with("workspace-") => CgroupRole::Workspace,
            _ => CgroupRole::Other,
        };
        read_group(&path, &format!("/{name}"), role, proc_root)
    }));
    Ok((controllers, groups))
}

fn read_group(cgroup_dir: &Path, path: &str, role: CgroupRole, proc_root: &Path) -> CgroupGroup {
    let sample = CgroupSample::read(cgroup_dir);
    CgroupGroup {
        path: path.to_owned(),
        role,
        cpu_usage_usec: sample.cpu_usage_usec,
        memory_current_bytes: sample.memory_current_bytes,
        memory_max_bytes: sample.memory_max_bytes,
        memory_max_unlimited: sample.memory_max_unlimited,
        error: sample.cgroup_error,
        processes: read_processes(cgroup_dir, proc_root),
    }
}

fn read_processes(cgroup_dir: &Path, proc_root: &Path) -> Vec<CgroupProcess> {
    let mut pids = read_file(&cgroup_dir.join("cgroup.procs"))
        .unwrap_or_default()
        .lines()
        .filter_map(|line| line.trim().parse::<u32>().ok())
        .collect::<Vec<_>>();
    pids.sort_unstable();
    pids.dedup();
    pids.into_iter()
        .map(|pid| {
            let pid_root = proc_root.join(pid.to_string());
            CgroupProcess {
                pid,
                name: read_file(&pid_root.join("comm"))
                    .map_or_else(|_| "unknown".to_owned(), |name| name.trim().to_owned()),
                membership: read_membership(&pid_root.join("cgroup")),
            }
        })
        .collect()
}

fn read_membership(path: &Path) -> Option<String> {
    read_file(path).ok()?.lines().find_map(|line| {
        line.strip_prefix("0::")
            .map(|membership| format!("0::{}", membership.trim()))
    })
}

fn read_cpu_usage_usec(cgroup_dir: &Path) -> Result<i64, String> {
    let path = cgroup_dir.join("cpu.stat");
    let contents = read_file(&path)?;
    contents
        .lines()
        .find_map(|line| line.strip_prefix("usage_usec "))
        .ok_or_else(|| format!("usage_usec missing in {}", path.display()))
        .and_then(|value| parse_i64(value.trim(), &path))
}

fn read_memory_max(cgroup_dir: &Path) -> Result<(Option<i64>, bool), String> {
    let path = cgroup_dir.join("memory.max");
    let contents = read_file(&path)?;
    let trimmed = contents.trim();
    if trimmed == "max" {
        Ok((None, true))
    } else {
        Ok((Some(parse_i64(trimmed, &path)?), false))
    }
}

fn read_u64_file(path: &Path) -> Result<i64, String> {
    parse_i64(read_file(path)?.trim(), path)
}

fn read_file(path: &Path) -> Result<String, String> {
    std::fs::read_to_string(path).map_err(|error| format!("{}: {error}", path.display()))
}

fn parse_i64(value: &str, path: &Path) -> Result<i64, String> {
    value
        .parse::<i64>()
        .map_err(|error| format!("{}: {error}", path.display()))
}
