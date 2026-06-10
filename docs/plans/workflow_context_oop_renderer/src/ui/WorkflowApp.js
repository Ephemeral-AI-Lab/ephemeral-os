import { RunStatus } from "../domain/RunStatus.js";
import {
  defaultGoal,
  sampleDeferredGoal,
  samplePlannerSummary,
  samplePlanSpec,
  sampleWorkItems,
} from "../sample/SampleData.js";

export class WorkflowApp {
  constructor({ factory, store, projector, controls, fileTree, fileViewer }) {
    this.factory = factory;
    this.store = store;
    this.projector = projector;
    this.controls = controls;
    this.fileTree = fileTree;
    this.fileViewer = fileViewer;
    this.selectedPath = "";
    this.selectedWorkerId = "";
  }

  start() {
    this.controls.setInitialValues();
    this.controls.bind({
      delegateWorkflow: () => this.delegateWorkflow(),
      seedDemo: () => this.seedDemo(),
      resetEmpty: () => this.resetEmpty(),
      launchPlanner: () => this.launchPlanner(),
      submitPlanOutcome: () => this.submitPlanOutcome(),
      launchNextWorker: () => this.launchNextWorker(),
      submitWorkerOutcome: isSuccess => this.submitWorkerOutcome(isSuccess),
      selectWorker: workerId => {
        this.selectedWorkerId = workerId;
        this.reloadAndRender();
      },
    });
    this.fileTree.onSelect(path => {
      this.selectedPath = path;
      this.reloadAndRender();
    });
    this.delegateWorkflow(defaultGoal());
  }

  delegateWorkflow(goal = undefined) {
    this.controls.showError("");
    const workflow = this.factory.delegateWorkflow(goal || this.controls.readWorkflowGoal());
    this.selectedPath = workflow.specPath();
    this.store.save(workflow, "delegate_workflow initialized workflow, first iteration, and first attempt");
    this.reloadAndRender();
  }

  resetEmpty() {
    this.controls.showError("");
    this.store.clear();
    this.selectedPath = "";
    this.selectedWorkerId = "";
    this.controls.setInitialValues();
    this.reloadAndRender();
  }

  seedDemo() {
    this.controls.showError("");
    const workflow = this.factory.delegateWorkflow(defaultGoal());
    const iteration = workflow.iterations[0];
    const attempt = iteration.attempts[0];
    attempt.status = RunStatus.Running;
    attempt.plan = this.factory.createPlan(workflow, iteration, attempt, "plan_pln_initial", RunStatus.Success);
    attempt.plan.planSpec = samplePlanSpec();
    attempt.plan.plannerSummary = samplePlannerSummary();
    attempt.plan.deferredGoalForNextIteration = sampleDeferredGoal();
    attempt.workItems = sampleWorkItems().map(input => this.factory.createWorkItem(workflow, iteration, attempt, attempt.plan, input));

    attempt.workItems[0].status = RunStatus.Success;
    attempt.workItems[0].workerSummary = "Schema fields and parent references were modeled with a shared base value and typed IDs.";
    attempt.workItems[0].workerOutcome = "Defined WorkflowEntityBase and denormalized back-references for iteration, attempt, plan, and work item entities.";
    attempt.workItems[1].status = RunStatus.Running;

    this.selectedPath = attempt.briefPath();
    this.selectedWorkerId = attempt.workItems[1].id;
    this.store.save(workflow, "seeded demo aggregate with planner outcome and worker progress");
    this.reloadAndRender();
  }

  launchPlanner() {
    this.withWorkflow("delegate_workflow must run first.", workflow => {
      const iteration = workflow.activeIteration();
      const attempt = iteration?.activeAttempt();
      if (!iteration || !attempt) throw new Error("No active attempt is available.");
      if (attempt.plan) throw new Error("The active attempt already has a plan.");
      attempt.plan = this.factory.createPlan(workflow, iteration, attempt);
      attempt.status = RunStatus.Running;
      this.selectedPath = attempt.plan.briefPath();
      return "planner launched; plan initialized as Running";
    });
  }

  submitPlanOutcome() {
    this.withWorkflow("delegate_workflow must run first.", workflow => {
      const iteration = workflow.activeIteration();
      const attempt = iteration?.activeAttempt();
      if (!iteration || !attempt) throw new Error("No active attempt is available.");
      if (!attempt.plan) throw new Error("Launch planner before submitting planner outcome.");
      if (attempt.plan.status === RunStatus.Success) throw new Error("Planner outcome was already submitted for this attempt.");

      const input = this.controls.readPlannerOutcome();
      validateWorkItems(input.workItems);
      attempt.plan.status = RunStatus.Success;
      attempt.plan.planSpec = input.planSpec;
      attempt.plan.plannerSummary = input.plannerSummary;
      attempt.plan.deferredGoalForNextIteration = input.deferredGoalForNextIteration || "";
      attempt.workItems = input.workItems.map(item => this.factory.createWorkItem(workflow, iteration, attempt, attempt.plan, item));
      attempt.status = RunStatus.Running;
      this.selectedPath = attempt.specPath();
      return "submit_plan_outcome created work items and reprojected context files";
    });
  }

  launchNextWorker() {
    this.withWorkflow("delegate_workflow must run first.", workflow => {
      const attempt = workflow.activeIteration()?.activeAttempt();
      if (!attempt || attempt.status !== RunStatus.Running) throw new Error("No running attempt can launch workers.");
      const ready = attempt.readyWorkItems()[0];
      if (!ready) throw new Error("No not-started work item has all dependencies complete.");
      ready.status = RunStatus.Running;
      this.selectedWorkerId = ready.id;
      this.selectedPath = ready.briefPath();
      return `worker launched for ${ready.id}`;
    });
  }

  submitWorkerOutcome(isSuccess) {
    this.withWorkflow("delegate_workflow must run first.", workflow => {
      const input = this.controls.readWorkerOutcome();
      const found = findWorkItem(workflow, input.workItemId);
      if (!found) throw new Error("Select a worker item.");
      if (found.item.status !== RunStatus.Running) throw new Error("Launch this work item before submitting its outcome.");

      found.item.status = isSuccess ? RunStatus.Success : RunStatus.Failed;
      found.item.workerSummary = input.workerSummary || (isSuccess
        ? "Worker completed the assigned context projection work."
        : "Worker could not complete the assigned context projection work.");
      found.item.workerOutcome = input.workerOutcome || (isSuccess
        ? "The requested artifact was produced and can be rendered from the latest DB aggregate."
        : "The worker found a blocking issue and the attempt needs a retry.");

      this.recomputeAfterWorker(workflow, found.iteration, found.attempt);
      this.selectedPath = found.item.specPath();
      return `submit_worker_outcome recorded ${found.item.id} as ${found.item.status}`;
    });
  }

  withWorkflow(missingMessage, mutator) {
    this.controls.showError("");
    const workflow = this.store.loadFreshWorkflow();
    if (!workflow) {
      this.controls.showError(missingMessage);
      return;
    }

    try {
      const event = mutator(workflow);
      this.store.save(workflow, event);
      this.reloadAndRender();
    } catch (error) {
      this.controls.showError(error.message);
    }
  }

  recomputeAfterWorker(workflow, iteration, attempt) {
    if (attempt.hasFailedWorkItem()) {
      attempt.status = RunStatus.Failed;
      if (!iteration.attempts.some(candidate => candidate.status === RunStatus.NotStarted)) {
        const retryIndex = iteration.attempts.length + 1;
        iteration.attempts.push(this.factory.createAttempt(workflow, iteration, `attempt_att_retry_${retryIndex}`, RunStatus.NotStarted));
      }
      return;
    }

    if (attempt.allWorkItemsSucceeded()) {
      attempt.status = RunStatus.Success;
      iteration.status = RunStatus.Success;
      const deferred = attempt.plan?.deferredGoalForNextIteration?.trim();
      if (deferred) {
        const nextIndex = workflow.iterations.length + 1;
        const nextIteration = this.factory.createIteration(workflow, `iteration_it_${nextIndex}`, deferred, RunStatus.Running);
        nextIteration.attempts.push(this.factory.createAttempt(workflow, nextIteration, `attempt_att_${nextIndex}_initial`, RunStatus.NotStarted));
        workflow.iterations.push(nextIteration);
        workflow.status = RunStatus.Running;
      } else {
        workflow.status = RunStatus.Success;
      }
      return;
    }

    attempt.status = RunStatus.Running;
  }

  reloadAndRender() {
    const workflow = this.store.loadFreshWorkflow();
    const files = this.projector.project(workflow);
    if (files.length && !files.some(file => file.path === this.selectedPath)) {
      this.selectedPath = files[0].path;
    }

    this.controls.renderHeader({
      version: this.store.currentVersion(),
      workflow,
      counts: countEntities(workflow),
    });
    this.controls.renderForms(workflow, this.selectedWorkerId);
    this.controls.renderEvents(this.store.events());
    this.fileTree.render(workflow, files, this.selectedPath);
    this.fileViewer.render(files.find(file => file.path === this.selectedPath));
  }
}

function countEntities(workflow) {
  if (!workflow) return { iterations: 0, attempts: 0, plans: 0, workItems: 0 };
  const attempts = workflow.iterations.flatMap(iteration => iteration.attempts);
  return {
    iterations: workflow.iterations.length,
    attempts: attempts.length,
    plans: attempts.filter(attempt => attempt.plan).length,
    workItems: attempts.reduce((sum, attempt) => sum + attempt.workItems.length, 0),
  };
}

function findWorkItem(workflow, workItemId) {
  for (const iteration of workflow.iterations) {
    for (const attempt of iteration.attempts) {
      const item = attempt.workItems.find(candidate => candidate.id === workItemId);
      if (item) return { iteration, attempt, item };
    }
  }
  return undefined;
}

function validateWorkItems(workItems) {
  if (!Array.isArray(workItems) || workItems.length === 0) {
    throw new Error("work_items JSON must be a non-empty array.");
  }

  const ids = new Set();
  for (const item of workItems) {
    if (!item.work_item_id || !item.work_item_spec) {
      throw new Error("Each work item needs work_item_id and work_item_spec.");
    }
    if (ids.has(item.work_item_id)) {
      throw new Error(`Duplicate work_item_id: ${item.work_item_id}`);
    }
    ids.add(item.work_item_id);
  }

  for (const item of workItems) {
    for (const need of item.needs || []) {
      if (!ids.has(need)) {
        throw new Error(`${item.work_item_id} needs unknown work item ${need}.`);
      }
    }
  }
}
