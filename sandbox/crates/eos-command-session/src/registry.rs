use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};

use serde::{Deserialize, Serialize};

use crate::session::CommandSession;
use crate::{CollectCompleted, CollectCompletedResponse, CommandResponse};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CommandSessionCompletion {
    pub command_session_id: String,
    pub caller_id: String,
    pub command: String,
    pub result: CommandResponse,
    pub notification_result: CommandResponse,
}

#[derive(Default)]
pub(crate) struct CommandSessionRegistry {
    sessions: Mutex<HashMap<String, Arc<CommandSession>>>,
    completed: Mutex<HashMap<String, CommandSessionCompletion>>,
    counter: AtomicU64,
}

impl CommandSessionRegistry {
    #[must_use]
    pub(crate) fn new() -> Self {
        Self {
            sessions: Mutex::new(HashMap::new()),
            completed: Mutex::new(HashMap::new()),
            counter: AtomicU64::new(1),
        }
    }

    #[must_use]
    pub(crate) fn next_id(&self) -> String {
        format!("cmd_{}", self.counter.fetch_add(1, Ordering::Relaxed))
    }

    pub(crate) fn insert(&self, session: Arc<CommandSession>) {
        lock(&self.sessions).insert(session.id().to_owned(), session);
    }

    #[must_use]
    pub(crate) fn get(&self, id: &str) -> Option<Arc<CommandSession>> {
        lock(&self.sessions).get(id).cloned()
    }

    pub(crate) fn remove(&self, id: &str) -> Option<Arc<CommandSession>> {
        lock(&self.sessions).remove(id)
    }

    #[must_use]
    pub(crate) fn count_by_caller(&self, caller_id: Option<&str>) -> usize {
        lock(&self.sessions)
            .values()
            .filter(|session| caller_id.is_none_or(|caller_id| session.caller_id() == caller_id))
            .count()
    }

    #[must_use]
    pub(crate) fn live(&self) -> Vec<Arc<CommandSession>> {
        lock(&self.sessions).values().cloned().collect()
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
