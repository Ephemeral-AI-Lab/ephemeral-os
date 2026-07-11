import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { ExternalLink, RotateCw } from "lucide-react";
import { Box, Button, Flex, Group, Input, Loader, Paper, Select, Text } from "@mantine/core";
import { useSandbox } from "@/pages/sandbox/SandboxContext";
import { previewScopes } from "@/pages/sandbox/SandboxHeader";

/**
 * The embedded web viewer: an iframe over the console's `/s/:id/...`
 * preview proxy. The frame is deliberately opaque to the Console: scope,
 * port, and initial path live in the query string so launchers can deep-link
 * without reading or synchronizing in-frame navigation.
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
  const [isLoading, setIsLoading] = useState(false);

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

  useEffect(() => {
    setIsLoading(Boolean(previewUrl));
  }, [previewUrl, reloadKey]);

  const apply = (next: { scope?: string; port?: string; path?: string }) => {
    const params = new URLSearchParams(searchParams);
    if (next.scope !== undefined) params.set("scope", next.scope);
    if (next.port !== undefined) params.set("port", next.port);
    if (next.path !== undefined) params.set("path", next.path);
    setSearchParams(params, { replace: true });
  };

  return (
    <Box style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <Flex align="center" gap="sm" wrap="wrap" px="md" py="sm" style={{ borderBottom: "1px solid var(--mantine-color-neutral-3)", background: "var(--mantine-color-white)" }}>
        <Text component="label" fz={11} c="dimmed">scope</Text>
        <Select
          w={208}
          value={scope}
          onChange={(value) => apply({ scope: value ?? "shared" })}
          data={[
            ...scopes.map((entry) => ({ value: entry.id, label: entry.label })),
            ...(scopeEntry || scope === "shared" ? [] : [{ value: scope, label: `isolated · ${scope}` }]),
          ]}
        />
        <Text component="label" htmlFor="preview-port" fz={11} c="dimmed">port</Text>
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
            w={80}
            styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }}
            inputMode="numeric"
          />
        </form>
        <Text component="label" htmlFor="preview-path" fz={11} c="dimmed">path</Text>
        <form
          style={{ flex: 1, minWidth: 0 }}
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
            w="100%"
            styles={{ input: { fontFamily: "var(--mantine-font-family-monospace)" } }}
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
            if (previewUrl) window.open(previewUrl, "_blank", "noopener,noreferrer");
          }}
          disabled={!previewUrl}
          title="open in a new tab"
        >
          <ExternalLink size={12} />
          tab
        </Button>
      </Flex>

      {isolated ? (
        <Box px="md" py={4} fz={11} style={{ borderBottom: "1px solid var(--mantine-color-warning-3)", background: "var(--mantine-color-warning-0)" }}>
          Isolated session: the server must bind <Text span ff="monospace">0.0.0.0</Text>{" "}
          or the workspace IP — <Text span ff="monospace">127.0.0.1</Text> is not
          reachable from outside the namespace.
        </Box>
      ) : null}

      <Box style={{ position: "relative", flex: 1, minHeight: 0, background: "var(--mantine-color-warm-0)" }} aria-busy={isLoading}>
        {previewUrl ? (
          <>
            {isLoading ? (
              <Group gap="sm" px="md" py={6} fz={11} c="dimmed" role="status" style={{ position: "absolute", top: 0, width: "100%", background: "var(--mantine-color-white)", zIndex: 1 }}>
                <Loader size="xs" />
                Loading preview…
              </Group>
            ) : null}
            <iframe
              key={`${previewUrl}-${reloadKey}`}
              src={previewUrl}
              onLoad={() => setIsLoading(false)}
              sandbox="allow-scripts"
              allow=""
              referrerPolicy="no-referrer"
              title={`preview of ${sandboxId} port ${port}`}
              style={{ width: "100%", height: "100%", border: 0, background: "var(--mantine-color-white)" }}
            />
          </>
        ) : (
          <Paper withBorder maw={448} mx="auto" mt={64} p="xl" ta="center">
            <Text size="sm" fw={600}>Pick a port to preview</Text>
            <Text mt="sm" size="xs" c="dimmed">
              Anything serving HTTP inside the sandbox renders here through{" "}
              <Text span ff="monospace">/s/{sandboxId}/…</Text> — start a
              server in the Terminal tab, then enter its port above.
            </Text>
          </Paper>
        )}
      </Box>
    </Box>
  );
}
