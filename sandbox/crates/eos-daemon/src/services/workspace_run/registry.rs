// The caller-keyed registry is the Linux PTY/overlay orchestration. On non-Linux
// the daemon serves command-session ops as stubs, so the registry is dead there —
// it stays compiled for the scaffold unit tests and a uniform module tree.
#![cfg_attr(not(target_os = "linux"), allow(dead_code))]

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};

use eos_command_session::{
    CollectCompleted, CollectCompletedResponse, CommandResponse, CommandSession,
    CommandSessionCompletion,
};
use eos_workspace_api::CommandWorkspacePolicy;

/// The workspace policy owned by a run — overlay/namespace state (lease + dirs)
/// plus the finalize/discard logic. The run, not the session, owns this so the
/// publish-vs-discard decision lives at the run level.
pub(crate) type PolicyArc = Arc<dyn CommandWorkspacePolicy + Send + Sync>;

/// One command session paired with the workspace policy that owns its overlay
/// (ephemeral) or namespace (isolated) state. The session is the PTY substrate;
/// the policy decides publish (complete) vs discard (cancel).
pub(crate) struct RunSession {
    pub(crate) session: CommandSession,
    pub(crate) policy: PolicyArc,
}

impl RunSession {
    #[must_use]
    pub(crate) fn new(session: CommandSession, policy: PolicyArc) -> Self {
        Self { session, policy }
    }
}

/// Which workspace a starting command session belongs to. The daemon picks the
/// kind from the caller's current mode; the registry uses it to place the session
/// into a fresh ephemeral workspace run or the caller's isolated run.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkspaceRunKind {
    Ephemeral,
    Isolated,
}

/// One ephemeral workspace run: **exactly one** command session. Each ephemeral
/// `exec_command` gets its own workspace (its snapshot lease + run dirs live in
/// the run's policy), so the workspace and its session are 1:1 and co-terminal.
struct EphemeralWorkspaceRun {
    run: Arc<RunSession>,
}

/// The isolated workspace run: **many** command sessions sharing the caller's one
/// isolated workspace (namespace + snapshot are owned by the isolated-session
/// subsystem; this run just tracks the command sessions running inside it).
struct IsolatedWorkspaceRun {
    sessions: HashMap<String, Arc<RunSession>>,
}

/// A caller's workspace runs. The XOR — many ephemeral workspaces (each one
/// session) **or** the one isolated workspace (many sessions) — is enforced by
/// the isolated enter/exit gate and encoded structurally here: an ephemeral
/// caller maps to a set of single-session runs, an isolated caller to one
/// many-session run.
enum CallerRun {
    Ephemeral(HashMap<String, EphemeralWorkspaceRun>),
    Isolated(IsolatedWorkspaceRun),
}

impl CallerRun {
    fn sessions(&self) -> Vec<Arc<RunSession>> {
        match self {
            Self::Ephemeral(runs) => runs.values().map(|run| Arc::clone(&run.run)).collect(),
            Self::Isolated(run) => run.sessions.values().cloned().collect(),
        }
    }

    fn get(&self, session_id: &str) -> Option<Arc<RunSession>> {
        match self {
            Self::Ephemeral(runs) => runs.get(session_id).map(|run| Arc::clone(&run.run)),
            Self::Isolated(run) => run.sessions.get(session_id).cloned(),
        }
    }

    fn count(&self) -> usize {
        match self {
            Self::Ephemeral(runs) => runs.len(),
            Self::Isolated(run) => run.sessions.len(),
        }
    }

    /// Remove `session_id`, returning the removed run and whether this caller
    /// run is now empty (so the registry can drop the caller entry).
    fn take(&mut self, session_id: &str) -> (Option<Arc<RunSession>>, bool) {
        match self {
            Self::Ephemeral(runs) => {
                let removed = runs.remove(session_id).map(|run| run.run);
                (removed, runs.is_empty())
            }
            Self::Isolated(run) => {
                let removed = run.sessions.remove(session_id);
                (removed, run.sessions.is_empty())
            }
        }
    }
}

/// Single caller-keyed command-session authority. Each caller maps to its
/// `CallerRun` (many ephemeral workspace runs or the one isolated run).
/// Session-targeted ops resolve by scanning runs for the session id (caller count
/// is small).
#[derive(Default)]
pub(crate) struct WorkspaceRunRegistry {
    runs: Mutex<HashMap<String, CallerRun>>,
    completed: Mutex<HashMap<String, CommandSessionCompletion>>,
    counter: AtomicU64,
}

impl WorkspaceRunRegistry {
    #[must_use]
    pub(crate) fn new() -> Self {
        Self {
            runs: Mutex::new(HashMap::new()),
            completed: Mutex::new(HashMap::new()),
            counter: AtomicU64::new(1),
        }
    }

    #[must_use]
    pub(crate) fn next_id(&self) -> String {
        format!("cmd_{}", self.counter.fetch_add(1, Ordering::Relaxed))
    }

    /// Place a started run into its caller's runs: a fresh ephemeral workspace
    /// run, or the caller's (created-on-first-session) isolated run.
    pub(crate) fn insert(&self, run: Arc<RunSession>, kind: WorkspaceRunKind) {
        let caller_id = run.session.caller_id().to_owned();
        let session_id = run.session.id().to_owned();
        let mut runs = lock(&self.runs);
        match kind {
            WorkspaceRunKind::Ephemeral => {
                let caller = runs
                    .entry(caller_id)
                    .or_insert_with(|| CallerRun::Ephemeral(HashMap::new()));
                if let CallerRun::Ephemeral(ephemeral) = caller {
                    ephemeral.insert(session_id, EphemeralWorkspaceRun { run });
                }
            }
            WorkspaceRunKind::Isolated => {
                let caller = runs.entry(caller_id).or_insert_with(|| {
                    CallerRun::Isolated(IsolatedWorkspaceRun {
                        sessions: HashMap::new(),
                    })
                });
                if let CallerRun::Isolated(isolated) = caller {
                    isolated.sessions.insert(session_id, run);
                }
            }
        }
    }

    #[must_use]
    pub(crate) fn get(&self, id: &str) -> Option<Arc<RunSession>> {
        lock(&self.runs).values().find_map(|run| run.get(id))
    }

    pub(crate) fn remove(&self, id: &str) -> Option<Arc<RunSession>> {
        let mut runs = lock(&self.runs);
        let caller = runs
            .iter()
            .find(|(_, run)| run.get(id).is_some())
            .map(|(caller, _)| caller.clone())?;
        let (run, now_empty) = runs
            .get_mut(&caller)
            .map(|run| run.take(id))
            .unwrap_or((None, false));
        if now_empty {
            runs.remove(&caller);
        }
        run
    }

    #[must_use]
    pub(crate) fn count_by_caller(&self, caller_id: Option<&str>) -> usize {
        let runs = lock(&self.runs);
        match caller_id {
            Some(caller) => runs.get(caller).map_or(0, CallerRun::count),
            None => runs.values().map(CallerRun::count).sum(),
        }
    }

    #[must_use]
    pub(crate) fn live(&self) -> Vec<Arc<RunSession>> {
        lock(&self.runs)
            .values()
            .flat_map(CallerRun::sessions)
            .collect()
    }

    /// All live runs owned by `caller_id` (drives per-caller cleanup).
    #[cfg(target_os = "linux")]
    #[must_use]
    pub(crate) fn caller_sessions(&self, caller_id: &str) -> Vec<Arc<RunSession>> {
        lock(&self.runs)
            .get(caller_id)
            .map(CallerRun::sessions)
            .unwrap_or_default()
    }

    pub(crate) fn push_completed(&self, completion: CommandSessionCompletion) {
        lock(&self.completed).insert(completion.command_session_id.clone(), completion);
    }

    pub(crate) fn take_completed_result(&self, id: &str) -> Option<CommandResponse> {
        lock(&self.completed).remove(id).map(|entry| entry.result)
    }

    #[must_use]
    pub(crate) fn completed_result(&self, id: &str) -> Option<CommandResponse> {
        lock(&self.completed)
            .get(id)
            .map(|entry| entry.result.clone())
    }

    #[must_use]
    pub(crate) fn collect_completed(&self, request: &CollectCompleted) -> CollectCompletedResponse {
        let wanted: Option<HashSet<String>> = request
            .command_session_ids
            .as_ref()
            .map(|ids| ids.iter().cloned().collect());
        let caller_id = request.caller_id.as_deref();
        let mut completed = lock(&self.completed);
        let matched: Vec<String> = completed
            .iter()
            .filter(|(id, completion)| {
                let id_matches = wanted.as_ref().is_none_or(|ids| ids.contains(*id));
                let caller_matches =
                    caller_id.is_none_or(|caller_id| completion.caller_id == caller_id);
                id_matches && caller_matches
            })
            .map(|(id, _)| id.clone())
            .collect();
        let completions = matched
            .iter()
            .filter_map(|id| completed.remove(id))
            .map(|mut completion| {
                completion.result = completion.notification_result.clone();
                completion
            })
            .collect();
        CollectCompletedResponse {
            success: true,
            completions,
        }
    }
}

pub(crate) fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}
