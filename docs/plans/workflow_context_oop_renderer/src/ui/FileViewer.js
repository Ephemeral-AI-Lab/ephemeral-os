{
  class FileViewer {
    constructor({ titleEl, contentEl }) {
      this.titleEl = titleEl;
      this.contentEl = contentEl;
    }

    render(file) {
      this.titleEl.textContent = file ? file.path : "No file selected";
      this.contentEl.textContent = file ? file.content : "";
    }
  }

  window.WorkflowContextOop.FileViewer = FileViewer;
}
