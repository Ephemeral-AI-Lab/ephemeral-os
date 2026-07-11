import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { EditorState } from "@codemirror/state";
import { EditorView, lineNumbers } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { keymap } from "@codemirror/view";
import { Loader2 } from "lucide-react";
import {
  fileBlame,
  fileRead,
  fileReadToEnd,
  fileWrite,
  type BlameRange,
} from "@/api/files";
import { RpcError } from "@/api/rpc";
import { useErrorToast } from "@/components/ErrorToast";
import { Button } from "@/components/ui/button";
import { blameGutter, ownerInfo, ownersOf } from "@/pages/sandbox/files/blame";

const WINDOW_LINES = 2000;
const EDIT_SIZE_LIMIT_BYTES = 1024 * 1024;

type Mode =
  | { kind: "view" }
  | { kind: "editing"; original: string; draft: string }
  | {
      kind: "conflict";
      draft: string;
      server: string;
      serverTotalLines: number;
      serverTotalBytes: number;
    };

interface LoadedText {
  content: string;
  startLine: number;
  endLine: number;
  totalLines: number;
  totalBytes: number;
  nextOffset: number | null;
  binary: boolean;
}

/**
 * The file surface: a windowed read-only viewer (2000-line windows, offset
 * paging, truncation indicator) with the BlameGutter in published scope, and
 * an edit mode that pages the whole file first, guards oversized files, and
 * refuses to save over a concurrent change.
 */
export function FileView({
  sandboxId,
  path,
  session,
  blameOn,
}: {
  sandboxId: string;
  path: string;
  session: string | null;
  blameOn: boolean;
}) {
  const [loaded, setLoaded] = useState<LoadedText | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>({ kind: "view" });
  const [busy, setBusy] = useState(false);
  const [blame, setBlame] = useState<BlameRange[] | null>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const navigate = useNavigate();
  const { showError } = useErrorToast();

  const load = useCallback(async () => {
    setLoaded(null);
    setLoadError(null);
    setMode({ kind: "view" });
    try {
      const window = await fileRead(sandboxId, path, session, 1, WINDOW_LINES);
      setLoaded({
        content: window.content,
        startLine: window.start_line,
        endLine: window.start_line + window.num_lines - 1,
        totalLines: window.total_lines,
        totalBytes: window.total_bytes,
        nextOffset: window.next_offset,
        binary: false,
      });
    } catch (error) {
      if (error instanceof RpcError && error.message.includes("UTF-8")) {
        setLoaded({
          content: "",
          startLine: 1,
          endLine: 0,
          totalLines: 0,
          totalBytes: 0,
          nextOffset: null,
          binary: true,
        });
        return;
      }
      setLoadError(error instanceof Error ? error.message : String(error));
    }
  }, [sandboxId, path, session]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setBlame(null);
    if (!blameOn || session !== null || path === "") return;
    let cancelled = false;
    fileBlame(sandboxId, path)
      .then((result) => {
        if (!cancelled) setBlame(result.ranges);
      })
      .catch((error) => showError(error));
    return () => {
      cancelled = true;
    };
  }, [blameOn, sandboxId, path, session, showError]);

  const onOwnerClick = useCallback(
    (owner: string) => {
      if (owner.startsWith("workspace_session:")) {
        void navigate(
          `/sandboxes/${encodeURIComponent(sandboxId)}/terminal?session=${encodeURIComponent(owner.slice("workspace_session:".length))}`,
        );
      } else if (owner.startsWith("operation:")) {
        void navigate(
          `/sandboxes/${encodeURIComponent(sandboxId)}/observability/traces/${encodeURIComponent(owner.slice("operation:".length))}`,
        );
      }
    },
    [navigate, sandboxId],
  );

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !loaded || loaded.binary) return;
    const editing = mode.kind === "editing";
    const extensions = [
      lineNumbers({
        formatNumber: (line) => String(line + loaded.startLine - 1),
      }),
      EditorView.lineWrapping,
      EditorView.theme({
        "&": { fontSize: "12px", backgroundColor: "var(--color-surface)" },
        ".cm-gutters": {
          backgroundColor: "var(--color-app)",
          color: "var(--color-ink-faint)",
          border: "none",
        },
        ".cm-blame-chip": {
          display: "inline-block",
          width: "10px",
          height: "12px",
          borderRadius: "2px",
          cursor: "pointer",
        },
        ".cm-blame-gutter": { width: "14px" },
      }),
    ];
    if (blameOn && blame && session === null && mode.kind === "view") {
      extensions.push(blameGutter(blame, loaded.startLine, onOwnerClick));
    }
    if (editing) {
      extensions.push(history(), keymap.of([...defaultKeymap, ...historyKeymap]));
    } else {
      extensions.push(EditorState.readOnly.of(true), EditorView.editable.of(false));
    }
    const state = EditorState.create({
      doc:
        mode.kind === "editing" || mode.kind === "conflict"
          ? mode.draft
          : loaded.content,
      extensions,
    });
    const view = new EditorView({ state, parent: host });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, [loaded, mode, blame, blameOn, session, onOwnerClick]);

  const loadMore = async () => {
    if (!loaded || loaded.nextOffset === null) return;
    setBusy(true);
    try {
      const window = await fileRead(
        sandboxId,
        path,
        session,
        loaded.nextOffset,
        WINDOW_LINES,
      );
      setLoaded({
        content: `${loaded.content}\n${window.content}`,
        startLine: loaded.startLine,
        endLine: window.start_line + window.num_lines - 1,
        totalLines: window.total_lines,
        totalBytes: window.total_bytes,
        nextOffset: window.next_offset,
        binary: false,
      });
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  };

  const beginEdit = async () => {
    setBusy(true);
    try {
      const whole = await fileReadToEnd(sandboxId, path, session);
      if (whole.totalBytes > EDIT_SIZE_LIMIT_BYTES) {
        showError(
          new Error(
            `file is ${whole.totalBytes} bytes — over the 1 MiB edit threshold, opening read-only`,
          ),
        );
        return;
      }
      setLoaded((current) =>
        current
          ? {
              ...current,
              content: whole.content,
              endLine: whole.totalLines,
              totalLines: whole.totalLines,
              totalBytes: whole.totalBytes,
              nextOffset: null,
            }
          : current,
      );
      setMode({
        kind: "editing",
        original: whole.content,
        draft: whole.content,
      });
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (mode.kind !== "editing") return;
    const edited = viewRef.current?.state.doc.toString() ?? "";
    setBusy(true);
    try {
      const current = await fileReadToEnd(sandboxId, path, session);
      if (current.content !== mode.original) {
        setMode({
          kind: "conflict",
          draft: edited,
          server: current.content,
          serverTotalLines: current.totalLines,
          serverTotalBytes: current.totalBytes,
        });
        return;
      }
      await fileWrite(sandboxId, path, edited, session);
      await load();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  };

  const keepEditingLocalDraft = () => {
    if (mode.kind !== "conflict") return;
    setLoaded((current) =>
      current
        ? {
            ...current,
            content: mode.server,
            endLine: mode.serverTotalLines,
            totalLines: mode.serverTotalLines,
            totalBytes: mode.serverTotalBytes,
            nextOffset: null,
          }
        : current,
    );
    setMode({
      kind: "editing",
      original: mode.server,
      draft: mode.draft,
    });
  };

  const copyLocalDraft = async () => {
    if (mode.kind !== "conflict") return;
    try {
      await navigator.clipboard?.writeText(mode.draft);
    } catch (error) {
      showError(error);
    }
  };

  if (loadError) {
    return (
      <div className="m-4 rounded border border-danger/40 bg-danger-soft p-3 text-xs text-ink">
        {loadError}
      </div>
    );
  }
  if (!loaded) {
    return (
      <div className="flex flex-1 items-center justify-center text-ink-faint">
        <Loader2 size={16} className="animate-spin" />
      </div>
    );
  }
  if (loaded.binary) {
    return (
      <div className="m-4 rounded border border-line bg-surface p-6 text-center text-xs text-ink-mid">
        Binary (non-UTF-8) file — the file operations are UTF-8-text-only in
        v0.
      </div>
    );
  }

  const blameLegend =
    blameOn && blame && session === null ? ownersOf(blame) : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface px-3 py-1.5">
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-ink-mid">
          {path} · lines {loaded.startLine}–{loaded.endLine} of {loaded.totalLines}
          {loaded.nextOffset !== null ? " (truncated)" : ""}
        </span>
        {loaded.nextOffset !== null && mode.kind === "view" ? (
          <Button size="sm" onClick={() => void loadMore()} disabled={busy}>
            load next {WINDOW_LINES}
          </Button>
        ) : null}
        {mode.kind === "view" ? (
          <Button size="sm" onClick={() => void beginEdit()} disabled={busy}>
            Edit
          </Button>
        ) : null}
        {mode.kind === "editing" ? (
          <>
            <Button size="sm" variant="ghost" onClick={() => void load()} disabled={busy}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={() => void save()} disabled={busy}>
              {busy ? "Saving…" : "Save"}
            </Button>
          </>
        ) : null}
      </div>

      {mode.kind === "conflict" ? (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-2 border-b border-warn/50 bg-warn-soft px-3 py-1.5 text-xs"
        >
          <span>
            Local draft preserved. The file changed while you were editing
            (agents share this workspace), so saving would overwrite their
            change.
          </span>
          <Button size="sm" onClick={keepEditingLocalDraft}>
            Keep editing local draft
          </Button>
          <Button size="sm" variant="ghost" onClick={() => void copyLocalDraft()}>
            Copy local draft
          </Button>
          <Button size="sm" variant="ghost" onClick={() => void load()}>
            Reload server version
          </Button>
        </div>
      ) : null}

      {blameLegend ? (
        <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface px-3 py-1 text-[11px]">
          <span className="text-ink-faint">blame:</span>
          {blameLegend.map((owner) => {
            const info = ownerInfo(owner);
            return (
              <button
                key={owner}
                type="button"
                onClick={() => onOwnerClick(owner)}
                className="flex items-center gap-1 rounded px-1 py-px hover:bg-surface-hover"
                title={owner}
              >
                <span
                  className="inline-block size-2.5 rounded-sm"
                  style={{ backgroundColor: info.color }}
                />
                <span className="max-w-56 truncate font-mono">{owner}</span>
              </button>
            );
          })}
        </div>
      ) : null}

      <div ref={hostRef} className="min-h-0 flex-1 overflow-auto" />
    </div>
  );
}
