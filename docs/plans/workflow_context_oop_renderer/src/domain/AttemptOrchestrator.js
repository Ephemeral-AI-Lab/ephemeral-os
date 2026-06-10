{
  const { RunStatus } = window.WorkflowContextOop;

  class AttemptOrchestrator {
    constructor(factory, launchScheduler = undefined) {
      this.factory = factory;
      this.launchScheduler = launchScheduler;
    }

    launchAttempt(workflow, iteration, options = {}) {
      if (!workflow || !iteration || iteration.workflowId !== workflow.id) {
        throw new Error("Workflow and iteration are required to launch an attempt.");
      }

      const attempt = this.factory.createAttempt(
        workflow,
        iteration,
        options.attemptId,
        RunStatus.NotStarted,
      );
      attempt.plan = this.factory.createPlan(
        workflow,
        iteration,
        attempt,
        options.planId,
        RunStatus.NotStarted,
      );
      this.launchScheduler?.enqueuePlan(attempt);
      return attempt;
    }

    launchPlanner(workflow, iteration, attempt, options = {}) {
      this.assertAttemptScope(workflow, iteration, attempt);
      if (!attempt.plan) {
        attempt.plan = this.factory.createPlan(workflow, iteration, attempt, options.planId, RunStatus.NotStarted);
      }
      if (attempt.plan.status !== RunStatus.NotStarted) {
        throw new Error("The planner agent is already launched or completed.");
      }

      attempt.status = RunStatus.Running;
      attempt.plan.status = RunStatus.Running;
      return attempt.plan;
    }

    materializeWorkItems(workflow, iteration, attempt, plannerOutcome) {
      this.assertAttemptScope(workflow, iteration, attempt);
      if (!attempt.plan) {
        throw new Error("launchPlanner must run before submit_planner_outcome.");
      }
      if (attempt.plan.status !== RunStatus.Running) {
        throw new Error("Planner outcome can only be submitted for a running planner agent.");
      }

      this.validateWorkItems(plannerOutcome.workItems);
      attempt.plan.status = RunStatus.Success;
      attempt.plan.planSpec = plannerOutcome.planSpec || "";
      attempt.plan.plannerSummary = plannerOutcome.plannerSummary || "";
      attempt.plan.deferredGoalForNextIteration = plannerOutcome.deferredGoalForNextIteration || "";
      attempt.workItems = plannerOutcome.workItems.map(item => (
        this.factory.createWorkItem(workflow, iteration, attempt, attempt.plan, item)
      ));
      attempt.status = RunStatus.Running;
      this.launchScheduler?.enqueueReadyWorkItems(attempt);
      return attempt.workItems;
    }

    launchNextWorker(attempt) {
      if (!attempt || attempt.status !== RunStatus.Running) {
        throw new Error("No running attempt can dispatch workers.");
      }

      const ready = attempt.readyWorkItems()[0];
      if (!ready) {
        throw new Error("No not-started work item has all dependencies complete.");
      }

      ready.status = RunStatus.Running;
      return ready;
    }

    launchWorker(attempt, workItemId) {
      if (!attempt || attempt.status !== RunStatus.Running) {
        throw new Error("No running attempt can dispatch workers.");
      }

      const workItem = attempt.workItems.find(candidate => candidate.id === workItemId);
      if (!workItem) {
        throw new Error(`Unknown work item: ${workItemId}`);
      }
      if (workItem.status !== RunStatus.NotStarted) {
        throw new Error(`${workItem.id} is already ${workItem.status}.`);
      }
      if (!attempt.readyWorkItems().some(candidate => candidate.id === workItem.id)) {
        throw new Error(`${workItem.id} is waiting for dependency work items.`);
      }

      workItem.status = RunStatus.Running;
      return workItem;
    }

    submitWorkerOutcome(workflow, iteration, attempt, workItemId, outcome, isSuccess) {
      this.assertAttemptScope(workflow, iteration, attempt);
      const workItem = attempt.workItems.find(candidate => candidate.id === workItemId);
      if (!workItem) {
        throw new Error(`Unknown work item: ${workItemId}`);
      }
      if (workItem.status !== RunStatus.Running) {
        throw new Error(`${workItem.id} must be Running before submit_worker_outcome.`);
      }

      workItem.status = isSuccess ? RunStatus.Success : RunStatus.Failed;
      workItem.workerSummary = outcome.workerSummary || (isSuccess
        ? "Worker completed the assigned context projection work."
        : "Worker could not complete the assigned context projection work.");
      workItem.workerOutcome = outcome.workerOutcome || (isSuccess
        ? "The requested artifact was produced and can be rendered from the latest aggregate."
        : "The worker found a blocking issue and the attempt needs a retry.");

      this.recomputeAfterWorker(workflow, iteration, attempt);
      this.launchScheduler?.enqueueReadyWorkItems(attempt);
      return workItem;
    }

    recomputeAfterWorker(workflow, iteration, attempt) {
      if (attempt.hasFailedWorkItem()) {
        attempt.status = RunStatus.Failed;
        return;
      }

      if (attempt.allWorkItemsSucceeded()) {
        attempt.status = RunStatus.Success;
        return;
      }

      attempt.status = RunStatus.Running;
    }

    validateWorkItems(workItems) {
      if (!Array.isArray(workItems) || workItems.length === 0) {
        throw new Error("work_items must be a non-empty array.");
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

    assertAttemptScope(workflow, iteration, attempt) {
      if (!workflow || !iteration || !attempt) {
        throw new Error("Workflow, iteration, and attempt are required.");
      }
      if (iteration.workflowId !== workflow.id || attempt.iterationId !== iteration.id) {
        throw new Error("Attempt scope does not match the workflow aggregate.");
      }
    }
  }

  window.WorkflowContextOop.AttemptOrchestrator = AttemptOrchestrator;
}
