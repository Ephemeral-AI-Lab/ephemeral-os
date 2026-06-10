{
  const {
    WorkflowFactory,
    InMemoryWorkflowStore,
    WorkflowProjector,
    AttemptAgentLaunchScheduler,
    AttemptOrchestrator,
    IterationOrchestrator,
    WorkflowOrchestrator,
    FileTreeView,
    FileViewer,
    LifecycleActionsView,
    WorkflowApp,
  } = window.WorkflowContextOop;

  const factory = new WorkflowFactory();
  const store = new InMemoryWorkflowStore(factory);
  const projector = new WorkflowProjector();
  const launchScheduler = new AttemptAgentLaunchScheduler();
  const attemptOrchestrator = new AttemptOrchestrator(factory, launchScheduler);
  launchScheduler.bindAttemptOrchestrator(attemptOrchestrator);
  const iterationOrchestrator = new IterationOrchestrator(attemptOrchestrator);
  const workflowOrchestrator = new WorkflowOrchestrator(factory, iterationOrchestrator);
  const fileTree = new FileTreeView(document.getElementById("fileTree"));
  const fileViewer = new FileViewer({
    titleEl: document.getElementById("selectedFileTitle"),
    contentEl: document.getElementById("fileView"),
  });
  const actionsView = new LifecycleActionsView({
    workflowGoalEl: document.getElementById("workflowGoal"),
    delegateWorkflowEl: document.getElementById("delegateWorkflow"),
    selectedEntityPillEl: document.getElementById("selectedEntityPill"),
    actionsEl: document.getElementById("selectedEntityActions"),
    errorEl: document.getElementById("flowError"),
  });

  const app = new WorkflowApp({
    factory,
    store,
    projector,
    workflowOrchestrator,
    attemptOrchestrator,
    launchScheduler,
    fileTree,
    fileViewer,
    actionsView,
    statusStripEl: document.getElementById("statusStrip"),
    versionPillEl: document.getElementById("versionPill"),
    eventCountEl: document.getElementById("eventCount"),
    dbLogEl: document.getElementById("dbLog"),
  });
  app.start();
}
