{
  const { RunStatus } = window.WorkflowContextOop;

  class IterationOrchestrator {
    constructor(attemptOrchestrator) {
      this.attemptOrchestrator = attemptOrchestrator;
    }

    launchAttempt(workflow, iteration, options = {}) {
      this.assertIterationScope(workflow, iteration);
      if (iteration.attempts.length >= iteration.maxTry) {
        throw new Error(`${iteration.id} has reached max_try=${iteration.maxTry}.`);
      }

      const attempt = this.attemptOrchestrator.launchAttempt(
        workflow,
        iteration,
        { attemptId: options.attemptId || this.nextAttemptId(iteration), planId: options.planId },
      );
      iteration.status = RunStatus.Running;
      iteration.attempts.push(attempt);
      return attempt;
    }

    reconcileAttemptResult(workflow, iteration, attempt) {
      this.assertIterationScope(workflow, iteration);
      if (!attempt || attempt.iterationId !== iteration.id) {
        throw new Error("Attempt does not belong to this iteration.");
      }

      if (attempt.status === RunStatus.Failed) {
        const existingRetry = iteration.attempts.find(candidate => (
          candidate.status === RunStatus.NotStarted || candidate.status === RunStatus.Running
        ));
        if (existingRetry && existingRetry !== attempt) {
          iteration.status = RunStatus.Running;
          return { kind: "retry_open", attempt: existingRetry };
        }
        if (iteration.attempts.length < iteration.maxTry) {
          return { kind: "retry_created", attempt: this.launchAttempt(workflow, iteration) };
        }

        iteration.status = RunStatus.Failed;
        return { kind: "iteration_failed" };
      }

      if (attempt.status === RunStatus.Success) {
        iteration.status = RunStatus.Success;
        return { kind: "iteration_success" };
      }

      iteration.status = RunStatus.Running;
      return { kind: "attempt_running" };
    }

    nextAttemptId(iteration) {
      const nextIndex = iteration.attempts.length + 1;
      return nextIndex === 1 ? "attempt_att_initial" : `attempt_att_retry_${nextIndex}`;
    }

    assertIterationScope(workflow, iteration) {
      if (!workflow || !iteration) {
        throw new Error("Workflow and iteration are required.");
      }
      if (iteration.workflowId !== workflow.id) {
        throw new Error("Iteration does not belong to this workflow.");
      }
    }
  }

  window.WorkflowContextOop.IterationOrchestrator = IterationOrchestrator;
}
