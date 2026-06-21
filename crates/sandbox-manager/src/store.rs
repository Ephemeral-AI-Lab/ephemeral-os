use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Mutex, MutexGuard};

use crate::{ManagerError, SandboxDaemonEndpoint, SandboxId, SandboxRecord, SandboxState};

#[derive(Debug, Default)]
pub struct SandboxStore {
    records: Mutex<HashMap<SandboxId, SandboxRecord>>,
}

impl SandboxStore {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    pub fn create(
        &self,
        id: SandboxId,
        workspace_root: PathBuf,
    ) -> Result<SandboxRecord, ManagerError> {
        let mut records = self.records()?;
        if records.contains_key(&id) {
            return Err(ManagerError::DuplicateSandbox { id });
        }
        let record = SandboxRecord::new(id.clone(), workspace_root, SandboxState::Creating);
        records.insert(id, record.clone());
        Ok(record)
    }

    pub fn insert(&self, record: SandboxRecord) -> Result<SandboxRecord, ManagerError> {
        let mut records = self.records()?;
        if records.contains_key(&record.id) {
            return Err(ManagerError::DuplicateSandbox {
                id: record.id.clone(),
            });
        }
        records.insert(record.id.clone(), record.clone());
        Ok(record)
    }

    pub fn update(&self, record: SandboxRecord) -> Result<SandboxRecord, ManagerError> {
        let mut records = self.records()?;
        if !records.contains_key(&record.id) {
            return Err(ManagerError::MissingSandbox {
                id: record.id.clone(),
            });
        }
        records.insert(record.id.clone(), record.clone());
        Ok(record)
    }

    pub fn list(&self) -> Result<Vec<SandboxRecord>, ManagerError> {
        let mut records = self.records()?.values().cloned().collect::<Vec<_>>();
        records.sort_by(|left, right| left.id.cmp(&right.id));
        Ok(records)
    }

    pub fn inspect(&self, id: &SandboxId) -> Result<SandboxRecord, ManagerError> {
        self.records()?
            .get(id)
            .cloned()
            .ok_or_else(|| ManagerError::MissingSandbox { id: id.clone() })
    }

    pub fn remove(&self, id: &SandboxId) -> Result<SandboxRecord, ManagerError> {
        self.records()?
            .remove(id)
            .ok_or_else(|| ManagerError::MissingSandbox { id: id.clone() })
    }

    pub fn transition_state(
        &self,
        id: &SandboxId,
        from: SandboxState,
        to: SandboxState,
    ) -> Result<SandboxRecord, ManagerError> {
        let mut records = self.records()?;
        let record = records
            .get_mut(id)
            .ok_or_else(|| ManagerError::MissingSandbox { id: id.clone() })?;
        if record.state != from {
            return Err(ManagerError::InvalidStateTransition {
                id: id.clone(),
                from: record.state,
                to,
            });
        }
        record.state = to;
        Ok(record.clone())
    }

    pub fn set_state(
        &self,
        id: &SandboxId,
        state: SandboxState,
    ) -> Result<SandboxRecord, ManagerError> {
        let mut records = self.records()?;
        let record = records
            .get_mut(id)
            .ok_or_else(|| ManagerError::MissingSandbox { id: id.clone() })?;
        record.state = state;
        Ok(record.clone())
    }

    pub fn update_endpoint(
        &self,
        id: &SandboxId,
        endpoint: Option<SandboxDaemonEndpoint>,
    ) -> Result<SandboxRecord, ManagerError> {
        let mut records = self.records()?;
        let record = records
            .get_mut(id)
            .ok_or_else(|| ManagerError::MissingSandbox { id: id.clone() })?;
        record.daemon = endpoint;
        Ok(record.clone())
    }

    fn records(&self) -> Result<MutexGuard<'_, HashMap<SandboxId, SandboxRecord>>, ManagerError> {
        self.records.lock().map_err(|_| ManagerError::StorePoisoned)
    }
}
