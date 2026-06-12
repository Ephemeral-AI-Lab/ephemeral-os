import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { loadLlmClientRegistry } from "../src/llm-client-registry.js";
import { codexJwtPayload, mintJwt, tempDir } from "./support.js";

interface Fixture {
  configPath: string;
  authPath: string;
}

function writeFixture(options: {
  jwt?: string;
  authBody?: string;
  clients?: unknown;
}): Fixture {
  const dir = tempDir("eos-llm-clients-");
  const authPath = join(dir, "auth.json");
  writeFileSync(
    authPath,
    options.authBody ??
      JSON.stringify({
        tokens: { access_token: options.jwt ?? mintJwt(codexJwtPayload()) },
      }),
  );
  const configPath = join(dir, "llm_clients.json");
  writeFileSync(
    configPath,
    JSON.stringify(
      options.clients ?? {
        clients: [
          {
            id: "codex_coding_plan",
            provider: "codex_coding_plan",
            model_id: "gpt-5.5",
            reasoning_effort: "medium",
            base_url: "https://chatgpt.com/backend-api/codex",
            auth: { kind: "codex_cli_auth_file", path: authPath },
          },
        ],
      },
    ),
  );
  return { configPath, authPath };
}

describe("llm client registry", () => {
  it("loads the codex coding-plan entry and builds its client (§13.2)", () => {
    const { configPath } = writeFixture({});
    const binding = loadLlmClientRegistry(configPath).require("codex_coding_plan");
    expect(binding).toMatchObject({
      id: "codex_coding_plan",
      model_id: "gpt-5.5",
      reasoning_effort: "medium",
    });
    expect(typeof binding.client.streamMessage).toBe("function");
  });

  it("reads the configured auth file without persisting the token (§13.2)", () => {
    const { configPath, authPath } = writeFixture({});
    const authBefore = readFileSync(authPath, "utf8");
    const configBefore = readFileSync(configPath, "utf8");
    loadLlmClientRegistry(configPath).require("codex_coding_plan");
    expect(readFileSync(authPath, "utf8"), "auth file untouched").toBe(authBefore);
    expect(readFileSync(configPath, "utf8"), "config untouched").toBe(configBefore);
  });

  it("throws on an unknown client id, naming the config path (§13.2)", () => {
    const { configPath } = writeFixture({});
    expect(() => loadLlmClientRegistry(configPath).require("absent")).toThrow(
      `unknown llm client id "absent" (config: ${configPath})`,
    );
  });

  it.each`
    problem                     | options                                                                        | expected
    ${"missing config file"}    | ${{ clients: undefined, missing: true }}                                       | ${/is not readable/}
    ${"invalid JSON"}           | ${{ raw: "{nope" }}                                                            | ${/is not valid JSON/}
    ${"unsupported provider"}   | ${{ clients: { clients: [{ id: "x", provider: "openai_api", model_id: "m", auth: { kind: "codex_cli_auth_file", path: "/p" } }] } }} | ${/is invalid: .*provider/}
    ${"missing auth path"}      | ${{ clients: { clients: [{ id: "x", provider: "codex_coding_plan", model_id: "m", auth: { kind: "codex_cli_auth_file" } }] } }}      | ${/is invalid: .*auth\.path/}
  `(
    "fails loudly at startup on $problem (§13.2)",
    ({
      options,
      expected,
    }: {
      options: { clients?: unknown; raw?: string; missing?: boolean };
      expected: RegExp;
    }) => {
      const dir = tempDir("eos-llm-clients-");
      const configPath = join(dir, "llm_clients.json");
      if (options.raw !== undefined) writeFileSync(configPath, options.raw);
      else if (!options.missing) writeFileSync(configPath, JSON.stringify(options.clients));
      expect(() => loadLlmClientRegistry(configPath)).toThrow(expected);
    },
  );

  it("rejects duplicate client ids at startup", () => {
    const { configPath, authPath } = writeFixture({});
    const entry = {
      id: "codex_coding_plan",
      provider: "codex_coding_plan",
      model_id: "gpt-5.5",
      auth: { kind: "codex_cli_auth_file", path: authPath },
    };
    writeFileSync(configPath, JSON.stringify({ clients: [entry, entry] }));
    expect(() => loadLlmClientRegistry(configPath)).toThrow(
      /duplicate client id "codex_coding_plan"/,
    );
  });

  it.each`
    problem                    | fixture                                                       | expected
    ${"missing auth file"}     | ${{ authBody: undefined, dropAuthFile: true }}                | ${/not found \(run "codex login"\)/}
    ${"no access_token"}       | ${{ authBody: JSON.stringify({ tokens: {} }) }}               | ${/has no tokens\.access_token/}
    ${"no chatgpt claim"}      | ${{ jwt: mintJwt({ exp: Math.floor(Date.now() / 1000) + 3600 }) }} | ${/no chatgpt account claim/}
    ${"expired token"}         | ${{ jwt: mintJwt(codexJwtPayload({ exp: Math.floor(Date.now() / 1000) - 10 })) }} | ${/is expired/}
    ${"opaque non-JWT token"}  | ${{ jwt: "not-a-jwt" }}                                       | ${/no chatgpt account claim/}
  `(
    "fails loudly when the codex auth file has $problem (§13.2)",
    ({
      fixture,
      expected,
    }: {
      fixture: { authBody?: string; jwt?: string; dropAuthFile?: boolean };
      expected: RegExp;
    }) => {
      const { configPath, authPath } = writeFixture(fixture);
      if (fixture.dropAuthFile) {
        // Point the config at a path that never existed.
        const config = JSON.parse(readFileSync(configPath, "utf8")) as {
          clients: { auth: { path: string } }[];
        };
        config.clients[0].auth.path = `${authPath}.absent`;
        writeFileSync(configPath, JSON.stringify(config));
      }
      expect(() => loadLlmClientRegistry(configPath)).toThrow(expected);
    },
  );

  it("defaults reasoning_effort to medium when the entry omits it", () => {
    const dir = tempDir("eos-llm-clients-");
    const authPath = join(dir, "auth.json");
    writeFileSync(
      authPath,
      JSON.stringify({ tokens: { access_token: mintJwt(codexJwtPayload()) } }),
    );
    const configPath = join(dir, "llm_clients.json");
    writeFileSync(
      configPath,
      JSON.stringify({
        clients: [
          {
            id: "codex",
            provider: "codex_coding_plan",
            model_id: "gpt-5.5",
            auth: { kind: "codex_cli_auth_file", path: authPath },
          },
        ],
      }),
    );
    expect(loadLlmClientRegistry(configPath).require("codex").reasoning_effort).toBe(
      "medium",
    );
  });
});
