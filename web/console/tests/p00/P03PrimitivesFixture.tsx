import {
  ActionIcon,
  Badge,
  Breadcrumbs,
  Button,
  Card,
  Combobox,
  Drawer,
  Group,
  Input,
  InputBase,
  Menu,
  Modal,
  Paper,
  Popover,
  ScrollArea,
  Select,
  Skeleton,
  Stack,
  Tabs,
  Text,
  Textarea,
  ThemeIcon,
  Tooltip,
  useCombobox,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  Bell,
  Check,
  ChevronDown,
  CircleAlert,
  FolderOpen,
  MoreHorizontal,
  Send,
  X,
} from "lucide-react";
import { createRoot } from "react-dom/client";
import { useState } from "react";
import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "../../src/index.css";
import { AppProviders } from "../../src/AppProviders";
import { StateBadge } from "../../src/components/StateBadge";
import styles from "./p03-primitives-fixture.module.css";

const scopeOptions = [
  { value: "published", label: "Published workspace" },
  { value: "session-a", label: "Session a7f3" },
  { value: "session-b", label: "Session c91e" },
];

function ScopeCombobox() {
  const [value, setValue] = useState(scopeOptions[0].value);
  const combobox = useCombobox({
    onDropdownClose: () => combobox.resetSelectedOption(),
    onDropdownOpen: () => combobox.selectFirstOption(),
  });
  const selected = scopeOptions.find((option) => option.value === value)!;

  return (
    <Combobox store={combobox} withinPortal={false}>
      <Combobox.Target targetType="button">
        <InputBase
          component="button"
          type="button"
          label="Workspace target"
          pointer
          rightSection={<Combobox.Chevron />}
          rightSectionPointerEvents="none"
          onClick={() => combobox.toggleDropdown()}
        >
          {selected.label}
        </InputBase>
      </Combobox.Target>
      <Combobox.Dropdown>
        <Combobox.Options>
          {scopeOptions.map((option) => (
            <Combobox.Option
              key={option.value}
              active={option.value === value}
              value={option.value}
              onClick={() => {
                setValue(option.value);
                combobox.closeDropdown();
              }}
            >
              {option.label}
            </Combobox.Option>
          ))}
        </Combobox.Options>
      </Combobox.Dropdown>
    </Combobox>
  );
}

function PrimitiveGallery() {
  const [modalOpened, setModalOpened] = useState(false);
  const [drawerOpened, setDrawerOpened] = useState(false);
  const [popoverOpened, setPopoverOpened] = useState(false);
  const [selectedImage, setSelectedImage] = useState<string | null>("node:22-alpine");
  const [validation, setValidation] = useState(false);

  const showNotifications = () => {
    notifications.show({
      autoClose: false,
      color: "danger",
      icon: <CircleAlert size={15} />,
      id: "p03-rpc-error",
      message: "The operation was rejected before any workspace changes were applied.",
      title: "policy_denied",
    });
    notifications.show({
      autoClose: false,
      color: "eyeBlue",
      icon: <Check size={15} />,
      id: "p03-command-complete",
      message: "Session output has been saved to the command ledger.",
      title: "Command completed",
    });
  };

  return (
    <main className={styles.gallery} data-testid="p03-gallery">
      <header className={styles.header}>
        <div>
          <Text c="dimmed" fw={700} size="xs" tt="uppercase">
            P03 primitive gallery
          </Text>
          <h1>Console controls and operator states</h1>
          <Text c="dimmed" maw="54rem">
            Shared Mantine primitives are shown in the compact EphemeralOS console language before page-by-page migration.
          </Text>
        </div>
        <Breadcrumbs separator="/">
          <Text size="sm">Console</Text>
          <Text size="sm">Foundation</Text>
          <Text size="sm">P03</Text>
        </Breadcrumbs>
      </header>

      <div className={styles.columns}>
        <Paper className={styles.section} p="md" withBorder>
          <Text fw={700}>Actions and status</Text>
          <Text c="dimmed" size="sm">Default, compact, destructive, and icon-only console actions.</Text>
          <Group className={styles.wrap} mt="sm">
            <Button>Outline default</Button>
            <Button size="compact-xs">Compact action</Button>
            <Button color="danger" variant="filled">Destroy sandbox</Button>
            <ActionIcon aria-label="Send command" color="eyeBlue" variant="light">
              <Send size={15} />
            </ActionIcon>
            <ActionIcon aria-label="Close command" color="danger" variant="subtle">
              <X size={15} />
            </ActionIcon>
          </Group>
          <Group className={styles.wrap} mt="md">
            <StateBadge label="ready" state="ready" />
            <StateBadge label="running" state="running" />
            <StateBadge label="warning" state="warn" />
            <StateBadge label="failed" state="failed" />
            <Badge color="warm" variant="outline">stopped</Badge>
          </Group>
        </Paper>

        <Paper className={styles.section} p="md" withBorder>
          <Text fw={700}>Forms and selection</Text>
          <Text c="dimmed" size="sm">Native fields retain inline validation and loading/empty status space.</Text>
          <div className={styles.formGrid}>
            <Input.Wrapper error={validation ? "Sandbox name is required" : undefined} label="Sandbox name">
              <Input placeholder="sandbox-name" />
            </Input.Wrapper>
            <Textarea aria-label="Command" minRows={2} placeholder="npm test" />
            <Select
              label="Base image"
              value={selectedImage}
              data={[
                { value: "node:22-alpine", label: "node:22-alpine" },
                { value: "python:3.13-slim", label: "python:3.13-slim" },
              ]}
              onChange={setSelectedImage}
              error={validation ? "Choose a permitted image" : undefined}
            />
            <ScopeCombobox />
          </div>
          <Group className={styles.wrap} mt="sm">
            <Button onClick={() => setValidation((current) => !current)} variant="light">
              Toggle inline error
            </Button>
            <Badge color="eyeBlue" variant="light">loading image catalog</Badge>
            <Badge color="warm" variant="light">no matching sessions</Badge>
          </Group>
        </Paper>
      </div>

      <div className={styles.columns}>
        <Paper className={styles.section} p="md" withBorder>
          <Text fw={700}>Navigation and overlays</Text>
          <Group className={styles.wrap} mt="sm">
            <Tabs defaultValue="overview">
              <Tabs.List>
                <Tabs.Tab value="overview">Overview</Tabs.Tab>
                <Tabs.Tab value="events">Events</Tabs.Tab>
              </Tabs.List>
              <Tabs.Panel pt="xs" value="overview">Workspace summary is current.</Tabs.Panel>
              <Tabs.Panel pt="xs" value="events">No new events.</Tabs.Panel>
            </Tabs>

            <Menu shadow="md" width={180}>
              <Menu.Target>
                <Button rightSection={<ChevronDown size={14} />} variant="default">More actions</Button>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Label>Workspace</Menu.Label>
                <Menu.Item leftSection={<FolderOpen size={14} />}>Open files</Menu.Item>
                <Menu.Item color="danger" leftSection={<X size={14} />}>Discard session</Menu.Item>
              </Menu.Dropdown>
            </Menu>

            <Popover opened={popoverOpened} onChange={setPopoverOpened} position="bottom" withArrow>
              <Popover.Target>
                <Button onClick={() => setPopoverOpened((opened) => !opened)} variant="default">Open popover</Button>
              </Popover.Target>
              <Popover.Dropdown>
                <Text fw={700} size="sm">Preview scope</Text>
                <Text c="dimmed" size="sm">Published workspace selected.</Text>
              </Popover.Dropdown>
            </Popover>

            <Tooltip label="Command metadata" openDelay={0} withArrow>
              <ActionIcon aria-label="Command metadata" variant="default"><MoreHorizontal size={15} /></ActionIcon>
            </Tooltip>
            <Button onClick={() => setModalOpened(true)} variant="default">Open modal</Button>
            <Button onClick={() => setDrawerOpened(true)} variant="default">Open drawer</Button>
            <Button leftSection={<Bell size={14} />} onClick={showNotifications} variant="light">Show notification stack</Button>
          </Group>
        </Paper>

        <Card className={styles.section} padding="md" withBorder>
          <Text fw={700}>Loading, empty, and error states</Text>
          <Stack gap="sm" mt="sm">
            <Skeleton height={10} radius="xl" />
            <Skeleton height={10} radius="xl" width="78%" />
            <Group gap="xs" wrap="nowrap">
              <ThemeIcon color="warm" variant="light"><FolderOpen size={15} /></ThemeIcon>
              <Text size="sm">No files match this scope.</Text>
            </Group>
            <Group gap="xs" wrap="nowrap">
              <ThemeIcon color="danger" variant="light"><CircleAlert size={15} /></ThemeIcon>
              <Text size="sm">Connection lost. Retry remains available.</Text>
            </Group>
          </Stack>
        </Card>
      </div>

      <Paper className={styles.section} p="md" withBorder>
        <Text fw={700}>Ordinary scroll surface</Text>
        <ScrollArea
          className={styles.scrollArea}
          type="always"
          offsetScrollbars
          viewportProps={{ "aria-label": "Workspace event log", tabIndex: 0 }}
        >
          <pre className={styles.log}>{Array.from({ length: 12 }, (_, index) => `#${String(index + 1).padStart(2, "0")} workspace event: health probe accepted`).join("\n")}</pre>
        </ScrollArea>
      </Paper>

      <Modal
        closeButtonProps={{ "aria-label": "Close destructive action" }}
        onClose={() => setModalOpened(false)}
        opened={modalOpened}
        title="Destroy sandbox"
      >
        <Text c="dimmed" size="sm">This action permanently removes the sandbox and active sessions.</Text>
        <Input.Wrapper label="Type sandbox id to confirm" mt="md">
          <Input data-autofocus placeholder="sandbox-a" />
        </Input.Wrapper>
        <Group justify="flex-end" mt="md">
          <Button onClick={() => setModalOpened(false)} variant="subtle">Cancel</Button>
          <Button color="danger" onClick={() => setModalOpened(false)} variant="filled">Destroy</Button>
        </Group>
      </Modal>

      <Drawer
        closeButtonProps={{ "aria-label": "Close workspace drawer" }}
        onClose={() => setDrawerOpened(false)}
        opened={drawerOpened}
        position="right"
        title="Workspace details"
      >
        <Input.Wrapper label="Filter workspace files">
          <Input data-autofocus placeholder="src/" />
        </Input.Wrapper>
        <Text c="dimmed" mt="md" size="sm">Focus is contained while the drawer is open.</Text>
      </Drawer>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(
  <AppProviders>
    <PrimitiveGallery />
  </AppProviders>,
);
