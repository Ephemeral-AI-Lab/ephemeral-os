export class WorkflowProjector {
  project(workflow) {
    if (!workflow) return [];

    const files = [
      this.file(workflow, "workflow", "spec", workflow.renderSpec()),
      this.file(workflow, "workflow", "brief", workflow.renderBrief()),
    ];

    workflow.iterations.forEach(iteration => {
      files.push(this.file(iteration, "iteration", "spec", iteration.renderSpec()));
      files.push(this.file(iteration, "iteration", "brief", iteration.renderBrief()));

      iteration.attempts.forEach(attempt => {
        files.push(this.file(attempt, "attempt", "spec", attempt.renderSpec()));
        files.push(this.file(attempt, "attempt", "brief", attempt.renderBrief()));

        if (attempt.plan) {
          files.push(this.file(attempt.plan, "plan", "spec", attempt.plan.renderSpec()));
          files.push(this.file(attempt.plan, "plan", "brief", attempt.plan.renderBrief()));
        }

        attempt.workItems.forEach(item => {
          files.push(this.file(item, "work_item", "spec", item.renderSpec()));
          files.push(this.file(item, "work_item", "brief", item.renderBrief()));
        });
      });
    });

    return files;
  }

  file(entity, entityKind, kind, content) {
    return {
      path: `${entity.folderPath}/${kind}.md`,
      entityKind,
      kind,
      status: entity.status,
      content,
    };
  }
}
