import { useEffect, useRef, useState } from "react";
import { EditorState } from "@codemirror/state";
import { EditorView, lineNumbers } from "@codemirror/view";
import {
  Button,
  Combobox,
  MantineProvider,
  Modal,
  Text,
  TextInput,
  Tooltip,
  Tree,
  useCombobox,
  useTree,
} from "@mantine/core";
import { useForm } from "@mantine/form";
import { useReducedMotion } from "@mantine/hooks";
import { Notifications, notifications } from "@mantine/notifications";
import { useVirtualizer } from "@tanstack/react-virtual";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import "./mantine-compatibility-spike.css";

const OPTIONS = Array.from({ length: 2_000 }, (_, index) =>
  `option ${index.toString().padStart(4, "0")}`,
);

const TREE_DATA = [
  {
    value: "workspace",
    label: "workspace",
    children: [{ value: "workspace/src", label: "src" }],
  },
];

function VirtualCombobox() {
  const [value, setValue] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const combobox = useCombobox({
    onDropdownClose: () => combobox.resetSelectedOption(),
    onDropdownOpen: () => combobox.selectFirstOption(),
  });
  const options = OPTIONS.filter((option) =>
    option.includes(search.trim().toLowerCase()),
  );
  const virtualizer = useVirtualizer({
    count: options.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 28,
    overscan: 4,
    initialRect: { width: 280, height: 168 },
    // The disposable fixture uses a fixed viewport so its virtual range is
    // deterministic in jsdom as well as in screenshot capture.
    observeElementRect: (_instance, callback) => {
      callback({ width: 280, height: 168 });
      return () => undefined;
    },
    observeElementOffset: (_instance, callback) => {
      callback(0, false);
      return () => undefined;
    },
  });

  return (
    <Combobox
      store={combobox}
      withinPortal={false}
      onOptionSubmit={(next) => {
        setValue(next);
        setSearch(next);
        combobox.closeDropdown();
      }}
    >
      <Combobox.Target>
        <TextInput
          label="Virtual option"
          value={search}
          onChange={(event) => {
            setSearch(event.currentTarget.value);
            combobox.openDropdown();
            combobox.updateSelectedOptionIndex();
          }}
          onFocus={() => combobox.openDropdown()}
          placeholder="Search 2,000 options"
        />
      </Combobox.Target>
      <Combobox.Dropdown>
        <Combobox.Options>
          <div
            ref={scrollRef}
            data-testid="virtual-options"
            style={{ maxHeight: 168, overflow: "auto" }}
          >
            <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
              {virtualizer.getVirtualItems().map((item) => (
                <Combobox.Option
                  key={options[item.index]}
                  value={options[item.index]}
                  style={{
                    height: item.size,
                    position: "absolute",
                    top: 0,
                    transform: `translateY(${item.start}px)`,
                    width: "100%",
                  }}
                >
                  {options[item.index]}
                </Combobox.Option>
              ))}
            </div>
          </div>
        </Combobox.Options>
      </Combobox.Dropdown>
      <output data-testid="selected-option">{value ?? "none"}</output>
    </Combobox>
  );
}

function CodeMirrorProbe() {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!hostRef.current) return;
    const view = new EditorView({
      state: EditorState.create({
        doc: "P00 CodeMirror probe",
        extensions: [
          lineNumbers(),
          EditorView.lineWrapping,
          EditorView.contentAttributes.of({ "aria-label": "P00 CodeMirror probe" }),
        ],
      }),
      parent: hostRef.current,
    });
    return () => view.destroy();
  }, []);

  return <div ref={hostRef} data-testid="codemirror-probe" />;
}

function UplotProbe() {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!hostRef.current) return;
    const plot = new uPlot(
      {
        width: 280,
        height: 120,
        legend: { show: false },
        series: [{}, { label: "P00 sample", stroke: "#2563eb" }],
      },
      [
        [0, 1, 2],
        [1, 2, 1],
      ],
      hostRef.current,
    );
    return () => plot.destroy();
  }, []);

  return <div ref={hostRef} data-testid="uplot-probe" />;
}

function FormProbe() {
  const form = useForm({
    initialValues: { label: "" },
    validate: {
      label: (value) => (value.trim() ? null : "A label is required"),
    },
  });

  return (
    <form onSubmit={form.onSubmit(() => undefined)}>
      <TextInput label="Required label" {...form.getInputProps("label")} />
      <Button type="submit">Validate form</Button>
    </form>
  );
}

function ModalProbe() {
  const [opened, setOpened] = useState(false);
  const reducedMotion = useReducedMotion();

  return (
    <>
      <Button onClick={() => setOpened(true)}>Open modal</Button>
      <Modal
        opened={opened}
        onClose={() => setOpened(false)}
        title="P00 portal modal"
        transitionProps={{ duration: reducedMotion ? 0 : 100 }}
      >
        <TextInput label="Modal input" data-autofocus />
        <Button mt="sm" onClick={() => setOpened(false)}>
          Close modal
        </Button>
      </Modal>
      <output data-testid="reduced-motion">{String(reducedMotion)}</output>
    </>
  );
}

function NotificationProbe() {
  return (
    <Button
      onClick={() =>
        notifications.show({ title: "P00 notification", message: "Mantine notifications mount" })
      }
    >
      Show notification
    </Button>
  );
}

function TooltipProbe() {
  const [opened, setOpened] = useState(false);
  return (
    <Tooltip opened={opened} label="Keyboard tooltip" openDelay={0} closeDelay={0}>
      <Button onFocus={() => setOpened(true)} onBlur={() => setOpened(false)}>
        Tooltip trigger
      </Button>
    </Tooltip>
  );
}

function TreeProbe() {
  const tree = useTree();
  return (
    <Tree tree={tree} data={TREE_DATA} />
  );
}

/**
 * Disposable P00 compatibility fixture. It is only imported by its contract
 * test; production MantineProvider setup begins in P02.
 */
export function MantineCompatibilitySpike({
  withUplot = true,
  withVirtualCombobox = true,
  withTree = true,
  withCodeMirror = true,
}: {
  withUplot?: boolean;
  withVirtualCombobox?: boolean;
  withTree?: boolean;
  withCodeMirror?: boolean;
}) {
  return (
    <MantineProvider theme={{ primaryColor: "blue", primaryShade: 8 }}>
      <Notifications />
      <main>
        <h1 className="p00-visually-hidden">P00 Mantine compatibility fixture</h1>
        <ModalProbe />
        <TooltipProbe />
        <NotificationProbe />
        <FormProbe />
        {withVirtualCombobox ? <VirtualCombobox /> : null}
        {withTree ? <TreeProbe /> : null}
        {withCodeMirror ? <CodeMirrorProbe /> : null}
        {withUplot ? <UplotProbe /> : null}
        <Text size="xs">Disposable P00 fixture</Text>
      </main>
    </MantineProvider>
  );
}
