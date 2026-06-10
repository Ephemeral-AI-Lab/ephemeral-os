import { RunStatus } from "./RunStatus.js";
import { Workflow } from "./Workflow.js";
import { Iteration } from "./Iteration.js";
import { Attempt } from "./Attempt.js";
import { Plan } from "./Plan.js";
import { WorkItem } from "./WorkItem.js";

export class WorkflowFactory {
  constructor() {
    this.planCounter = 1;
  }

  delegateWorkflow(goal) {
    const workflow = new Workflow({
      id: "workflow_wf_context_projection",
      status: RunStatus.Running,
      folderPath: "workflow_wf_context_projection",
      goal,
      iterations: [],
    });
    const iteration = this.createIteration(workflow, "iteration_it_initial", goal, RunStatus.Running);
    const attempt = this.createAttempt(workflow, iteration, "attempt_att_initial", RunStatus.NotStarted);
    iteration.attempts.push(attempt);
    workflow.iterations.push(iteration);
    return workflow;
  }

  createIteration(workflow, id, goal, status = RunStatus.Running) {
    return new Iteration({
      id,
      status,
      folderPath: `${workflow.folderPath}/${id}`,
      workflowId: workflow.id,
      goal,
      attempts: [],
    });
  }

  createAttempt(workflow, iteration, id, status = RunStatus.NotStarted) {
    return new Attempt({
      id,
      status,
      folderPath: `${iteration.folderPath}/${id}`,
      workflowId: workflow.id,
      iterationId: iteration.id,
      plan: undefined,
      workItems: [],
    });
  }

  createPlan(workflow, iteration, attempt, id = undefined, status = RunStatus.Running) {
    const planId = id || `plan_pln_${this.planCounter++}`;
    return new Plan({
      id: planId,
      status,
      folderPath: `${attempt.folderPath}/${planId}`,
      workflowId: workflow.id,
      iterationId: iteration.id,
      attemptId: attempt.id,
    });
  }

  createWorkItem(workflow, iteration, attempt, plan, input) {
    const id = input.work_item_id;
    return new WorkItem({
      id,
      status: RunStatus.NotStarted,
      folderPath: `${attempt.folderPath}/${id}`,
      workflowId: workflow.id,
      iterationId: iteration.id,
      attemptId: attempt.id,
      planId: plan.id,
      workItemSpec: input.work_item_spec,
      needs: Array.isArray(input.needs) ? input.needs.slice() : [],
    });
  }

  cloneWorkflow(workflow) {
    const clonedWorkflow = new Workflow({
      id: workflow.id,
      status: workflow.status,
      folderPath: workflow.folderPath,
      goal: workflow.goal,
      iterations: [],
    });

    clonedWorkflow.iterations = workflow.iterations.map(iteration => {
      const clonedIteration = new Iteration({
        id: iteration.id,
        status: iteration.status,
        folderPath: iteration.folderPath,
        workflowId: iteration.workflowId,
        goal: iteration.goal,
        attempts: [],
      });

      clonedIteration.attempts = iteration.attempts.map(attempt => {
        const clonedAttempt = new Attempt({
          id: attempt.id,
          status: attempt.status,
          folderPath: attempt.folderPath,
          workflowId: attempt.workflowId,
          iterationId: attempt.iterationId,
          plan: attempt.plan ? this.clonePlan(attempt.plan) : undefined,
          workItems: [],
        });
        clonedAttempt.workItems = attempt.workItems.map(item => this.cloneWorkItem(item));
        return clonedAttempt;
      });
      return clonedIteration;
    });

    return clonedWorkflow;
  }

  clonePlan(plan) {
    return new Plan({
      id: plan.id,
      status: plan.status,
      folderPath: plan.folderPath,
      workflowId: plan.workflowId,
      iterationId: plan.iterationId,
      attemptId: plan.attemptId,
      planSpec: plan.planSpec,
      plannerSummary: plan.plannerSummary,
      deferredGoalForNextIteration: plan.deferredGoalForNextIteration,
    });
  }

  cloneWorkItem(item) {
    return new WorkItem({
      id: item.id,
      status: item.status,
      folderPath: item.folderPath,
      workflowId: item.workflowId,
      iterationId: item.iterationId,
      attemptId: item.attemptId,
      planId: item.planId,
      workItemSpec: item.workItemSpec,
      needs: item.needs.slice(),
      workerSummary: item.workerSummary,
      workerOutcome: item.workerOutcome,
    });
  }
}
