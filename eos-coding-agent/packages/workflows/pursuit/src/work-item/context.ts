import type { WorkItemState } from "./state.js";

/** One field, one file; absent field, absent path. Content is verbatim. */
export interface EntityFieldFile {
  readonly name: string;
  readonly content: string;
}

export function workItemFieldFiles(item: WorkItemState): EntityFieldFile[] {
  const files: EntityFieldFile[] = [
    { name: "title.md", content: item.title },
    { name: "spec.md", content: item.spec },
  ];
  if (item.summary !== null) files.push({ name: "summary.md", content: item.summary });
  if (item.outcome !== null) files.push({ name: "outcome.md", content: item.outcome });
  return files;
}
