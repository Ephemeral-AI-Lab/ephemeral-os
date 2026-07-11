import { useState } from "react";
import { useSearchParams } from "react-router";
import { GitBranch, PanelLeft } from "lucide-react";
import {
  Box,
  Breadcrumbs,
  Button,
  Center,
  Drawer,
  Flex,
  Group,
  Paper,
  Select,
  Stack,
  Text,
  Tooltip,
} from "@mantine/core";
import { useMediaQuery } from "@mantine/hooks";
import { useSandbox } from "@/pages/sandbox/SandboxContext";
import { FileTree } from "@/pages/sandbox/files/FileTree";
import { FileView } from "@/pages/sandbox/files/FileView";

const PUBLISHED = "__published__";

/**
 * Files keeps the editor and navigation state in the URL. On narrow screens
 * the same navigator moves into a Mantine Drawer, preserving one scroll owner
 * for the editor pane and a focus-restoring entry point for the tree.
 */
export function FilesTab() {
  const { sandboxId, snapshot } = useSandbox();
  const [searchParams, setSearchParams] = useSearchParams();
  const [navigatorOpen, setNavigatorOpen] = useState(false);
  const narrow = useMediaQuery("(max-width: 47.99em)");
  const path = searchParams.get("path") ?? "";
  const session = searchParams.get("session");
  const blameOn = searchParams.get("blame") === "1";
  const workspaces = snapshot?.sandboxes[0]?.workspaces ?? [];

  const apply = (next: { path?: string | null; session?: string | null; blame?: boolean }) => {
    const params = new URLSearchParams(searchParams);
    if (next.path !== undefined) {
      if (next.path) params.set("path", next.path);
      else params.delete("path");
    }
    if (next.session !== undefined) {
      if (next.session) params.set("session", next.session);
      else params.delete("session");
    }
    if (next.blame !== undefined) {
      if (next.blame) params.set("blame", "1");
      else params.delete("blame");
    }
    setSearchParams(params, { replace: true });
  };

  const navigator = (
    <FileNavigator
      onScopeChange={(value) => apply({ session: value === PUBLISHED ? null : value ?? null, blame: false })}
      onSelect={(selected) => {
        apply({ path: selected });
        setNavigatorOpen(false);
      }}
      sandboxId={sandboxId}
      selectedPath={path}
      session={session}
      workspaces={workspaces}
    />
  );

  return (
    <Flex data-files-workspace h="100%" mih={0} miw={0} style={{ flex: 1, overflow: "hidden" }}>
      {!narrow ? (
        <Paper
          component="aside"
          data-files-navigator
          radius={0}
          withBorder
          style={{ display: "flex", flex: "0 0 17rem", flexDirection: "column", minHeight: 0 }}
        >
          {navigator}
        </Paper>
      ) : (
        <Drawer
          data-files-navigator-drawer
          onClose={() => setNavigatorOpen(false)}
          opened={navigatorOpen}
          position="left"
          size="20rem"
          title="File navigator"
        >
          <Box style={{ display: "flex", height: "100%", minHeight: 0, flexDirection: "column" }}>
            {navigator}
          </Box>
        </Drawer>
      )}

      <Flex direction="column" mih={0} miw={0} style={{ flex: 1 }}>
        <Paper component="header" data-files-toolbar px="md" py="sm" radius={0} withBorder>
          <Group justify="space-between" wrap="wrap">
            <Group gap="sm" wrap="nowrap">
              {narrow ? (
                <Button
                  aria-label="Open file navigator"
                  leftSection={<PanelLeft size={14} />}
                  onClick={() => setNavigatorOpen(true)}
                >
                  Files
                </Button>
              ) : null}
              <Box miw={0}>
                <Breadcrumbs separator="›">
                  <Text size="xs">Files</Text>
                  <Text c="dimmed" ff="monospace" size="xs">
                    {session ? `live · ${session}` : "published snapshot"}
                  </Text>
                  {path.split("/").filter(Boolean).map((segment, index) => (
                    <Text c={index === path.split("/").filter(Boolean).length - 1 ? undefined : "dimmed"} ff="monospace" key={`${segment}-${index}`} size="xs">
                      {segment}
                    </Text>
                  ))}
                </Breadcrumbs>
              </Box>
            </Group>
            <Tooltip
              label={
                session
                  ? "Blame reads the published auditability log. Switch to the published snapshot to use it."
                  : "Color each line by its owner from the publish auditability log."
              }
              openDelay={300}
            >
              <Button
                aria-pressed={blameOn}
                disabled={session !== null}
                leftSection={<GitBranch size={13} />}
                onClick={() => apply({ blame: !blameOn })}
                variant={blameOn ? "filled" : "default"}
              >
                Blame
              </Button>
            </Tooltip>
          </Group>
        </Paper>

        {path === "" ? (
          <Center p="xl" style={{ flex: 1, minHeight: 0 }}>
            <Paper maw={440} p="xl" ta="center" withBorder>
              <Text fw={600} size="sm">Pick a file</Text>
              <Text c="dimmed" mt="xs" size="xs">
                Browse the {session ? "live session workspace" : "published snapshot"} from the file navigator.
                Blame is available in published scope.
              </Text>
            </Paper>
          </Center>
        ) : (
          <FileView
            blameOn={blameOn && session === null}
            key={`${session ?? "published"}:${path}`}
            path={path}
            sandboxId={sandboxId}
            session={session}
          />
        )}
      </Flex>
    </Flex>
  );
}

function FileNavigator({
  sandboxId,
  session,
  selectedPath,
  workspaces,
  onScopeChange,
  onSelect,
}: {
  sandboxId: string;
  session: string | null;
  selectedPath: string;
  workspaces: { workspace_id: string }[];
  onScopeChange: (value: string | null) => void;
  onSelect: (path: string) => void;
}) {
  return (
    <Stack gap={0} h="100%" mih={0}>
      <Box p="sm" style={{ borderBottom: "1px solid var(--mantine-color-default-border)" }}>
        <Select
          aria-label="File scope"
          data={[
            { value: PUBLISHED, label: "published snapshot" },
            ...workspaces.map((workspace) => ({
              value: workspace.workspace_id,
              label: `live · ${workspace.workspace_id}`,
            })),
          ]}
          label="Scope"
          onChange={onScopeChange}
          size="xs"
          value={session ?? PUBLISHED}
        />
      </Box>
      <FileTree
        onSelect={onSelect}
        sandboxId={sandboxId}
        selectedPath={selectedPath}
        session={session}
      />
    </Stack>
  );
}
