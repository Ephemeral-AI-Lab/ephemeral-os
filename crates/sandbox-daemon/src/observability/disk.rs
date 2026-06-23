use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct DiskSample {
    pub(crate) upperdir_bytes: Option<i64>,
    pub(crate) file_count: Option<i64>,
    pub(crate) dir_count: Option<i64>,
    pub(crate) symlink_count: Option<i64>,
    pub(crate) truncated: Option<bool>,
    pub(crate) read_error_count: Option<i64>,
    pub(crate) first_error_path: Option<String>,
}

impl DiskSample {
    pub(crate) fn empty() -> Self {
        Self::default()
    }
}

pub(crate) fn sample_upperdir(path: &Path) -> DiskSample {
    let mut sample = DiskSample {
        upperdir_bytes: Some(0),
        file_count: Some(0),
        dir_count: Some(0),
        symlink_count: Some(0),
        truncated: Some(false),
        read_error_count: Some(0),
        first_error_path: None,
    };
    let mut stack = vec![path.to_path_buf()];

    while let Some(current) = stack.pop() {
        let metadata = match fs::symlink_metadata(&current) {
            Ok(metadata) => metadata,
            Err(error) => {
                record_error(&mut sample, &current, error);
                continue;
            }
        };
        let file_type = metadata.file_type();
        if file_type.is_file() {
            add(
                &mut sample.upperdir_bytes,
                i64::try_from(metadata.len()).unwrap_or(i64::MAX),
            );
            add(&mut sample.file_count, 1);
        } else if file_type.is_dir() {
            add(&mut sample.dir_count, 1);
            let entries = match fs::read_dir(&current) {
                Ok(entries) => entries,
                Err(error) => {
                    record_error(&mut sample, &current, error);
                    continue;
                }
            };
            for entry in entries {
                match entry {
                    Ok(entry) => stack.push(entry.path()),
                    Err(error) => record_error(&mut sample, &current, error),
                }
            }
        } else if file_type.is_symlink() {
            add(&mut sample.symlink_count, 1);
        }
    }

    sample
}

fn add(value: &mut Option<i64>, amount: i64) {
    *value = Some(value.unwrap_or_default().saturating_add(amount));
}

fn record_error(sample: &mut DiskSample, path: &Path, error: std::io::Error) {
    add(&mut sample.read_error_count, 1);
    if sample.first_error_path.is_none() {
        sample.first_error_path = Some(first_error(path, error));
    }
}

fn first_error(path: &Path, error: std::io::Error) -> String {
    let path = path_string(path);
    format!("{path}: {error}")
}

fn path_string(path: &Path) -> String {
    PathBuf::from(path).to_string_lossy().into_owned()
}
