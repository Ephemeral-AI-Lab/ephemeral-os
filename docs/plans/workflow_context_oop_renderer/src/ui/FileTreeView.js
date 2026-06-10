{
  const { statusClass } = window.WorkflowContextOop;

  class FileTreeView {
    constructor(rootEl) {
      this.rootEl = rootEl;
      this.onSelectPath = () => {};
    }

    onSelect(callback) {
      this.onSelectPath = callback;
    }

    render(workflow, files, selectedPath) {
      if (!workflow) {
        this.rootEl.innerHTML = '<div class="note">No workflow delegated.</div>';
        return;
      }

      const fileByPath = new Map(files.map(file => [file.path, file]));
      this.rootEl.innerHTML = this.renderFolder(workflow.id, workflow, fileByPath, [
        ...workflow.iterations.map(iteration => this.renderFolder(iteration.id, iteration, fileByPath, [
          ...iteration.attempts.map(attempt => this.renderFolder(attempt.id, attempt, fileByPath, [
            attempt.plan ? this.renderFolder(attempt.plan.id, attempt.plan, fileByPath, [], selectedPath) : "",
            ...attempt.workItems.map(item => this.renderFolder(item.id, item, fileByPath, [], selectedPath)),
          ], selectedPath)),
        ], selectedPath)),
      ], selectedPath);

      this.rootEl.querySelectorAll("[data-path]").forEach(link => {
        link.addEventListener("click", event => {
          event.preventDefault();
          this.onSelectPath(link.getAttribute("data-path"));
        });
      });
    }

    renderFolder(name, entity, fileByPath, children, selectedPath) {
      return `
        <div class="fs-folder">
          <div class="fs-folder-label">
            <span class="fs-folder-name">${escapeHtml(name)}/</span>
            ${pill(entity.status, statusClass(entity.status))}
          </div>
          <div class="fs-children">
            ${this.renderFileLink(entity, "spec", fileByPath, selectedPath)}
            ${this.renderFileLink(entity, "brief", fileByPath, selectedPath)}
            ${children.filter(Boolean).join("")}
          </div>
        </div>
      `;
    }

    renderFileLink(entity, kind, fileByPath, selectedPath) {
      const path = `${entity.folderPath}/${kind}.md`;
      const file = fileByPath.get(path);
      if (!file) return "";
      return `
        <a href="#fileView" class="fs-file ${path === selectedPath ? "selected" : ""}" data-path="${escapeAttr(path)}">
          <span>${escapeHtml(kind)}.md</span>
          <span class="fs-file-kind">${escapeHtml(file.entityKind)}</span>
        </a>
      `;
    }
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

  window.WorkflowContextOop.FileTreeView = FileTreeView;
}
