import {
  defaultGoal,
  sampleDeferredGoal,
  samplePlannerSummary,
  samplePlanSpec,
  sampleWorkItems,
} from "../sample/SampleData.js";

export class LifecycleControls {
  constructor(elements) {
    this.elements = elements;
  }

  bind(handlers) {
    this.elements.delegateWorkflow.addEventListener("click", () => handlers.delegateWorkflow());
    this.elements.seedDemo.addEventListener("click", () => handlers.seedDemo());
    this.elements.resetEmpty.addEventListener("click", () => handlers.resetEmpty());
    this.elements.launchPlanner.addEventListener("click", () => handlers.launchPlanner());
    this.elements.submitPlan.addEventListener("click", () => handlers.submitPlanOutcome());
    this.elements.launchWorker.addEventListener("click", () => handlers.launchNextWorker());
    this.elements.submitWorkerSuccess.addEventListener("click", () => handlers.submitWorkerOutcome(true));
    this.elements.submitWorkerFailure.addEventListener("click", () => handlers.submitWorkerOutcome(false));
    this.elements.workerSelect.addEventListener("change", () => handlers.selectWorker(this.elements.workerSelect.value));
  }

  setInitialValues() {
    this.elements.workflowGoal.value = defaultGoal();
    this.elements.planSpec.value = samplePlanSpec();
    this.elements.plannerSummary.value = samplePlannerSummary();
    this.elements.deferredGoal.value = sampleDeferredGoal();
    this.elements.workItemsJson.value = JSON.stringify(sampleWorkItems(), null, 2);
  }

  readWorkflowGoal() {
    return this.elements.workflowGoal.value.trim() || defaultGoal();
  }

  readPlannerOutcome() {
    return {
      planSpec: this.elements.planSpec.value.trim() || samplePlanSpec(),
      plannerSummary: this.elements.plannerSummary.value.trim() || samplePlannerSummary(),
      deferredGoalForNextIteration: this.elements.deferredGoal.value.trim(),
      workItems: JSON.parse(this.elements.workItemsJson.value),
    };
  }

  readWorkerOutcome() {
    return {
      workItemId: this.elements.workerSelect.value,
      workerSummary: this.elements.workerSummary.value.trim(),
      workerOutcome: this.elements.workerOutcome.value.trim(),
    };
  }

  renderHeader({ version, workflow, counts }) {
    const workflowStatus = workflow ? workflow.status : "NotStarted";
    this.elements.versionPill.textContent = `version ${version}`;
    this.elements.statusStrip.innerHTML = [
      pill(`workflow ${workflowStatus}`, statusClass(workflowStatus)),
      pill(`${counts.iterations} iterations`, "running"),
      pill(`${counts.attempts} attempts`, "running"),
      pill(`${counts.plans} plans`, "success"),
      pill(`${counts.workItems} work items`, "not-started"),
    ].join("");
  }

  renderForms(workflow, selectedWorkerId) {
    if (!workflow) {
      this.elements.activeAttemptPill.textContent = "none";
      this.elements.activeWorkerPill.textContent = "none";
      this.elements.workerSelect.innerHTML = "";
      return;
    }

    this.elements.workflowGoal.value = workflow.goal;
    const attempt = workflow.activeIteration()?.activeAttempt();
    this.elements.activeAttemptPill.textContent = attempt ? attempt.id : "none";
    this.elements.activeAttemptPill.className = `pill ${attempt ? statusClass(attempt.status) : ""}`;

    this.elements.planSpec.value = attempt?.plan?.planSpec || samplePlanSpec();
    this.elements.plannerSummary.value = attempt?.plan?.plannerSummary || samplePlannerSummary();
    this.elements.deferredGoal.value = attempt?.plan?.deferredGoalForNextIteration || sampleDeferredGoal();
    this.elements.workItemsJson.value = JSON.stringify(
      attempt?.workItems?.length
        ? attempt.workItems.map(item => ({
          work_item_id: item.id,
          work_item_spec: item.workItemSpec,
          needs: item.needs,
        }))
        : sampleWorkItems(),
      null,
      2,
    );

    const items = allWorkItems(workflow);
    this.elements.workerSelect.innerHTML = items.map(item => {
      return `<option value="${escapeAttr(item.id)}">${escapeHtml(item.id)} (${escapeHtml(item.status)})</option>`;
    }).join("");

    const selectedItem = items.find(item => item.id === selectedWorkerId)
      || items.find(item => item.status === "Running")
      || items.find(item => item.status === "NotStarted")
      || items[0];

    if (selectedItem) {
      this.elements.workerSelect.value = selectedItem.id;
      this.elements.activeWorkerPill.textContent = selectedItem.id;
      this.elements.activeWorkerPill.className = `pill ${statusClass(selectedItem.status)}`;
      this.elements.workerSummary.value = selectedItem.workerSummary || "Worker completed the assigned context projection work.";
      this.elements.workerOutcome.value = selectedItem.workerOutcome || "The worker produced the requested projection behavior and left the context files regenerable from DB state.";
    } else {
      this.elements.activeWorkerPill.textContent = "none";
      this.elements.workerSummary.value = "";
      this.elements.workerOutcome.value = "";
    }
  }

  renderEvents(events) {
    this.elements.eventCount.textContent = `${events.length} events`;
    this.elements.dbLog.innerHTML = events.map(event => `
      <div class="log-entry">v${escapeHtml(String(event.version))}: ${escapeHtml(event.event)}</div>
    `).join("") || '<div class="note">No events yet.</div>';
  }

  showError(message) {
    this.elements.flowError.textContent = message || "";
    this.elements.flowError.classList.toggle("show", Boolean(message));
  }
}

function allWorkItems(workflow) {
  return workflow.iterations.flatMap(iteration => iteration.attempts.flatMap(attempt => attempt.workItems));
}

function statusClass(status) {
  if (status === "Running") return "running";
  if (status === "Success") return "success";
  if (status === "Failed") return "failed";
  return "not-started";
}

function pill(text, klass) {
  return `<span class="pill ${klass || ""}">${escapeHtml(text)}</span>`;
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
