{
  const { RunStatus } = window.WorkflowContextOop;

  class WorkflowOrchestrator {
    constructor(factory, iterationOrchestrator) {
      this.factory = factory;
      this.iterationOrchestrator = iterationOrchestrator;
    }

    delegateWorkflow(goal, options = {}) {
      const workflow = this.factory.createWorkflow(goal, options.workflowId);
      this.launchIteration(workflow, workflow.goal, {
        iterationId: options.iterationId || "iteration_it_initial",
        attemptId: options.attemptId || "attempt_att_initial",
        maxTry: options.maxTry,
      });
      return workflow;
    }

    launchIteration(workflow, goal, options = {}) {
      const iteration = this.factory.createIteration(
        workflow,
        options.iterationId || this.nextIterationId(workflow),
        goal,
        RunStatus.Running,
        options.maxTry,
      );
      workflow.status = RunStatus.Running;
      workflow.iterations.push(iteration);
      this.iterationOrchestrator.launchAttempt(workflow, iteration, { attemptId: options.attemptId });
      return iteration;
    }

    reconcileAttemptResult(workflow, iteration, attempt) {
      const iterationResult = this.iterationOrchestrator.reconcileAttemptResult(workflow, iteration, attempt);
      if (iteration.status === RunStatus.Success) {
        if (workflow.iterations[workflow.iterations.length - 1] !== iteration) {
          workflow.status = RunStatus.Running;
          return { kind: "workflow_running", iterationResult };
        }

        const deferredGoal = attempt.plan?.deferredGoalForNextIteration?.trim();
        if (deferredGoal) {
          const nextIteration = this.launchIteration(workflow, deferredGoal);
          return { kind: "workflow_deferred", iterationResult, iteration: nextIteration };
        }

        workflow.status = RunStatus.Success;
        return { kind: "workflow_success", iterationResult };
      }

      if (iteration.status === RunStatus.Failed) {
        workflow.status = RunStatus.Failed;
        return { kind: "workflow_failed", iterationResult };
      }

      workflow.status = RunStatus.Running;
      return { kind: "workflow_running", iterationResult };
    }

    nextIterationId(workflow) {
      return `iteration_it_${workflow.iterations.length + 1}`;
    }
  }

  window.WorkflowContextOop.WorkflowOrchestrator = WorkflowOrchestrator;
}
