use crate::model::LayerPath;

pub(crate) const GIT_METADATA_UNSUPPORTED_DROP_REASON: &str = "git_metadata_unsupported";
pub(crate) const GIT_INDEX_STAT_REFRESH_DROP_REASON: &str = "git_index_stat_refresh";
pub(crate) const GIT_INDEX_STAGED_STATE_REJECT_REASON: &str = "git_index_staged_state";
pub(crate) const GIT_LOCK_FILE_REJECT_REASON: &str = "git_lock_file";
pub(crate) const GIT_INCOMPLETE_OPERATION_REJECT_REASON: &str = "git_incomplete_operation";
pub(crate) const GIT_HOOK_WRITE_REJECT_REASON: &str = "git_hook_write";
pub(crate) const GIT_METADATA_DELETE_REJECT_REASON: &str = "git_metadata_delete";
pub(crate) const GIT_METADATA_OPAQUE_REPLACE_REJECT_REASON: &str = "git_metadata_opaque_replace";
pub(crate) const GIT_REF_WRITE_REJECT_REASON: &str = "git_ref_write";
pub(crate) const GIT_OBJECT_REWRITE_REJECT_REASON: &str = "git_object_rewrite";
pub(crate) const GIT_REFLOG_REWRITE_REJECT_REASON: &str = "git_reflog_rewrite";
pub(crate) const DAEMON_CONTROL_PATH_DROP_REASON: &str = "daemon_control_path";
pub(crate) const COMMAND_SCRATCH_PATH_DROP_REASON: &str = "command_scratch_path";
pub(crate) const OPAQUE_DIR_PROTECTED_DESCENDANT_DROP_REASON: &str =
    "opaque_dir_protected_descendant";
pub(crate) const OPAQUE_DIR_MIXED_ROUTES_DROP_REASON: &str = "opaque_dir_mixed_routes";
pub(crate) const OPAQUE_DIR_EXPANSION_LIMIT_DROP_REASON: &str = "opaque_dir_expansion_limit";

pub(super) const OPAQUE_DIR_EXPANSION_LIMIT: usize = 4096;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RouteDropReason {
    GitMetadataUnsupported,
    GitIndexStatRefresh,
    GitIndexStagedState,
    GitLockFile,
    GitIncompleteOperation,
    GitHookWrite,
    GitMetadataDelete,
    GitMetadataOpaqueReplace,
    GitRefWrite,
    GitObjectRewrite,
    GitReflogRewrite,
    DaemonControlPath,
    CommandScratchPath,
    OpaqueDirProtectedDescendant,
    OpaqueDirMixedRoutes,
    OpaqueDirExpansionLimit,
}

impl RouteDropReason {
    pub(crate) const fn as_str(self) -> &'static str {
        match self {
            Self::GitMetadataUnsupported => GIT_METADATA_UNSUPPORTED_DROP_REASON,
            Self::GitIndexStatRefresh => GIT_INDEX_STAT_REFRESH_DROP_REASON,
            Self::GitIndexStagedState => GIT_INDEX_STAGED_STATE_REJECT_REASON,
            Self::GitLockFile => GIT_LOCK_FILE_REJECT_REASON,
            Self::GitIncompleteOperation => GIT_INCOMPLETE_OPERATION_REJECT_REASON,
            Self::GitHookWrite => GIT_HOOK_WRITE_REJECT_REASON,
            Self::GitMetadataDelete => GIT_METADATA_DELETE_REJECT_REASON,
            Self::GitMetadataOpaqueReplace => GIT_METADATA_OPAQUE_REPLACE_REJECT_REASON,
            Self::GitRefWrite => GIT_REF_WRITE_REJECT_REASON,
            Self::GitObjectRewrite => GIT_OBJECT_REWRITE_REJECT_REASON,
            Self::GitReflogRewrite => GIT_REFLOG_REWRITE_REJECT_REASON,
            Self::DaemonControlPath => DAEMON_CONTROL_PATH_DROP_REASON,
            Self::CommandScratchPath => COMMAND_SCRATCH_PATH_DROP_REASON,
            Self::OpaqueDirProtectedDescendant => OPAQUE_DIR_PROTECTED_DESCENDANT_DROP_REASON,
            Self::OpaqueDirMixedRoutes => OPAQUE_DIR_MIXED_ROUTES_DROP_REASON,
            Self::OpaqueDirExpansionLimit => OPAQUE_DIR_EXPANSION_LIMIT_DROP_REASON,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Route {
    Gated,
    Direct,
    Drop,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct PublishDecision {
    pub(crate) path: LayerPath,
    pub(crate) route: Route,
    pub(crate) base_hash: Option<String>,
    pub(crate) drop_reason: Option<RouteDropReason>,
    pub(crate) reject_publish: bool,
    pub(crate) validation_base_hashes: Option<Vec<(LayerPath, Option<String>)>>,
}

pub(super) fn publish_decision(
    path: LayerPath,
    route: Route,
    base_hash: Option<String>,
    drop_reason: Option<RouteDropReason>,
) -> PublishDecision {
    PublishDecision {
        path,
        route,
        base_hash,
        drop_reason,
        reject_publish: false,
        validation_base_hashes: None,
    }
}

pub(super) fn rejected_drop_decision(
    path: LayerPath,
    drop_reason: RouteDropReason,
) -> PublishDecision {
    PublishDecision {
        path,
        route: Route::Drop,
        base_hash: None,
        drop_reason: Some(drop_reason),
        reject_publish: true,
        validation_base_hashes: None,
    }
}
