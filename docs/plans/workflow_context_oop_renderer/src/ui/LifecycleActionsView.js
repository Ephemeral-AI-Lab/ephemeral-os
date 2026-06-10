{
  const { RunStatus } = window.WorkflowContextOop;

  class LifecycleActionsView {
    constructor({
      workflowGoalEl,
      delegateWorkflowEl,
      selectedEntityPillEl,
      actionsEl,
      errorEl,
    }) {
      this.workflowGoalEl = workflowGoalEl;
      this.delegateWorkflowEl = delegateWorkflowEl;
      this.selectedEntityPillEl = selectedEntityPillEl;
      this.actionsEl = actionsEl;
      this.errorEl = errorEl;
    }

    setWorkflowGoal(goal) {
      this.workflowGoalEl.value = goal;
    }

    readWorkflowGoal() {
      return this.workflowGoalEl.value.trim();
    }

    bind(handlers) {
      this.delegateWorkflowEl.addEventListener("click", () => handlers.delegateWorkflow());
      this.actionsEl.addEventListener("click", event => {
        const button = event.target.closest("[data-action]");
        if (!button) return;
        handlers.runAction(button.getAttribute("data-action"));
      });
    }

    showError(message) {
      this.errorEl.textContent = message;
      this.errorEl.classList.toggle("show", Boolean(message));
    }

    render(context) {
      this.selectedEntityPillEl.textContent = this.describeSelection(context);
      this.actionsEl.innerHTML = this.renderActions(context);
    }

    describeSelection(context) {
      if (!context?.workflow) return "no workflow";
      if (!context.kind || context.kind === "none") return "select plan/work_item";
      const entity = context[context.kind] || context.entity;
      return `${context.kind}: ${entity?.id || "unknown"}`;
    }

    renderActions(context) {
      if (!context?.workflow) {
        return this.note("delegate_workflow has not been called.");
      }

      if (context.kind === "plan") {
        return this.renderPlanActions(context);
      }

      if (context.kind === "workItem") {
        return this.renderWorkItemActions(context);
      }

      if (context.kind === "attempt") {
        return this.note(`attempt status: ${context.attempt.status}`);
      }

      if (context.kind === "iteration") {
        return this.note(`iteration status: ${context.iteration.status}, max_try: ${context.iteration.maxTry}`);
      }

      return this.note("Select a plan or work_item file to drive agent calls.");
    }

    renderPlanActions(context) {
      const plan = context.plan;
      if (plan.status === RunStatus.NotStarted) {
        return this.note("planner queued; scheduler will launch automatically");
      }
      if (plan.status === RunStatus.Running) {
        return this.buttons([
          ["submitPlannerOutcomeDeferred", "submit_planner_outcome(deferred)"],
          ["submitPlannerOutcomeFinal", "submit_planner_outcome(final)"],
        ]);
      }
      return this.note(`planner status: ${plan.status}`);
    }

    renderWorkItemActions(context) {
      const item = context.workItem;
      if (item.status === RunStatus.NotStarted && context.isReadyWorkItem) {
        return this.note("worker queued; scheduler will launch automatically");
      }
      if (item.status === RunStatus.NotStarted) {
        return this.note(`waiting for: ${item.needs.join(", ") || "planner"}`);
      }
      if (item.status === RunStatus.Running) {
        return this.buttons([
          ["submitWorkerSuccess", "submit_worker_outcome(success)"],
          ["submitWorkerFailure", "submit_worker_outcome(failure)", "danger"],
        ]);
      }
      return this.note(`worker status: ${item.status}`);
    }

    buttons(items) {
      return `<div class="action-buttons">${items.map(([action, label, klass = "primary"]) => (
        `<button type="button" class="${klass}" data-action="${escapeAttr(action)}">${escapeHtml(label)}</button>`
      )).join("")}</div>`;
    }

    note(message) {
      return `<div class="note">${escapeHtml(message)}</div>`;
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/'/g, "&#39;");
  }

  window.WorkflowContextOop.LifecycleActionsView = LifecycleActionsView;
}
