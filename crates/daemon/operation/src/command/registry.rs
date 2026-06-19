// Non-Linux keeps this compiled for scaffold unit tests and a uniform module tree.
#![cfg_attr(not(target_os = "linux"), allow(dead_code))]

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};

use std::path::PathBuf;

use command::process::CommandProcess;
use command::{CollectCompleted, StartCommand};
use layerstack::service::{LeaseReleaseHandle, Snapshot};
use workspace::profile::host_compatible::HostWorkspace;
use workspace::profile::WorkspaceModeContext;

use super::contract::{CollectCompletedOutput, CommandCompletion, CommandResponse};
pub(crate) struct HostRun {
    pub(crate) process: CommandProcess,
    pub(crate) trace_origin: CommandTraceOrigin,
    pub(crate) root: PathBuf,
    pub(crate) snapshot: Snapshot,
    pub(crate) workspace: HostWorkspace,
    pub(crate) lease: LeaseReleaseHandle,
}

pub(crate) struct IsolatedNetworkRun {
    pub(crate) process: CommandProcess,
    pub(crate) trace_origin: CommandTraceOrigin,
    pub(crate) context: WorkspaceModeContext,
    pub(crate) remountable: bool,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct CommandTraceOrigin {
    pub(crate) trace_id: Option<String>,
    pub(crate) request_id: Option<String>,
}

impl CommandTraceOrigin {
    pub(crate) fn from_start(request: &StartCommand) -> Self {
        Self {
            trace_id: request.trace_id.clone(),
            request_id: request.request_id.clone(),
        }
    }
}

pub(crate) enum ActiveCommand {
    Host(HostRun),
    IsolatedNetwork(IsolatedNetworkRun),
}

impl ActiveCommand {
    pub(crate) fn process(&self) -> &CommandProcess {
        match self {
            Self::Host(run) => &run.process,
            Self::IsolatedNetwork(run) => &run.process,
        }
    }

    pub(crate) fn trace_origin(&self) -> &CommandTraceOrigin {
        match self {
            Self::Host(run) => &run.trace_origin,
            Self::IsolatedNetwork(run) => &run.trace_origin,
        }
    }
}

const MAX_COMPLETED_ENTRIES: usize = 1024;
const MAX_COLLECT_COMPLETED_BATCH: usize = 8;
pub(crate) const MAX_ACTIVE_COMMANDS: usize = 256;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct CommandAdmissionError {
    pub(crate) active: usize,
    pub(crate) max: usize,
}

impl std::fmt::Display for CommandAdmissionError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            formatter,
            "too many active commands: {} >= {}",
            self.active, self.max
        )
    }
}

pub(crate) struct CommandReservation {
    registry: Arc<CommandRegistry>,
    activated: bool,
}

impl CommandReservation {
    pub(crate) fn activate(mut self, run: Arc<ActiveCommand>) {
        self.registry.insert_reserved(run);
        self.activated = true;
    }
}

impl Drop for CommandReservation {
    fn drop(&mut self) {
        if !self.activated {
            self.registry.active_count.fetch_sub(1, Ordering::AcqRel);
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct CompletionBufferEviction {
    pub(crate) command_id: String,
    pub(crate) seq: u64,
    pub(crate) max_entries: usize,
}

struct CompletedEntry {
    seq: u64,
    completion: CommandCompletion,
}

#[derive(Default)]
pub(crate) struct CommandRegistry {
    runs: Mutex<HashMap<String, HashMap<String, Arc<ActiveCommand>>>>,
    completed: Mutex<HashMap<String, CompletedEntry>>,
    counter: AtomicU64,
    completed_seq: AtomicU64,
    active_count: AtomicUsize,
}

impl CommandRegistry {
    #[must_use]
    pub(crate) fn new() -> Self {
        Self {
            runs: Mutex::new(HashMap::new()),
            completed: Mutex::new(HashMap::new()),
            counter: AtomicU64::new(1),
            completed_seq: AtomicU64::new(1),
            active_count: AtomicUsize::new(0),
        }
    }

    #[must_use]
    pub(crate) fn next_id(&self) -> String {
        format!("cmd_{}", self.counter.fetch_add(1, Ordering::Relaxed))
    }

    pub(crate) fn try_reserve(
        self: &Arc<Self>,
    ) -> Result<CommandReservation, CommandAdmissionError> {
        let mut active = self.active_count.load(Ordering::Acquire);
        loop {
            if active >= MAX_ACTIVE_COMMANDS {
                return Err(CommandAdmissionError {
                    active,
                    max: MAX_ACTIVE_COMMANDS,
                });
            }
            match self.active_count.compare_exchange_weak(
                active,
                active + 1,
                Ordering::AcqRel,
                Ordering::Acquire,
            ) {
                Ok(_) => {
                    return Ok(CommandReservation {
                        registry: Arc::clone(self),
                        activated: false,
                    });
                }
                Err(next) => active = next,
            }
        }
    }

    #[cfg(test)]
    pub(crate) fn insert(&self, run: Arc<ActiveCommand>) {
        self.active_count.fetch_add(1, Ordering::AcqRel);
        self.insert_reserved(run);
    }

    fn insert_reserved(&self, run: Arc<ActiveCommand>) {
        let caller_id = run.process().caller_id().to_owned();
        let command_id = run.process().id().to_owned();
        lock(&self.runs)
            .entry(caller_id)
            .or_default()
            .insert(command_id, run);
    }

    #[must_use]
    pub(crate) fn get(&self, id: &str) -> Option<Arc<ActiveCommand>> {
        lock(&self.runs)
            .values()
            .find_map(|runs| runs.get(id).cloned())
    }

    pub(crate) fn remove(&self, id: &str) -> Option<Arc<ActiveCommand>> {
        let mut runs = lock(&self.runs);
        let caller = runs
            .iter()
            .find(|(_, caller_runs)| caller_runs.contains_key(id))
            .map(|(caller, _)| caller.clone())?;
        let run = runs.get_mut(&caller)?.remove(id);
        if runs.get(&caller).is_some_and(HashMap::is_empty) {
            runs.remove(&caller);
        }
        if run.is_some() {
            self.active_count.fetch_sub(1, Ordering::AcqRel);
        }
        run
    }

    #[must_use]
    pub(crate) fn count_by_caller(&self, caller_id: Option<&str>) -> usize {
        if caller_id.is_none() {
            return self.active_count.load(Ordering::Acquire);
        }
        let runs = lock(&self.runs);
        match caller_id {
            Some(caller) => runs.get(caller).map_or(0, HashMap::len),
            None => runs.values().map(HashMap::len).sum(),
        }
    }

    #[must_use]
    pub(crate) fn live(&self) -> Vec<Arc<ActiveCommand>> {
        lock(&self.runs)
            .values()
            .flat_map(|runs| runs.values().cloned())
            .collect()
    }

    #[must_use]
    pub(crate) fn caller_commands(&self, caller_id: &str) -> Vec<Arc<ActiveCommand>> {
        lock(&self.runs)
            .get(caller_id)
            .map(|runs| runs.values().cloned().collect())
            .unwrap_or_default()
    }

    pub(crate) fn push_completed(
        &self,
        completion: CommandCompletion,
    ) -> Vec<CompletionBufferEviction> {
        let seq = self.completed_seq.fetch_add(1, Ordering::Relaxed);
        let mut completed = lock(&self.completed);
        completed.insert(
            completion.command_id.clone(),
            CompletedEntry { seq, completion },
        );
        let mut evictions = Vec::new();
        while completed.len() > MAX_COMPLETED_ENTRIES {
            let Some((oldest, oldest_seq)) = completed
                .iter()
                .min_by_key(|(_, entry)| entry.seq)
                .map(|(id, entry)| (id.clone(), entry.seq))
            else {
                break;
            };
            completed.remove(&oldest);
            evictions.push(CompletionBufferEviction {
                command_id: oldest,
                seq: oldest_seq,
                max_entries: MAX_COMPLETED_ENTRIES,
            });
        }
        evictions
    }

    pub(crate) fn take_completed_result(&self, id: &str) -> Option<CommandResponse> {
        lock(&self.completed)
            .remove(id)
            .map(|entry| entry.completion.result)
    }

    #[must_use]
    pub(crate) fn completed_result(&self, id: &str) -> Option<CommandResponse> {
        lock(&self.completed)
            .get(id)
            .map(|entry| entry.completion.result.clone())
    }

    #[must_use]
    pub(crate) fn collect_completed(&self, request: &CollectCompleted) -> CollectCompletedOutput {
        let wanted: Option<HashSet<String>> = request
            .command_ids
            .as_ref()
            .map(|ids| ids.iter().cloned().collect());
        let caller_id = request.caller_id.as_deref();
        let mut completed = lock(&self.completed);
        let mut matched: Vec<(String, u64)> = completed
            .iter()
            .filter(|(id, entry)| {
                let id_matches = wanted.as_ref().is_none_or(|ids| ids.contains(*id));
                let caller_matches =
                    caller_id.is_none_or(|caller_id| entry.completion.caller_id == caller_id);
                id_matches && caller_matches
            })
            .map(|(id, entry)| (id.clone(), entry.seq))
            .collect();
        matched.sort_by_key(|(_, seq)| *seq);
        let has_more = matched.len() > MAX_COLLECT_COMPLETED_BATCH;
        matched.truncate(MAX_COLLECT_COMPLETED_BATCH);
        let completions = matched
            .iter()
            .filter_map(|(id, _)| completed.remove(id))
            .map(|entry| entry.completion)
            .collect();
        CollectCompletedOutput {
            success: true,
            completions,
            has_more,
            max_completions: MAX_COLLECT_COMPLETED_BATCH,
        }
    }
}

pub(crate) fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

#[cfg(test)]
#[path = "../../tests/command/registry.rs"]
mod tests;
