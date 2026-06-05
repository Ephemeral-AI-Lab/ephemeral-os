use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

use eos_layerstack::{LayerStack, MergedView, AUTO_SQUASH_MAX_DEPTH};
use eos_occ::{
    ChangesetResult, CommitTransactionPort, FileResult, OccStatus, PreparedChangeset,
    PublishConflict, Route,
};
use eos_protocol::{LayerChange, LayerPath, Manifest};

use crate::response_timings::{i64_to_f64_saturating, usize_to_f64_saturating};

use super::hash_current;

static AUTO_SQUASH_MAX_DEPTH_CONFIG: AtomicUsize = AtomicUsize::new(AUTO_SQUASH_MAX_DEPTH);

pub(super) fn configure_auto_squash_max_depth(max_depth: usize) {
    AUTO_SQUASH_MAX_DEPTH_CONFIG.store(max_depth.max(1), Ordering::Relaxed);
}

fn auto_squash_max_depth() -> usize {
    AUTO_SQUASH_MAX_DEPTH_CONFIG.load(Ordering::Relaxed)
}

#[derive(Clone)]
pub(crate) struct LayerStackCommitTransaction {
    pub(crate) root: PathBuf,
}

impl CommitTransactionPort for LayerStackCommitTransaction {
    fn revalidate_and_publish(
        &self,
        combined: &PreparedChangeset,
    ) -> std::result::Result<ChangesetResult, PublishConflict> {
        let total_start = Instant::now();
        let mut stack = match LayerStack::open(self.root.clone()) {
            Ok(stack) => stack,
            Err(err) => return Ok(failed_revalidate_result(combined, &err, total_start)),
        };
        let validate_start = Instant::now();
        let active = match stack.read_active_manifest() {
            Ok(manifest) => manifest,
            Err(err) => return Ok(failed_revalidate_result(combined, &err, total_start)),
        };
        let view = MergedView::new(self.root.clone());
        let validations = validate_prepared(&self.root, &view, &active, combined);
        let validate_s = validate_start.elapsed().as_secs_f64();
        if combined.atomic
            && validations
                .iter()
                .any(|file| is_validation_failure(file.status))
        {
            return Ok(atomic_validation_drop_result(
                combined,
                validations,
                validate_s,
                total_start,
            ));
        }
        let publishable_changes = publishable_changes(combined, &validations);
        if publishable_changes.is_empty() {
            return Ok(no_publish_result(
                combined,
                validations,
                validate_s,
                total_start,
            ));
        }
        let publish_start = Instant::now();
        match stack.publish_layer(&publishable_changes) {
            Ok(manifest) => {
                let publish_s = publish_start.elapsed().as_secs_f64();
                let auto_squash_timings = run_auto_squash(&mut stack);
                Ok(committed_changeset_result(
                    combined,
                    validations,
                    manifest_version_u64_optional(manifest.version),
                    PublishedCommitTimings {
                        validate_s,
                        publish_s,
                        auto_squash_timings,
                        total_start,
                    },
                ))
            }
            Err(eos_layerstack::LayerStackError::ManifestConflict { found, .. }) => {
                Err(PublishConflict {
                    observed_version: manifest_version_u64_optional(found),
                })
            }
            Err(err) => {
                let publish_s = publish_start.elapsed().as_secs_f64();
                let timings = commit_timings(
                    combined,
                    validate_s,
                    publish_s,
                    total_start.elapsed().as_secs_f64(),
                );
                Ok(failed_changeset_with_timings(
                    combined,
                    &err.to_string(),
                    timings,
                ))
            }
        }
    }
}

fn failed_revalidate_result(
    combined: &PreparedChangeset,
    err: &eos_layerstack::LayerStackError,
    total_start: Instant,
) -> ChangesetResult {
    let timings = commit_timings(combined, 0.0, 0.0, total_start.elapsed().as_secs_f64());
    failed_changeset_with_timings(combined, &err.to_string(), timings)
}

fn atomic_validation_drop_result(
    combined: &PreparedChangeset,
    validations: Vec<FileResult>,
    validate_s: f64,
    total_start: Instant,
) -> ChangesetResult {
    ChangesetResult {
        files: validations
            .into_iter()
            .map(|file| {
                if file.status.is_published() {
                    FileResult {
                        status: OccStatus::Dropped,
                        message: "not published because atomic changeset validation failed"
                            .to_owned(),
                        ..file
                    }
                } else {
                    file
                }
            })
            .collect(),
        published_manifest_version: None,
        timings: commit_timings(
            combined,
            validate_s,
            0.0,
            total_start.elapsed().as_secs_f64(),
        ),
    }
}

fn publishable_changes(
    combined: &PreparedChangeset,
    validations: &[FileResult],
) -> Vec<LayerChange> {
    let publishable_paths = validations
        .iter()
        .filter(|file| file.status.is_published())
        .map(|file| file.path.as_str())
        .collect::<HashSet<_>>();
    combined
        .changes
        .iter()
        .filter(|change| publishable_paths.contains(change.path().as_str()))
        .cloned()
        .collect()
}

fn no_publish_result(
    combined: &PreparedChangeset,
    validations: Vec<FileResult>,
    validate_s: f64,
    total_start: Instant,
) -> ChangesetResult {
    ChangesetResult {
        files: validations,
        published_manifest_version: None,
        timings: commit_timings(
            combined,
            validate_s,
            0.0,
            total_start.elapsed().as_secs_f64(),
        ),
    }
}

fn run_auto_squash(stack: &mut LayerStack) -> BTreeMap<String, f64> {
    let mut timings = BTreeMap::new();
    let Some(active) = stack.read_active_manifest().ok() else {
        return timings;
    };
    let max_depth = auto_squash_max_depth();
    if active.depth() <= max_depth
        || !stack
            .can_squash(max_depth)
            .is_ok_and(|can_squash| can_squash)
    {
        return timings;
    }

    let squash_start = Instant::now();
    let squashed = stack.squash(max_depth).ok().flatten();
    let squash_elapsed_s = squash_start.elapsed().as_secs_f64();
    timings.insert(
        "layer_stack.auto_squash.total_s".to_owned(),
        squash_elapsed_s,
    );
    timings.insert(
        "layer_stack.auto_squash.max_depth".to_owned(),
        usize_to_f64_saturating(max_depth),
    );
    timings.insert(
        "layer_stack.auto_squash.depth_before".to_owned(),
        usize_to_f64_saturating(active.depth()),
    );
    match squashed {
        Some(manifest) => {
            timings.insert(
                "layer_stack.auto_squash.depth_after".to_owned(),
                usize_to_f64_saturating(manifest.depth()),
            );
            timings.insert(
                "layer_stack.auto_squash.manifest_version".to_owned(),
                i64_to_f64_saturating(manifest.version),
            );
        }
        None => {
            timings.insert("layer_stack.auto_squash.raced".to_owned(), 1.0);
        }
    }
    timings
}

fn committed_changeset_result(
    combined: &PreparedChangeset,
    validations: Vec<FileResult>,
    published_manifest_version: Option<u64>,
    phases: PublishedCommitTimings,
) -> ChangesetResult {
    let mut timings = commit_timings(
        combined,
        phases.validate_s,
        phases.publish_s,
        phases.total_start.elapsed().as_secs_f64(),
    );
    timings.extend(phases.auto_squash_timings);
    ChangesetResult {
        files: validations
            .into_iter()
            .map(|file| {
                if file.status.is_published() {
                    FileResult {
                        status: OccStatus::Committed,
                        ..file
                    }
                } else {
                    file
                }
            })
            .collect(),
        published_manifest_version,
        timings,
    }
}

struct PublishedCommitTimings {
    validate_s: f64,
    publish_s: f64,
    auto_squash_timings: BTreeMap<String, f64>,
    total_start: Instant,
}

fn validate_prepared(
    root: &Path,
    view: &MergedView,
    manifest: &Manifest,
    prepared: &PreparedChangeset,
) -> Vec<FileResult> {
    let mut parent_absent_cache = HashMap::new();
    prepared
        .path_groups
        .iter()
        .map(|group| match group.route {
            Route::Drop => FileResult {
                path: group.path.clone(),
                status: OccStatus::Dropped,
                message: group
                    .message
                    .clone()
                    .unwrap_or_else(|| "change dropped".to_owned()),
            },
            Route::Reject => FileResult {
                path: group.path.clone(),
                status: OccStatus::Rejected,
                message: group
                    .message
                    .clone()
                    .unwrap_or_else(|| "change rejected".to_owned()),
            },
            Route::Direct => validate_direct_group(&group.path),
            Route::Gated => validate_gated_group(
                root,
                view,
                manifest,
                prepared,
                &group.path,
                group.base_hash.as_deref(),
                &mut parent_absent_cache,
            ),
            _ => FileResult {
                path: group.path.clone(),
                status: OccStatus::Rejected,
                message: "unsupported route".to_owned(),
            },
        })
        .collect()
}

fn validate_direct_group(path: &LayerPath) -> FileResult {
    FileResult {
        path: path.clone(),
        status: OccStatus::Accepted,
        message: String::new(),
    }
}

fn validate_gated_group(
    root: &Path,
    view: &MergedView,
    manifest: &Manifest,
    prepared: &PreparedChangeset,
    path: &LayerPath,
    base_hash: Option<&str>,
    parent_absent_cache: &mut HashMap<String, bool>,
) -> FileResult {
    let path_str = path.as_str();
    if prepared.changes.iter().any(|change| {
        change.path().as_str() == path_str && matches!(change, LayerChange::Symlink { .. })
    }) {
        return FileResult {
            path: path.clone(),
            status: OccStatus::Rejected,
            message: "unsupported gated change kind: SymlinkChange".to_owned(),
        };
    }
    if base_hash.is_none() {
        if let Some(parent) = parent_dir(path_str) {
            let parent_absent = *parent_absent_cache
                .entry(parent.to_owned())
                .or_insert_with(|| parent_absent_from_manifest(root, manifest, parent));
            if parent_absent {
                return FileResult {
                    path: path.clone(),
                    status: OccStatus::Accepted,
                    message: String::new(),
                };
            }
        }
    }
    match view.read_bytes(path_str, manifest) {
        Ok((bytes, exists)) if hash_current(bytes.as_deref(), exists).as_deref() == base_hash => {
            FileResult {
                path: path.clone(),
                status: OccStatus::Accepted,
                message: String::new(),
            }
        }
        Ok(_) => FileResult {
            path: path.clone(),
            status: OccStatus::AbortedVersion,
            message: "content changed".to_owned(),
        },
        Err(err) => FileResult {
            path: path.clone(),
            status: OccStatus::Failed,
            message: err.to_string(),
        },
    }
}

fn parent_dir(path: &str) -> Option<&str> {
    path.rsplit_once('/')
        .map(|(parent, _)| parent)
        .filter(|parent| !parent.is_empty())
}

fn parent_absent_from_manifest(root: &Path, manifest: &Manifest, parent: &str) -> bool {
    manifest.layers.iter().all(|layer| {
        let path = PathBuf::from(&layer.path);
        let layer_dir = if path.is_absolute() {
            path
        } else {
            root.join(path)
        };
        matches!(
            std::fs::symlink_metadata(layer_dir.join(parent)),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound
        )
    })
}

const fn is_validation_failure(status: OccStatus) -> bool {
    matches!(
        status,
        OccStatus::AbortedOverlap
            | OccStatus::AbortedVersion
            | OccStatus::Failed
            | OccStatus::Rejected
    )
}

fn failed_changeset_with_timings(
    prepared: &PreparedChangeset,
    message: &str,
    timings: BTreeMap<String, f64>,
) -> ChangesetResult {
    ChangesetResult {
        files: prepared
            .path_groups
            .iter()
            .map(|group| FileResult {
                path: group.path.clone(),
                status: OccStatus::Failed,
                message: message.to_owned(),
            })
            .collect(),
        published_manifest_version: None,
        timings,
    }
}

fn commit_timings(
    prepared: &PreparedChangeset,
    validate_s: f64,
    publish_s: f64,
    total_s: f64,
) -> BTreeMap<String, f64> {
    let mut timings = BTreeMap::new();
    timings.insert("occ.apply.total_s".to_owned(), total_s);
    timings.insert("occ.commit.total_s".to_owned(), total_s);
    timings.insert("occ.commit.validate_groups_s".to_owned(), validate_s);
    timings.insert("occ.commit.publish_layer_s".to_owned(), publish_s);
    timings.insert(
        "occ.commit.stager_write_count".to_owned(),
        usize_to_f64_saturating(prepared.changes.len()),
    );
    timings.insert("occ.commit.stager_write_total_s".to_owned(), publish_s);
    timings.insert(
        "occ.commit.gated_path_count".to_owned(),
        usize_to_f64_saturating(
            prepared
                .path_groups
                .iter()
                .filter(|group| group.route == Route::Gated)
                .count(),
        ),
    );
    timings.insert(
        "occ.commit.direct_path_count".to_owned(),
        usize_to_f64_saturating(
            prepared
                .path_groups
                .iter()
                .filter(|group| group.route == Route::Direct)
                .count(),
        ),
    );
    for key in [
        "occ.commit.gated_read_current_total_s",
        "occ.commit.gated_apply_changes_total_s",
        "occ.commit.gated_stage_delta_total_s",
        "occ.commit.direct_read_current_total_s",
        "occ.commit.direct_apply_changes_total_s",
        "occ.commit.direct_stage_delta_total_s",
    ] {
        timings.insert(key.to_owned(), 0.0);
    }
    timings
}

fn manifest_version_u64_optional(version: i64) -> Option<u64> {
    u64::try_from(version).ok()
}
