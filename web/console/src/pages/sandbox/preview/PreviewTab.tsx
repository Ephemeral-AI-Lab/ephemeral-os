import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { ExternalLink, RotateCw } from "lucide-react";
import { Button, Input, Select } from "@mantine/core";
import { useSandbox } from "@/pages/sandbox/SandboxContext";
import { previewScopes } from "@/pages/sandbox/SandboxHeader";

/**
 * The embedded web viewer: an iframe over the console's `/s/:id/...`
 * preview proxy — purely client-side, no new server surface. Scope, port,
 * and path live in the query string so the view survives refresh and every
 * PortPreview launcher can deep-link here.
 */
export function PreviewTab() {
  const { sandboxId, snapshot } = useSandbox();
  const [searchParams, setSearchParams] = useSearchParams();
  const scope = searchParams.get("scope") ?? "shared";
  const port = searchParams.get("port") ?? "";
  const path = searchParams.get("path") ?? "/";
  const [pathDraft, setPathDraft] = useState(path);
  const [portDraft, setPortDraft] = useState(port);
  const [reloadKey, setReloadKey] = useState(0);
  const [blocked, setBlocked] = useState<string | null>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => setPathDraft(path), [path]);
  useEffect(() => setPortDraft(port), [port]);

  const scopes = previewScopes(snapshot ?? undefined);
  const scopeEntry = scopes.find((entry) => entry.id === scope);
  const isolated = scope !== "shared";

  const portValid = /^\d+$/.test(port) && Number(port) >= 1 && Number(port) <= 65535;
  const previewUrl = useMemo(() => {
    if (!portValid) return null;
    const scopeSegment = scope === "shared" ? "shared" : `isolated=${scope}`;
    const cleanPath = path.startsWith("/") ? path.slice(1) : path;
    return `/s/${encodeURIComponent(sandboxId)}/${scopeSegment}/${port}/${cleanPath}`;
  }, [sandboxId, scope, port, path, portValid]);

  const apply = (next: { scope?: string; port?: string; path?: string }) => {
    const params = new URLSearchParams(searchParams);
    if (next.scope !== undefined) params.set("scope", next.scope);
    if (next.port !== undefined) params.set("port", next.port);
    if (next.path !== undefined) params.set("path", next.path);
    setSearchParams(params, { replace: true });
  };

  useEffect(() => {
    setBlocked(null);
    if (!previewUrl) return;
    const controller = new AbortController();
    void fetch(previewUrl, { signal: controller.signal })
      .then((response) => {
        const frameOptions = response.headers.get("x-frame-options") ?? "";
        const csp = response.headers.get("content-security-policy") ?? "";
        if (/deny/i.test(frameOptions)) {
          setBlocked("the app sends X-Frame-Options: DENY");
        } else if (/frame-ancestors[^;]*'none'/i.test(csp)) {
          setBlocked("the app sends CSP frame-ancestors 'none'");
        }
        controller.abort();
      })
      .catch(() => {});
    return () => controller.abort();
  }, [previewUrl, reloadKey]);

  const syncFromIframe = () => {
    const frame = iframeRef.current;
    if (!frame) return;
    try {
      const current = frame.contentWindow?.location.pathname;
      if (!current) return;
      const prefix = `/s/${encodeURIComponent(sandboxId)}/${scope === "shared" ? "shared" : `isolated=${scope}`}/${port}`;
      if (current.startsWith(prefix)) {
        const inner = current.slice(prefix.length) || "/";
        setPathDraft(inner + (frame.contentWindow?.location.search ?? ""));
      }
    } catch {
      setBlocked("the app refuses to render inside a frame");
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface px-3 py-2">
        <label className="text-[11px] text-ink-faint">scope</label>
        <Select
          className="w-52"
          value={scope}
          onChange={(value) => apply({ scope: value ?? "shared" })}
          data={[
            ...scopes.map((entry) => ({ value: entry.id, label: entry.label })),
            ...(scopeEntry || scope === "shared" ? [] : [{ value: scope, label: `isolated · ${scope}` }]),
          ]}
        />
        <label className="text-[11px] text-ink-faint" htmlFor="preview-port">
          port
        </label>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            apply({ port: portDraft.trim() });
          }}
        >
          <Input
            id="preview-port"
            value={portDraft}
            onChange={(event) => setPortDraft(event.target.value)}
            placeholder="5173"
            className="w-20 font-mono"
            inputMode="numeric"
          />
        </form>
        <label className="text-[11px] text-ink-faint" htmlFor="preview-path">
          path
        </label>
        <form
          className="min-w-0 flex-1"
          onSubmit={(event) => {
            event.preventDefault();
            apply({ path: pathDraft.trim() === "" ? "/" : pathDraft.trim() });
          }}
        >
          <Input
            id="preview-path"
            value={pathDraft}
            onChange={(event) => setPathDraft(event.target.value)}
            placeholder="/"
            className="w-full font-mono"
          />
        </form>
        <Button
          size="compact-xs"
          onClick={() => setReloadKey((key) => key + 1)}
          disabled={!previewUrl}
          title="refresh"
        >
          <RotateCw size={12} />
        </Button>
        <Button
          size="compact-xs"
          onClick={() => {
            if (previewUrl) window.open(previewUrl, "_blank", "noopener");
          }}
          disabled={!previewUrl}
          title="open in a new tab"
        >
          <ExternalLink size={12} />
          tab
        </Button>
      </div>

      {isolated ? (
        <div className="border-b border-warn/40 bg-warn-soft px-3 py-1 text-[11px] text-ink">
          Isolated session: the server must bind <span className="font-mono">0.0.0.0</span>{" "}
          or the workspace IP — <span className="font-mono">127.0.0.1</span> is not
          reachable from outside the namespace.
        </div>
      ) : null}

      {blocked ? (
        <div className="flex items-center gap-3 border-b border-danger/40 bg-danger-soft px-3 py-1.5 text-[11px] text-ink">
          Embedding blocked: {blocked}.
          <Button
            size="compact-xs"
            onClick={() => {
              if (previewUrl) window.open(previewUrl, "_blank", "noopener");
            }}
          >
            <ExternalLink size={11} />
            open in a new tab instead
          </Button>
        </div>
      ) : null}

      <div className="min-h-0 flex-1 bg-app">
        {previewUrl ? (
          <iframe
            key={`${previewUrl}-${reloadKey}`}
            ref={iframeRef}
            src={previewUrl}
            onLoad={syncFromIframe}
            title={`preview of ${sandboxId} port ${port}`}
            className="h-full w-full border-0 bg-white"
          />
        ) : (
          <div className="mx-auto mt-16 max-w-md rounded-lg border border-line bg-surface p-8 text-center">
            <div className="text-sm font-semibold">Pick a port to preview</div>
            <p className="mt-2 text-xs text-ink-mid">
              Anything serving HTTP inside the sandbox renders here through{" "}
              <span className="font-mono">/s/{sandboxId}/…</span> — start a
              server in the Terminal tab, then enter its port above.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
