import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { Compartment, EditorState } from "@codemirror/state";
import { EditorView, keymap, lineNumbers } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { PanelsTopLeft } from "lucide-react";
import {
  Alert,
  Box,
  Button,
  Center,
  Drawer,
  Flex,
  Group,
  Loader,
  Paper,
  Text,
} from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import {
  fileBlame,
  fileRead,
  fileReadToEnd,
  fileWrite,
  type BlameRange,
} from "@/api/files";
import { RpcError } from "@/api/rpc";
import { useErrorToast } from "@/components/ErrorToast";
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

type EditorMeta = {
  document: string;
  startLine: number;
  editable: boolean;
  blameKey: string;
  mode: Mode["kind"];
};

/**
 * The file surface retains one CodeMirror instance for the selected path.
 * Paging appends content in place and blame/editability use compartments, so
 * focus, viewport, selection, and history survive normal Files interactions.
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
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<Mode>({ kind: "view" });
  const [busy, setBusy] = useState(false);
  const [blame, setBlame] = useState<BlameRange[] | null>(null);
  const [blameDrawerOpen, setBlameDrawerOpen] = useState(false);
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const lineNumbersCompartment = useRef(new Compartment()).current;
  const editabilityCompartment = useRef(new Compartment()).current;
  const blameCompartment = useRef(new Compartment()).current;
  const editorMeta = useRef<EditorMeta | null>(null);
  const navigate = useNavigate();
  const { showError } = useErrorToast();
  const narrow = useMediaQuery("(max-width: 47.99em)");

  const load = useCallback(async () => {
    setLoading(true);
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
    } finally {
      setLoading(false);
    }
  }, [sandboxId, path, session]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setBlame(null);
    if (!blameOn || session !== null) return;
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

    const editable = mode.kind === "editing";
    const blameKey = blameOn && blame && session === null ? JSON.stringify(blame) : "";
    const lineNumberExtension = lineNumbers({
      formatNumber: (line) => String(line + loaded.startLine - 1),
    });
    const editabilityExtension = [
      EditorState.readOnly.of(!editable),
      EditorView.editable.of(editable),
    ];
    const blameExtension = blameKey ? blameGutter(blame!, loaded.startLine, onOwnerClick) : [];
    const view = viewRef.current;

    if (!view) {
      const initialDocument = mode.kind === "view" ? loaded.content : mode.draft;
      const state = EditorState.create({
        doc: initialDocument,
        extensions: [
          history(),
          keymap.of([...defaultKeymap, ...historyKeymap]),
          lineNumbersCompartment.of(lineNumberExtension),
          editabilityCompartment.of(editabilityExtension),
          blameCompartment.of(blameExtension),
          EditorView.lineWrapping,
          EditorView.contentAttributes.of({ "aria-label": `File contents for ${path}` }),
          EditorView.theme({
            "&": { height: "100%", fontSize: "12px", backgroundColor: "var(--mantine-color-body)" },
            ".cm-gutters": {
              backgroundColor: "var(--mantine-color-default-hover)",
              color: "var(--mantine-color-dimmed)",
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
        ],
      });
      viewRef.current = new EditorView({ state, parent: host });
      editorMeta.current = {
        document: initialDocument,
        startLine: loaded.startLine,
        editable,
        blameKey,
        mode: mode.kind,
      };
      return;
    }

    const previous = editorMeta.current;
    const currentDocument = view.state.doc.toString();
    const retainDraft =
      (mode.kind === "editing" || mode.kind === "conflict") &&
      (previous?.mode === "editing" || previous?.mode === "conflict");
    const nextDocument = retainDraft
      ? currentDocument
      : mode.kind === "view"
        ? loaded.content
        : mode.draft;
    const effects = [];
    if (previous?.startLine !== loaded.startLine) {
      effects.push(lineNumbersCompartment.reconfigure(lineNumberExtension));
    }
    if (previous?.editable !== editable) {
      effects.push(editabilityCompartment.reconfigure(editabilityExtension));
    }
    if (previous?.blameKey !== blameKey) {
      effects.push(blameCompartment.reconfigure(blameExtension));
    }

    if (currentDocument !== nextDocument) {
      const changes = nextDocument.startsWith(currentDocument)
        ? { from: currentDocument.length, insert: nextDocument.slice(currentDocument.length) }
        : { from: 0, to: currentDocument.length, insert: nextDocument };
      view.dispatch({ changes, effects });
    } else if (effects.length > 0) {
      view.dispatch({ effects });
    }
    editorMeta.current = {
      document: nextDocument,
      startLine: loaded.startLine,
      editable,
      blameKey,
      mode: mode.kind,
    };
  }, [loaded, mode, blame, blameOn, session, onOwnerClick, lineNumbersCompartment, editabilityCompartment, blameCompartment]);

  useEffect(
    () => () => {
      viewRef.current?.destroy();
      viewRef.current = null;
      editorMeta.current = null;
    },
    [],
  );

  const loadMore = async () => {
    if (!loaded || loaded.nextOffset === null) return;
    setBusy(true);
    try {
      const window = await fileRead(sandboxId, path, session, loaded.nextOffset, WINDOW_LINES);
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
        showError(new Error(`file is ${whole.totalBytes} bytes — over the 1 MiB edit threshold, opening read-only`));
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
      setMode({ kind: "editing", original: whole.content, draft: whole.content });
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
    setMode({ kind: "editing", original: mode.server, draft: mode.draft });
  };

  const copyLocalDraft = async () => {
    if (mode.kind !== "conflict") return;
    try {
      await navigator.clipboard?.writeText(mode.draft);
    } catch (error) {
      showError(error);
    }
  };

  if (!loaded) {
    return (
      <Center style={{ flex: 1, minHeight: 0 }}>
        {loadError ? <Alert color="red" title="File unavailable">{loadError}</Alert> : <Loader aria-label="Loading file" size="sm" />}
      </Center>
    );
  }

  if (loaded.binary) {
    return (
      <Center p="xl" style={{ flex: 1, minHeight: 0 }}>
        <Paper maw={480} p="xl" ta="center" withBorder>
          <Text size="sm">Binary (non-UTF-8) file</Text>
          <Text c="dimmed" mt="xs" size="xs">File operations are UTF-8-text-only in v0.</Text>
        </Paper>
      </Center>
    );
  }

  const blameLegend = blameOn && blame && session === null ? ownersOf(blame) : null;
  const blameControls = blameLegend ? <BlameLegend owners={blameLegend} onOwnerClick={onOwnerClick} /> : null;

  return (
    <Flex direction="column" mih={0} style={{ flex: 1 }}>
      <Paper component="section" data-file-view-toolbar px="md" py="sm" radius={0} withBorder>
        <Group justify="space-between" wrap="wrap">
          <Text ff="monospace" size="xs" style={{ minWidth: 0 }} truncate>
            {path} · lines {loaded.startLine}–{loaded.endLine} of {loaded.totalLines}
            {loaded.nextOffset !== null ? " (truncated)" : ""}
          </Text>
          <Group gap="xs">
            {loaded.nextOffset !== null && mode.kind === "view" ? (
              <Button disabled={busy} onClick={() => void loadMore()}>Load next {WINDOW_LINES}</Button>
            ) : null}
            {mode.kind === "view" ? (
              <Button disabled={busy} onClick={() => void beginEdit()}>Edit</Button>
            ) : null}
            {mode.kind === "editing" ? (
              <>
                <Button disabled={busy} onClick={() => void load()} variant="default">Cancel</Button>
                <Button disabled={busy} onClick={() => void save()} variant="filled">{busy ? "Saving…" : "Save"}</Button>
              </>
            ) : null}
            {narrow && blameControls ? (
              <Button leftSection={<PanelsTopLeft size={13} />} onClick={() => setBlameDrawerOpen(true)} variant="default">
                Blame legend
              </Button>
            ) : null}
          </Group>
        </Group>
      </Paper>

      {loadError ? <Alert color="red" m="sm" title="File reload failed">{loadError}</Alert> : null}
      {mode.kind === "conflict" ? (
        <Alert color="yellow" title="Concurrent file change" variant="light">
          <Group gap="sm" justify="space-between" wrap="wrap">
            <Text size="xs">Local draft preserved. The file changed while you were editing, so saving would overwrite another change.</Text>
            <Group gap="xs">
              <Button onClick={keepEditingLocalDraft}>Keep editing local draft</Button>
              <Button onClick={() => void copyLocalDraft()} variant="default">Copy local draft</Button>
              <Button onClick={() => void load()} variant="default">Reload server version</Button>
            </Group>
          </Group>
        </Alert>
      ) : null}

      {!narrow && blameControls ? (
        <Paper component="section" px="md" py="xs" radius={0} withBorder>
          {blameControls}
        </Paper>
      ) : null}
      {narrow && blameControls ? (
        <Drawer
          onClose={() => setBlameDrawerOpen(false)}
          opened={blameDrawerOpen}
          position="right"
          size="20rem"
          title="Blame legend"
        >
          {blameControls}
        </Drawer>
      ) : null}

      <Box pos="relative" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
        <Box ref={hostRef} h="100%" />
        {loading ? (
          <Center inset={0} pos="absolute" style={{ background: "rgb(255 253 251 / 0.72)" }}>
            <Loader aria-label="Loading file" size="sm" />
          </Center>
        ) : null}
      </Box>
    </Flex>
  );
}

function BlameLegend({ owners, onOwnerClick }: { owners: string[]; onOwnerClick: (owner: string) => void }) {
  return (
    <Group gap="xs" wrap="wrap">
      <Text c="dimmed" size="xs">Blame</Text>
      {owners.map((owner) => {
        const info = ownerInfo(owner);
        return (
          <Button
            key={owner}
            leftSection={<Box h={10} style={{ backgroundColor: info.color, borderRadius: 2 }} w={10} />}
            onClick={() => onOwnerClick(owner)}
            title={owner}
            variant="subtle"
          >
            <Text ff="monospace" maw={220} size="xs" truncate>{owner}</Text>
          </Button>
        );
      })}
    </Group>
  );
}
