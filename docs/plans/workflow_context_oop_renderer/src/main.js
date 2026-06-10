import { WorkflowFactory } from "./domain/WorkflowFactory.js";
import { InMemoryWorkflowStore } from "./store/InMemoryWorkflowStore.js";
import { WorkflowProjector } from "./projection/WorkflowProjector.js";
import { LifecycleControls } from "./ui/LifecycleControls.js";
import { FileTreeView } from "./ui/FileTreeView.js";
import { FileViewer } from "./ui/FileViewer.js";
import { WorkflowApp } from "./ui/WorkflowApp.js";

const elements = {
  statusStrip: document.getElementById("statusStrip"),
  versionPill: document.getElementById("versionPill"),
  workflowGoal: document.getElementById("workflowGoal"),
  delegateWorkflow: document.getElementById("delegateWorkflow"),
  seedDemo: document.getElementById("seedDemo"),
  resetEmpty: document.getElementById("resetEmpty"),
  launchPlanner: document.getElementById("launchPlanner"),
  launchWorker: document.getElementById("launchWorker"),
  flowError: document.getElementById("flowError"),
  activeAttemptPill: document.getElementById("activeAttemptPill"),
  planSpec: document.getElementById("planSpec"),
  plannerSummary: document.getElementById("plannerSummary"),
  deferredGoal: document.getElementById("deferredGoal"),
  workItemsJson: document.getElementById("workItemsJson"),
  submitPlan: document.getElementById("submitPlan"),
  activeWorkerPill: document.getElementById("activeWorkerPill"),
  workerSelect: document.getElementById("workerSelect"),
  workerSummary: document.getElementById("workerSummary"),
  workerOutcome: document.getElementById("workerOutcome"),
  submitWorkerSuccess: document.getElementById("submitWorkerSuccess"),
  submitWorkerFailure: document.getElementById("submitWorkerFailure"),
  eventCount: document.getElementById("eventCount"),
  dbLog: document.getElementById("dbLog"),
};

const factory = new WorkflowFactory();
const store = new InMemoryWorkflowStore(factory);
const projector = new WorkflowProjector();
const controls = new LifecycleControls(elements);
const fileTree = new FileTreeView(document.getElementById("fileTree"));
const fileViewer = new FileViewer({
  titleEl: document.getElementById("selectedFileTitle"),
  contentEl: document.getElementById("fileView"),
});

const app = new WorkflowApp({ factory, store, projector, controls, fileTree, fileViewer });
app.start();
