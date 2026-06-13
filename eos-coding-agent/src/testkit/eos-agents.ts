import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { stringify as stringifyYaml } from "yaml";

/** A profile fixture: frontmatter fields plus an optional system-prompt body. */
export interface ProfileFixture {
  name: string;
  llm_client_id?: string;
  description?: string;
  max_turns?: number;
  terminal_tool?: string;
  workflows?: string[];
  subagents?: string[];
  pursuit_context_script?: string;
  allowed_tools?: string[];
  /** Extra frontmatter keys (e.g. dropped fields) to assert rejection. */
  extra?: Record<string, unknown>;
  body?: string;
}

/** Write one `<dir>/<name>.md` profile file from a fixture. */
export function writeProfile(dir: string, fixture: ProfileFixture): string {
  const { body, extra, ...fields } = fixture;
  const frontmatter: Record<string, unknown> = {
    llm_client_id: "scripted",
    allowed_tools: [],
    ...stripUndefined(fields),
    ...extra,
  };
  const path = join(dir, `${fixture.name}.md`);
  writeFileSync(path, `---\n${stringifyYaml(frontmatter)}---\n\n${body ?? "You are a test agent."}\n`);
  return path;
}

/** Build a temp `.eos-agents`-style tree with a `profile/` dir of fixtures. */
export function tempProfileDir(...fixtures: ProfileFixture[]): string {
  const dir = mkdtempSync(join(tmpdir(), "eos-profiles-"));
  for (const fixture of fixtures) writeProfile(dir, fixture);
  return dir;
}

/** Write one `<dir>/<name>.md` workflow config file. */
export function writeWorkflow(
  dir: string,
  config: { name: string; type: string; description: string; tools: string[]; args?: Record<string, unknown>; body?: string },
): string {
  mkdirSync(dir, { recursive: true });
  const { body, ...frontmatter } = config;
  const path = join(dir, `${config.name}.md`);
  writeFileSync(path, `---\n${stringifyYaml(stripUndefined(frontmatter))}---\n\n${body ?? "Workflow docs."}\n`);
  return path;
}

function stripUndefined<T extends Record<string, unknown>>(value: T): Partial<T> {
  return Object.fromEntries(
    Object.entries(value).filter(([, child]) => child !== undefined),
  ) as Partial<T>;
}
