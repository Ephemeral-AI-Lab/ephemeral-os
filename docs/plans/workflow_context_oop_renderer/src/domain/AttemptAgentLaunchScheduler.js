{
  const { RunStatus } = window.WorkflowContextOop;

  class AttemptAgentLaunchScheduler {
    constructor() {
      this.attemptOrchestrator = undefined;
      this.queue = [];
    }

    bindAttemptOrchestrator(attemptOrchestrator) {
      this.attemptOrchestrator = attemptOrchestrator;
    }

    enqueuePlan(attempt) {
      if (attempt?.plan?.status === RunStatus.NotStarted) {
        this.enqueue({ kind: "planner", attemptPath: attempt.folderPath, planId: attempt.plan.id });
      }
    }

    enqueueReadyWorkItems(attempt) {
      if (!attempt || attempt.status !== RunStatus.Running) return;
      attempt.readyWorkItems().forEach(item => {
        this.enqueue({ kind: "worker", attemptPath: attempt.folderPath, workItemId: item.id });
      });
    }

    scheduleReadyAgents(workflow) {
      if (!workflow || !this.attemptOrchestrator) return [];
      workflow.iterations.forEach(iteration => {
        iteration.attempts.forEach(attempt => {
          this.enqueuePlan(attempt);
          this.enqueueReadyWorkItems(attempt);
        });
      });
      return this.flush(workflow);
    }

    flush(workflow) {
      const launched = [];
      while (this.queue.length > 0) {
        const task = this.queue.shift();
        const scope = this.findAttemptScope(workflow, task.attemptPath);
        if (!scope) continue;

        if (task.kind === "planner") {
          const plan = scope.attempt.plan;
          if (!plan || plan.id !== task.planId || plan.status !== RunStatus.NotStarted) continue;
          this.attemptOrchestrator.launchPlanner(scope.workflow, scope.iteration, scope.attempt, { planId: plan.id });
          launched.push(`launch_agent("planner") -> ${plan.id}`);
          continue;
        }

        if (task.kind === "worker") {
          const item = scope.attempt.workItems.find(candidate => candidate.id === task.workItemId);
          if (!item || item.status !== RunStatus.NotStarted) continue;
          if (!scope.attempt.readyWorkItems().some(candidate => candidate.id === item.id)) continue;
          this.attemptOrchestrator.launchWorker(scope.attempt, item.id);
          launched.push(`launch_agent("worker") -> ${item.id}`);
        }
      }
      return launched;
    }

    enqueue(task) {
      if (this.queue.some(candidate => sameTask(candidate, task))) return;
      this.queue.push(task);
    }

    findAttemptScope(workflow, attemptPath) {
      for (const iteration of workflow.iterations) {
        const attempt = iteration.attempts.find(candidate => candidate.folderPath === attemptPath);
        if (attempt) return { workflow, iteration, attempt };
      }
      return undefined;
    }
  }

  function sameTask(left, right) {
    return left.kind === right.kind
      && left.attemptPath === right.attemptPath
      && left.planId === right.planId
      && left.workItemId === right.workItemId;
  }

  window.WorkflowContextOop.AttemptAgentLaunchScheduler = AttemptAgentLaunchScheduler;
}
