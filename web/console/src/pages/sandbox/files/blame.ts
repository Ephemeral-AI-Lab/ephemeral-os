import { gutter, GutterMarker, type EditorView } from "@codemirror/view";
import type { BlameRange } from "@/api/files";

export interface BlameOwnerInfo {
  owner: string;
  color: string;
  hatched: boolean;
}

/**
 * Stable owner coloring: hash → hue for workspace sessions and operations,
 * neutral for `original`, hatched gray for `unknown`. The owner string stays
 * opaque beyond the prefix split.
 */
export function ownerInfo(owner: string): BlameOwnerInfo {
  if (owner === "original") {
    return { owner, color: "#c8cdd6", hatched: false };
  }
  if (owner === "unknown") {
    return { owner, color: "#9aa1ac", hatched: true };
  }
  const hue = hashHue(owner);
  return { owner, color: `hsl(${hue} 65% 55%)`, hatched: false };
}

function hashHue(text: string): number {
  let hash = 0;
  for (let index = 0; index < text.length; index += 1) {
    hash = (hash * 31 + text.charCodeAt(index)) | 0;
  }
  return Math.abs(hash) % 360;
}

export function ownersOf(ranges: BlameRange[]): string[] {
  return [...new Set(ranges.map((range) => range.owner))];
}

class BlameMarker extends GutterMarker {
  constructor(private readonly info: BlameOwnerInfo) {
    super();
  }

  override toDOM(): Node {
    const element = document.createElement("span");
    element.className = "cm-blame-chip";
    element.title = this.info.owner;
    element.style.backgroundColor = this.info.color;
    if (this.info.hatched) {
      element.style.backgroundImage =
        "repeating-linear-gradient(45deg, transparent 0 2px, #ffffff88 2px 4px)";
    }
    return element;
  }

  override eq(other: GutterMarker): boolean {
    return other instanceof BlameMarker && other.info.owner === this.info.owner;
  }
}

/**
 * The BlameGutter: CodeMirror's gutter API rendering one owner chip per
 * line, resolved from the absolute blame ranges. `windowStart` maps the
 * viewer's 1-indexed window offset onto absolute file lines. Clicking a
 * chip reports the owner for cross-tab navigation.
 */
export function blameGutter(
  ranges: BlameRange[],
  windowStart: number,
  onOwnerClick: (owner: string) => void,
) {
  const ownerAt = (absoluteLine: number): string | null => {
    for (const range of ranges) {
      if (
        absoluteLine >= range.start_line &&
        absoluteLine < range.start_line + range.line_count
      ) {
        return range.owner;
      }
    }
    return null;
  };

  return gutter({
    class: "cm-blame-gutter",
    lineMarker(view: EditorView, line) {
      const docLine = view.state.doc.lineAt(line.from).number;
      const owner = ownerAt(windowStart + docLine - 1);
      return owner ? new BlameMarker(ownerInfo(owner)) : null;
    },
    domEventHandlers: {
      click(view, line) {
        const docLine = view.state.doc.lineAt(line.from).number;
        const owner = ownerAt(windowStart + docLine - 1);
        if (owner) onOwnerClick(owner);
        return owner !== null;
      },
    },
  });
}
