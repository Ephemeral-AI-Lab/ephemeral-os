use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};

use eos_types::{
    AgentName, AgentRegistry, AgentType, GeneratorId, PlannerPlan, Task, TaskId, TaskStatus,
};

use crate::{Result, WorkflowError};

/// Closed scheduler resolution for a persisted plan DAG.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DagResolution {
    /// More work may still be runnable or in flight.
    Running,
    /// Every persisted plan task is done.
    Passed,
    /// The DAG is quiescent because at least one task failed or blocked.
    FailedOrBlocked,
}

/// Pending plan tasks whose needs are all done.
pub fn ready_pending_plan_ids(tasks: &[Task]) -> Result<Vec<TaskId>> {
    let statuses = statuses_by_id(tasks);
    validate_persisted_needs(tasks, &statuses)?;
    Ok(tasks
        .iter()
        .filter(|task| task.status == TaskStatus::Pending)
        .filter(|task| {
            task.needs
                .iter()
                .all(|dep| statuses.get(dep).is_some_and(|s| *s == TaskStatus::Done))
        })
        .map(|task| task.id.clone())
        .collect())
}

pub(crate) fn dag_resolution(tasks: &[Task]) -> Result<DagResolution> {
    let statuses = statuses_by_id(tasks);
    validate_persisted_needs(tasks, &statuses)?;
    let unreachable = unreachable_pending_ids(tasks, &statuses)?;
    let all_done = statuses.values().all(|s| *s == TaskStatus::Done);
    if all_done {
        return Ok(DagResolution::Passed);
    }
    let all_quiescent = statuses.iter().all(|(task_id, status)| {
        status.is_terminal_generator()
            || (*status == TaskStatus::Pending && unreachable.contains(task_id))
    });
    let any_failed_or_blocked = statuses.values().any(|s| {
        matches!(
            s,
            TaskStatus::Failed | TaskStatus::Blocked | TaskStatus::Cancelled
        )
    });
    if all_quiescent && any_failed_or_blocked {
        Ok(DagResolution::FailedOrBlocked)
    } else {
        Ok(DagResolution::Running)
    }
}

fn statuses_by_id(tasks: &[Task]) -> HashMap<TaskId, TaskStatus> {
    tasks
        .iter()
        .map(|task| (task.id.clone(), task.status))
        .collect()
}

fn validate_persisted_needs(tasks: &[Task], statuses: &HashMap<TaskId, TaskStatus>) -> Result<()> {
    for task in tasks {
        let missing: Vec<String> = task
            .needs
            .iter()
            .filter(|dep| !statuses.contains_key(*dep))
            .map(ToString::to_string)
            .collect();
        if !missing.is_empty() {
            return Err(WorkflowError::invariant(format!(
                "plan task {:?} has unknown persisted needs: {missing:?}",
                task.id.as_str()
            )));
        }
    }
    Ok(())
}

fn unreachable_pending_ids(
    tasks: &[Task],
    statuses: &HashMap<TaskId, TaskStatus>,
) -> Result<HashSet<TaskId>> {
    let by_id: HashMap<TaskId, &Task> = tasks.iter().map(|task| (task.id.clone(), task)).collect();
    let mut visiting = HashSet::new();
    let mut memo = HashMap::new();
    let mut unreachable = HashSet::new();
    for (task_id, status) in statuses {
        if *status == TaskStatus::Pending
            && is_unreachable(task_id, statuses, &by_id, &mut visiting, &mut memo)?
        {
            unreachable.insert(task_id.clone());
        }
    }
    Ok(unreachable)
}

fn is_unreachable(
    task_id: &TaskId,
    statuses: &HashMap<TaskId, TaskStatus>,
    by_id: &HashMap<TaskId, &Task>,
    visiting: &mut HashSet<TaskId>,
    memo: &mut HashMap<TaskId, bool>,
) -> Result<bool> {
    if let Some(value) = memo.get(task_id) {
        return Ok(*value);
    }
    if !visiting.insert(task_id.clone()) {
        return Err(WorkflowError::invariant(format!(
            "plan task dependency cycle reached persisted task {:?}",
            task_id.as_str()
        )));
    }
    let status = statuses
        .get(task_id)
        .copied()
        .ok_or_else(|| WorkflowError::not_found("task", task_id.as_str()))?;
    if status != TaskStatus::Pending {
        visiting.remove(task_id);
        memo.insert(task_id.clone(), false);
        return Ok(false);
    }
    let task = by_id
        .get(task_id)
        .ok_or_else(|| WorkflowError::not_found("task", task_id.as_str()))?;
    for dep in &task.needs {
        let dep_status = statuses
            .get(dep)
            .copied()
            .ok_or_else(|| WorkflowError::not_found("task", dep.as_str()))?;
        if matches!(
            dep_status,
            TaskStatus::Failed | TaskStatus::Blocked | TaskStatus::Cancelled
        ) || (dep_status == TaskStatus::Pending
            && is_unreachable(dep, statuses, by_id, visiting, memo)?)
        {
            visiting.remove(task_id);
            memo.insert(task_id.clone(), true);
            return Ok(true);
        }
    }
    visiting.remove(task_id);
    memo.insert(task_id.clone(), false);
    Ok(false)
}

pub(crate) fn validate_plan_shape(plan: &PlannerPlan) -> Result<()> {
    if plan.reducers.is_empty() {
        return Err(WorkflowError::invariant(
            "plan must contain at least one reducer",
        ));
    }
    let mut generator_ids = BTreeSet::new();
    for task in &plan.tasks {
        if !generator_ids.insert(&task.generator_id) {
            return Err(WorkflowError::invariant(format!(
                "plan contains duplicate generator id {:?}",
                task.generator_id
            )));
        }
    }

    let mut reducer_ids = BTreeSet::new();
    for reducer in &plan.reducers {
        if !reducer_ids.insert(&reducer.reducer_id) {
            return Err(WorkflowError::invariant(format!(
                "plan contains duplicate reducer id {:?}",
                reducer.reducer_id
            )));
        }
    }

    for task in &plan.tasks {
        for need in &task.needs {
            if !generator_ids.contains(need) {
                return Err(WorkflowError::invariant(format!(
                    "generator task {:?} has unknown generator needs: {:?}",
                    task.generator_id, need
                )));
            }
        }
    }
    let mut downstream_by_generator: BTreeMap<&GeneratorId, Vec<&str>> =
        generator_ids.iter().map(|id| (*id, Vec::new())).collect();
    for task in &plan.tasks {
        for need in &task.needs {
            downstream_by_generator
                .get_mut(need)
                .expect("generator needs were validated above")
                .push(task.generator_id.as_str());
        }
    }
    for reducer in &plan.reducers {
        if reducer.needs.is_empty() {
            return Err(WorkflowError::invariant(format!(
                "reducer task {:?} must need at least one generator",
                reducer.reducer_id
            )));
        }
        for need in &reducer.needs {
            if !generator_ids.contains(need) {
                return Err(WorkflowError::invariant(format!(
                    "reducer task {:?} has unknown generator needs: {:?}",
                    reducer.reducer_id, need
                )));
            }
            downstream_by_generator
                .get_mut(need)
                .expect("reducer needs were validated above")
                .push(reducer.reducer_id.as_str());
        }
    }
    let dangling: Vec<&str> = downstream_by_generator
        .iter()
        .filter_map(|(id, downstream)| downstream.is_empty().then_some(id.as_str()))
        .collect();
    if !dangling.is_empty() {
        return Err(WorkflowError::invariant(format!(
            "plan has generator(s) no downstream task needs: {dangling:?}"
        )));
    }
    assert_acyclic(plan)
}

/// Validate every plan agent before any task row is written: each generator
/// is bound to a registered workflow-launchable profile and has a task spec,
/// and the fixed `reducer` profile is registered. Runs after the pure shape
/// checks and before materialization so a rejected plan leaves no orphan rows.
pub(crate) fn validate_plan_agents(plan: &PlannerPlan, registry: &AgentRegistry) -> Result<()> {
    for task in &plan.tasks {
        let agent_name = AgentName::new(task.agent_name.clone())?;
        let agent_def = registry.get(&agent_name).ok_or_else(|| {
            WorkflowError::AgentDefinition(format!(
                "agent definition {:?} is not registered",
                task.agent_name
            ))
        })?;
        // D6: a generator task must be bound to an agent-type profile. The
        // generator role itself is task lineage, not profile metadata.
        if agent_def.agent_type != AgentType::Agent {
            return Err(WorkflowError::invariant(format!(
                "generator task {:?} is bound to agent {:?} with type {:?}, expected agent",
                task.generator_id, task.agent_name, agent_def.agent_type
            )));
        }
        if !plan.task_specs.contains_key(&task.generator_id) {
            return Err(WorkflowError::not_found(
                "task spec",
                task.generator_id.as_str(),
            ));
        }
    }
    let reducer_name = AgentName::new("reducer")?;
    let reducer = registry.get(&reducer_name).ok_or_else(|| {
        WorkflowError::AgentDefinition("agent definition \"reducer\" is not registered".to_owned())
    })?;
    if reducer.agent_type != AgentType::Agent {
        return Err(WorkflowError::invariant(format!(
            "reducer profile has type {:?}, expected agent",
            reducer.agent_type
        )));
    }
    Ok(())
}

fn assert_acyclic(plan: &PlannerPlan) -> Result<()> {
    let mut by_needs: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
    for task in &plan.tasks {
        by_needs.insert(
            task.generator_id.as_str(),
            task.needs.iter().map(GeneratorId::as_str).collect(),
        );
    }
    let mut remaining = by_needs
        .iter()
        .map(|(id, needs)| (*id, needs.iter().copied().collect::<BTreeSet<_>>()))
        .collect::<BTreeMap<_, _>>();
    let mut dependents: BTreeMap<&str, Vec<&str>> =
        by_needs.keys().map(|id| (*id, Vec::new())).collect();
    for (id, needs) in &by_needs {
        for need in needs {
            if let Some(entries) = dependents.get_mut(need) {
                entries.push(id);
            }
        }
    }
    let mut ready = remaining
        .iter()
        .filter_map(|(id, needs)| needs.is_empty().then_some(*id))
        .collect::<VecDeque<_>>();
    let mut order = Vec::new();
    while let Some(id) = ready.pop_front() {
        order.push(id);
        for dependent in dependents.get(id).into_iter().flatten() {
            if let Some(needs) = remaining.get_mut(dependent) {
                needs.remove(id);
                if needs.is_empty() {
                    ready.push_back(dependent);
                }
            }
        }
    }
    if order.len() != by_needs.len() {
        let ordered = order.into_iter().collect::<BTreeSet<_>>();
        let cycle = by_needs
            .keys()
            .filter(|id| !ordered.contains(**id))
            .copied()
            .collect::<Vec<_>>();
        return Err(WorkflowError::invariant(format!(
            "plan contains a dependency cycle among: {cycle:?}"
        )));
    }
    Ok(())
}

#[cfg(test)]
#[path = "../../tests/attempt/plan_dag/mod.rs"]
mod tests;
