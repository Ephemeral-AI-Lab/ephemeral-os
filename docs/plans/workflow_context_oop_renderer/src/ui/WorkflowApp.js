{
  const {
    RunStatus,
    defaultGoal,
    sampleDeferredGoal,
    samplePlannerSummary,
    samplePlanSpec,
    sampleWorkItems,
  } = window.WorkflowContextOop;

  class WorkflowApp {
    constructor({
      factory,
      store,
      projector,
      workflowOrchestrator,
      attemptOrchestrator,
      launchScheduler,
      fileTree,
      fileViewer,
      actionsView,
      statusStripEl,
      versionPillEl,
      eventCountEl,
      dbLogEl,
    }) {
      this.factory = factory;
      this.store = store;
      this.projector = projector;
      this.workflowOrchestrator = workflowOrchestrator;
      this.attemptOrchestrator = attemptOrchestrator;
      this.launchScheduler = launchScheduler;
      this.fileTree = fileTree;
      this.fileViewer = fileViewer;
      this.actionsView = actionsView;
      this.statusStripEl = statusStripEl;
      this.versionPillEl = versionPillEl;
      this.eventCountEl = eventCountEl;
      this.dbLogEl = dbLogEl;
      this.selectedPath = "";
    }

    start() {
      this.actionsView.setWorkflowGoal(defaultGoal());
      this.actionsView.bind({
        delegateWorkflow: () => this.runAction(() => this.delegateWorkflow(this.actionsView.readWorkflowGoal())),
        runAction: action => this.runSelectedAction(action),
      });
      this.fileTree.onSelect(path => {
        this.selectedPath = path;
        this.reloadAndRender();
      });
      this.reloadAndRender();
      window.workflowContextApp = this;
    }

    delegateWorkflow(goal = defaultGoal()) {
      const workflow = this.workflowOrchestrator.delegateWorkflow(goal || defaultGoal());
      const attempt = workflow.iterations[0]?.attempts[0];
      this.selectedPath = attempt?.plan?.briefPath() || workflow.specPath();
      const launched = this.launchScheduler.scheduleReadyAgents(workflow);
      this.store.save(workflow, eventWithLaunches(
        "delegate_workflow initialized workflow, first iteration, first attempt, and plan",
        launched,
      ));
      this.reloadAndRender();
      return workflow;
    }

    runSelectedAction(action) {
      const actions = {
        submitPlannerOutcomeDeferred: () => this.submitPlannerOutcomeForSelectedPlan(true),
        submitPlannerOutcomeFinal: () => this.submitPlannerOutcomeForSelectedPlan(false),
        submitWorkerSuccess: () => this.submitWorkerOutcomeForSelectedWorkItem(true),
        submitWorkerFailure: () => this.submitWorkerOutcomeForSelectedWorkItem(false),
      };
      const handler = actions[action];
      if (!handler) {
        this.actionsView.showError(`Unknown action: ${action}`);
        return undefined;
      }
      return this.runAction(handler);
    }

    runAction(action) {
      this.actionsView.showError("");
      try {
        return action();
      } catch (error) {
        this.actionsView.showError(error.message);
        return undefined;
      }
    }

    submitPlannerOutcomeForSelectedPlan(withDeferredGoal) {
      return this.withSelectedContext("plan", ({ workflow, iteration, attempt, plan }) => {
        this.attemptOrchestrator.materializeWorkItems(workflow, iteration, attempt, {
          planSpec: samplePlanSpec(),
          plannerSummary: samplePlannerSummary(),
          deferredGoalForNextIteration: withDeferredGoal ? sampleDeferredGoal() : "",
          workItems: sampleWorkItems(),
        });
        const nextReady = attempt.readyWorkItems()[0];
        this.selectedPath = nextReady?.briefPath() || plan.briefPath();
        return `submit_planner_outcome materialized work_items for ${attempt.id}`;
      });
    }

    submitWorkerOutcomeForSelectedWorkItem(isSuccess) {
      return this.withSelectedContext("workItem", ({ workflow, iteration, attempt, workItem }) => {
        const item = this.attemptOrchestrator.submitWorkerOutcome(
          workflow,
          iteration,
          attempt,
          workItem.id,
          this.sampleWorkerOutcome(workItem, isSuccess),
          isSuccess,
        );
        const result = this.workflowOrchestrator.reconcileAttemptResult(workflow, iteration, attempt);
        this.selectedPath = this.nextSelectionAfterWorkerResult(result, item, workflow);
        return `submit_worker_outcome recorded ${item.id} as ${item.status}; ${result.kind}`;
      });
    }

    withSelectedContext(expectedKind, mutator) {
      return this.withWorkflow(workflow => {
        const context = this.resolveSelectedContext(workflow);
        if (context.kind !== expectedKind) {
          throw new Error(`Select a ${expectedKind} file first.`);
        }
        return mutator(context);
      });
    }

    withWorkflow(mutator) {
      const workflow = this.store.loadFreshWorkflow();
      if (!workflow) {
        throw new Error("delegate_workflow must run first.");
      }

      const event = mutator(workflow);
      const launched = this.launchScheduler.scheduleReadyAgents(workflow);
      this.store.save(workflow, eventWithLaunches(event, launched));
      this.reloadAndRender();
      return workflow;
    }

    reloadAndRender() {
      const workflow = this.store.loadFreshWorkflow();
      const files = this.projector.project(workflow);
      if (files.length && !files.some(file => file.path === this.selectedPath)) {
        this.selectedPath = files[0].path;
      }

      this.renderHeader(workflow);
      this.actionsView.render(this.resolveSelectedContext(workflow));
      this.renderEvents();
      this.fileTree.render(workflow, files, this.selectedPath);
      this.fileViewer.render(files.find(file => file.path === this.selectedPath));
    }

    resolveSelectedContext(workflow) {
      if (!workflow) return { kind: "none", workflow: undefined };
      const selectedPath = this.selectedPath;
      if (!selectedPath) return { kind: "none", workflow };
      if (matchesEntity(workflow, selectedPath)) return { kind: "workflow", workflow, entity: workflow };

      for (const iteration of workflow.iterations) {
        if (matchesEntity(iteration, selectedPath)) {
          return { kind: "iteration", workflow, iteration, entity: iteration };
        }
        for (const attempt of iteration.attempts) {
          if (matchesEntity(attempt, selectedPath)) {
            return { kind: "attempt", workflow, iteration, attempt, entity: attempt };
          }
          if (attempt.plan && matchesEntity(attempt.plan, selectedPath)) {
            return { kind: "plan", workflow, iteration, attempt, plan: attempt.plan, entity: attempt.plan };
          }
          for (const workItem of attempt.workItems) {
            if (matchesEntity(workItem, selectedPath)) {
              return {
                kind: "workItem",
                workflow,
                iteration,
                attempt,
                workItem,
                entity: workItem,
                isReadyWorkItem: attempt.readyWorkItems().some(candidate => candidate.id === workItem.id),
              };
            }
          }
        }
      }

      return { kind: "none", workflow };
    }

    sampleWorkerOutcome(workItem, isSuccess) {
      if (isSuccess) {
        return {
          workerSummary: `${workItem.id} completed its assigned projection step.`,
          workerOutcome: `${workItem.id} produced the requested context projection artifact from the latest aggregate.`,
        };
      }

      return {
        workerSummary: `${workItem.id} found a blocking projection issue.`,
        workerOutcome: `${workItem.id} failed, so the attempt closes as failure and the iteration retry policy is evaluated.`,
      };
    }

    nextSelectionAfterWorkerResult(result, item, workflow) {
      if (result.kind === "workflow_deferred") {
        const nextAttempt = result.iteration?.attempts[0];
        return nextAttempt?.plan?.briefPath() || result.iteration?.specPath() || item.specPath();
      }

      const retryAttempt = result.iterationResult?.attempt;
      if (retryAttempt?.plan) {
        return retryAttempt.plan.briefPath();
      }

      if (result.kind === "workflow_running") {
        const found = this.findWorkItem(workflow, item.id);
        const nextReady = found?.attempt.readyWorkItems()[0];
        const nextRunning = found?.attempt.workItems.find(candidate => (
          candidate.status === RunStatus.Running && candidate.id !== item.id
        ));
        return nextReady?.briefPath() || nextRunning?.briefPath() || item.specPath();
      }

      if (result.kind === "workflow_success" || result.kind === "workflow_failed") {
        return workflow.specPath();
      }

      return item.specPath();
    }

    findWorkItem(workflow, workItemId) {
      for (const iteration of workflow.iterations) {
        for (const attempt of iteration.attempts) {
          const item = attempt.workItems.find(candidate => candidate.id === workItemId);
          if (item) return { iteration, attempt, item };
        }
      }
      return undefined;
    }

    renderHeader(workflow) {
      this.versionPillEl.textContent = `version ${this.store.currentVersion()}`;
      if (!workflow) {
        this.statusStripEl.innerHTML = '<span class="pill">no workflow</span>';
        return;
      }

      const counts = countEntities(workflow);
      this.statusStripEl.innerHTML = [
        statusPill(workflow.status),
        `<span class="pill">${counts.iterations} iterations</span>`,
        `<span class="pill">${counts.attempts} attempts</span>`,
        `<span class="pill">${counts.plans} plans</span>`,
        `<span class="pill">${counts.workItems} work_items</span>`,
      ].join("");
    }

    renderEvents() {
      const events = this.store.events();
      this.eventCountEl.textContent = `${events.length} events`;
      this.dbLogEl.innerHTML = events.map(event => (
        `<div class="log-entry">v${event.version}: ${escapeHtml(event.event)}</div>`
      )).join("");
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

  function statusPill(status) {
    return `<span class="pill ${statusClass(status)}">${status}</span>`;
  }

  function statusClass(status) {
    if (status === RunStatus.Running) return "running";
    if (status === RunStatus.Success) return "success";
    if (status === RunStatus.Failed) return "failed";
    return "not-started";
  }

  function matchesEntity(entity, path) {
    return path === entity.specPath() || path === entity.briefPath();
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, char => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[char]);
  }

  function eventWithLaunches(event, launched) {
    if (!launched.length) return event;
    return `${event}; scheduler: ${launched.join(", ")}`;
  }

  window.WorkflowContextOop.WorkflowApp = WorkflowApp;
}
