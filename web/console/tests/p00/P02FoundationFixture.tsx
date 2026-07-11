import { Button, Drawer, Modal, Paper, Text, TextInput, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { createRoot } from "react-dom/client";
import { useState } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "../../src/index.css";
import { AppProviders } from "../../src/AppProviders";
import { Shell } from "../../src/shell/Shell";
import styles from "./p02-foundation-fixture.module.css";

function FoundationSpecimen() {
  const [modalOpened, setModalOpened] = useState(false);
  const [drawerOpened, setDrawerOpened] = useState(false);

  return (
    <section className={styles.specimen} data-testid="foundation-sentinel">
      <header className={styles.heading}>
        <div>
          <Text c="dimmed" fw={700} size="xs" tt="uppercase">
            P02 foundation fixture
          </Text>
          <h1>Shared provider, stable legacy surface</h1>
          <p>
            One root owns Mantine overlays while the shared shell stays readable during migration.
          </p>
        </div>
        <Button data-testid="mantine-action">Mantine action</Button>
      </header>

      <div className={styles.grid}>
        <Paper aria-label="Legacy compatibility sentinel" p="md" shadow="sm" withBorder>
          <Text c="dimmed" fw={700} size="xs" tt="uppercase">
            Shared component surface
          </Text>
          <Text mt="sm" size="sm">
            This sentinel proves the shell remains usable while Mantine owns visual primitives.
          </Text>
          <Button mt="sm" size="compact-xs" variant="default" type="button">
            Legacy control
          </Button>
        </Paper>

        <section aria-label="Mantine overlay controls" className={styles.controls}>
          <Button onClick={() => setModalOpened(true)} variant="default">
            Open modal
          </Button>
          <Button onClick={() => setDrawerOpened(true)} variant="default">
            Open drawer
          </Button>
          <Tooltip label="Rendered above the shared shell" openDelay={0} withArrow>
            <Button data-testid="tooltip-trigger" variant="subtle">
              Reveal tooltip
            </Button>
          </Tooltip>
          <Button
            color="eyeBlue"
            onClick={() =>
              notifications.show({
                autoClose: false,
                id: "p02-foundation-notification",
                message: "A single Notifications host is active.",
                title: "Notification host active",
              })
            }
            variant="light"
          >
            Show notification
          </Button>
        </section>
      </div>

      <Modal
        closeButtonProps={{ "aria-label": "Close modal verification" }}
        onClose={() => setModalOpened(false)}
        opened={modalOpened}
        title="Modal verification"
      >
        <TextInput data-autofocus label="Modal command" placeholder="Focus arrives here" />
        <Button mt="md" onClick={() => setModalOpened(false)}>
          Close modal action
        </Button>
      </Modal>

      <Drawer
        closeButtonProps={{ "aria-label": "Close drawer verification" }}
        onClose={() => setDrawerOpened(false)}
        opened={drawerOpened}
        position="right"
        title="Drawer verification"
      >
        <TextInput data-autofocus label="Drawer filter" placeholder="Focus arrives here" />
        <Button mt="md" onClick={() => setDrawerOpened(false)}>
          Close drawer action
        </Button>
      </Drawer>
    </section>
  );
}

function P02FoundationFixture() {
  return (
    <AppProviders>
      <MemoryRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<FoundationSpecimen />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </AppProviders>
  );
}

createRoot(document.getElementById("root")!).render(<P02FoundationFixture />);
