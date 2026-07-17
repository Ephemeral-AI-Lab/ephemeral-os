import { Check, Plus } from "lucide-react";
import {
  Box,
  Combobox,
  Group,
  InputBase,
  Loader,
  Text,
  useCombobox,
} from "@mantine/core";
import type { WorkspaceSnapshot } from "@/api/observability";

export type NetworkProfile = "shared" | "isolated";

export type CreatedSessionTarget = {
  workspaceSessionId: string;
  networkProfile: NetworkProfile;
};

const AUTOMATIC = "action:automatic";
const CREATE_SHARED = "action:create:shared";
const CREATE_ISOLATED = "action:create:isolated";
const SESSION_PREFIX = "session:";

export function SessionTargetPicker({
  workspaces,
  value,
  createdSession,
  creating,
  onChange,
  onCreate,
}: {
  workspaces: WorkspaceSnapshot[];
  value: string | null;
  createdSession: CreatedSessionTarget | null;
  creating: boolean;
  onChange: (workspaceSessionId: string | null) => void;
  onCreate: (networkProfile: NetworkProfile) => void;
}) {
  const combobox = useCombobox({
    onDropdownClose: () => combobox.resetSelectedOption(),
    onDropdownOpen: () => combobox.selectFirstOption(),
  });
  const sessions = workspaces.map((workspace) => ({
    workspaceSessionId: workspace.workspace_id,
    networkProfile: workspace.network_profile,
  }));
  if (
    createdSession &&
    !sessions.some((session) => session.workspaceSessionId === createdSession.workspaceSessionId)
  ) {
    sessions.push(createdSession);
  }
  const selectedSession = sessions.find((session) => session.workspaceSessionId === value) ?? null;
  const selectedLabel = selectedSession
    ? `${selectedSession.workspaceSessionId} · ${selectedSession.networkProfile}`
    : "Automatic · shared · auto-publish";

  return (
    <Combobox
      store={combobox}
      readOnly={creating}
      position="top-start"
      onOptionSubmit={(option) => {
        combobox.closeDropdown();
        if (option === AUTOMATIC) {
          onChange(null);
        } else if (option === CREATE_SHARED) {
          onCreate("shared");
        } else if (option === CREATE_ISOLATED) {
          onCreate("isolated");
        } else if (option.startsWith(SESSION_PREFIX)) {
          onChange(option.slice(SESSION_PREFIX.length));
        }
      }}
    >
      <Combobox.Target targetType="button">
        <InputBase
          aria-label="Workspace session"
          component="button"
          data-terminal-session-picker
          disabled={creating}
          label="Workspace session"
          pointer
          rightSection={creating ? <Loader size="xs" /> : <Combobox.Chevron />}
          rightSectionPointerEvents="none"
          type="button"
          onClick={() => combobox.toggleDropdown()}
          styles={{ input: { textAlign: "left" } }}
        >
          <Text component="span" ff={selectedSession ? "monospace" : undefined} size="sm" truncate>
            {selectedLabel}
          </Text>
        </InputBase>
      </Combobox.Target>

      <Combobox.Dropdown>
        <Combobox.Options>
          <Combobox.Option active={value === null} value={AUTOMATIC}>
            <OptionContent
              checked={value === null}
              description="Shared network · publishes and closes after the command"
              label="Automatic"
            />
          </Combobox.Option>

          {sessions.length > 0 ? (
            <Combobox.Group label="Live sessions">
              {sessions.map((session) => (
                <Combobox.Option
                  active={value === session.workspaceSessionId}
                  key={session.workspaceSessionId}
                  value={`${SESSION_PREFIX}${session.workspaceSessionId}`}
                >
                  <OptionContent
                    checked={value === session.workspaceSessionId}
                    description={`${session.networkProfile} network · retains unpublished changes`}
                    label={session.workspaceSessionId}
                    monospace
                  />
                </Combobox.Option>
              ))}
            </Combobox.Group>
          ) : null}

          <Combobox.Group label="Create retained session">
            <Combobox.Option disabled={creating} value={CREATE_SHARED}>
              <OptionContent
                action
                description="Sandbox network · retains unpublished changes"
                label="Create shared session"
              />
            </Combobox.Option>
            <Combobox.Option disabled={creating} value={CREATE_ISOLATED}>
              <OptionContent
                action
                description="No external network · retains unpublished changes"
                label="Create isolated session"
              />
            </Combobox.Option>
          </Combobox.Group>
        </Combobox.Options>
      </Combobox.Dropdown>
    </Combobox>
  );
}

function OptionContent({
  label,
  description,
  checked = false,
  action = false,
  monospace = false,
}: {
  label: string;
  description: string;
  checked?: boolean;
  action?: boolean;
  monospace?: boolean;
}) {
  return (
    <Group gap="sm" justify="space-between" wrap="nowrap">
      <Group gap="xs" miw={0} wrap="nowrap">
        {action ? <Plus aria-hidden size={14} /> : null}
        <Box miw={0}>
          <Text ff={monospace ? "monospace" : undefined} size="sm" truncate>
            {label}
          </Text>
          <Text c="dimmed" size="xs" truncate>
            {description}
          </Text>
        </Box>
      </Group>
      {checked ? <Check aria-hidden size={14} /> : null}
    </Group>
  );
}
