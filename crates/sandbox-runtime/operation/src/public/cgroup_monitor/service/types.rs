use crate::command::CommandSessionId;
use crate::workspace_crate::{
    CgroupCleanupState, CgroupMonitorSample, CgroupMonitorState, CgroupMonitorTarget,
    WorkspaceSessionId,
};

#[derive(Debug, Clone, PartialEq)]
pub struct InspectCgroupMonitorOutput {
    pub workspace_session_id: WorkspaceSessionId,
    pub command_session_id: Option<CommandSessionId>,
    pub target: CgroupMonitorTarget,
    pub monitor: CgroupMonitorState,
    pub latest: Option<CgroupMonitorSample>,
    pub cleanup: CgroupCleanupState,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ReadCgroupMonitorSamplesOutput {
    pub workspace_session_id: WorkspaceSessionId,
    pub command_session_id: Option<CommandSessionId>,
    pub target: CgroupMonitorTarget,
    pub samples: Vec<CgroupMonitorSample>,
}
