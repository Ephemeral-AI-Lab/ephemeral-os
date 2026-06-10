{
  function defaultGoal() {
    return "Implement DB-backed workflow context projections where every entity renders deterministic spec.md and brief.md files from the latest workflow aggregate.";
  }

  function samplePlanSpec() {
    return [
      "Use the workflow aggregate loaded after the DB commit as the only rendering input.",
      "Render leaf plan and work item files first, then aggregate attempt, iteration, and workflow files.",
      "Keep brief.md compact and add references only for terminal success/failure briefs.",
    ].join("\n");
  }

  function samplePlannerSummary() {
    return "The planner split projection work into schema, renderer, writer, and verification tasks. The plan keeps DB state authoritative and treats files as replaceable projections.";
  }

  function sampleDeferredGoal() {
    return "Add context retrieval tools over projected workflow files with path and line-range reads.";
  }

  function sampleWorkItems() {
    return [
      {
        work_item_id: "work_item_schema",
        work_item_spec: "Define WorkflowEntityBase, status enum, entity IDs, folder paths, and denormalized parent references.",
        needs: [],
      },
      {
        work_item_id: "work_item_renderers",
        work_item_spec: "Implement renderSpec and renderBrief for workflow, iteration, attempt, plan, and work item entities.",
        needs: ["work_item_schema"],
      },
      {
        work_item_id: "work_item_projector",
        work_item_spec: "Write rendered files atomically to the context filesystem after each committed mutation.",
        needs: ["work_item_schema"],
      },
      {
        work_item_id: "work_item_verification",
        work_item_spec: "Add projection tests for pending state, leaf-only attempt briefs, references, and retry attempt creation.",
        needs: ["work_item_renderers", "work_item_projector"],
      },
    ];
  }

  Object.assign(window.WorkflowContextOop, {
    defaultGoal,
    samplePlanSpec,
    samplePlannerSummary,
    sampleDeferredGoal,
    sampleWorkItems,
  });
}
