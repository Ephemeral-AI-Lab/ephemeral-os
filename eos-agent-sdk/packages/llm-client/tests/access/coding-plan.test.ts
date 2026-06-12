import { describe, expect, it } from "vitest";

import { apiKeyAccess } from "../../src/access/api-key.js";
import { claudeCodingPlanAccess } from "../../src/access/claude-coding-plan.js";
import {
  codexAccessClaimsFromJwt,
  codexCodingPlanAccess,
} from "../../src/access/codex-coding-plan.js";
import { ProviderError } from "../../src/errors.js";
import { SecretString } from "../../src/secret.js";

/** Mirror of the Rust test helper: a jwt whose payload carries the auth claim. */
function jwtWithAuthClaim(accountId: string | undefined, fedramp: boolean): string {
  const auth =
    accountId === undefined
      ? {}
      : { chatgpt_account_id: accountId, chatgpt_account_is_fedramp: fedramp };
  const payload = Buffer.from(
    JSON.stringify({ "https://api.openai.com/auth": auth }),
  ).toString("base64url");
  return `header.${payload}.signature`;
}

describe("api key access", () => {
  it("yields an api_key credential with no extra headers", async () => {
    const access = apiKeyAccess("https://api.anthropic.com", new SecretString("k"));
    expect(access.baseUrl).toBe("https://api.anthropic.com");
    expect(access.credential.kind).toBe("api_key");
    expect(access.credential.secret.expose()).toBe("k");
    await expect(access.headers()).resolves.toEqual({});
  });
});

describe("claude coding plan access", () => {
  it("yields an oauth bearer with the beta and identity headers", async () => {
    const access = claudeCodingPlanAccess(
      "https://api.anthropic.com",
      new SecretString("oauth-token"),
    );
    expect(access.credential.kind).toBe("bearer");
    expect(access.credential.secret.expose()).toBe("oauth-token");
    await expect(access.headers()).resolves.toEqual({
      "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
      "anthropic-dangerous-direct-browser-access": "true",
      "user-agent": "claude-cli/2.1.75",
      "x-app": "cli",
    });
  });
});

describe("codex coding plan access", () => {
  it("reads the account claim into bearer credentials and routing headers", async () => {
    const token = jwtWithAuthClaim("account-123", true);
    const access = codexCodingPlanAccess(
      "https://chatgpt.com/backend-api/codex",
      new SecretString(token),
    );
    expect(access.credential.kind).toBe("bearer");
    expect(access.credential.secret.expose()).toBe(token);
    await expect(access.headers()).resolves.toEqual({
      "chatgpt-account-id": "account-123",
      "x-openai-fedramp": "true",
    });
  });

  it("omits the fedramp header for non-fedramp accounts", async () => {
    const access = codexCodingPlanAccess(
      "https://chatgpt.com/backend-api/codex",
      new SecretString(jwtWithAuthClaim("account-123", false)),
    );
    await expect(access.headers()).resolves.toEqual({
      "chatgpt-account-id": "account-123",
    });
  });

  it("defaults the fedramp claim to false when absent", () => {
    const payload = Buffer.from(
      JSON.stringify({
        "https://api.openai.com/auth": { chatgpt_account_id: "account-123" },
      }),
    ).toString("base64url");
    const claims = codexAccessClaimsFromJwt(`header.${payload}.sig`);
    expect(claims).toEqual({
      accountId: "account-123",
      isFedrampAccount: false,
    });
  });

  const failures: [string, string, string][] = [
    ["a bare string", "not-a-jwt", "codex access token is not a jwt"],
    [
      "an empty payload segment",
      "header..signature",
      "codex access token is not a jwt",
    ],
    [
      "a non-base64url payload",
      "header.no+pad=.signature",
      "codex access token payload is not base64url",
    ],
    [
      "a non-json payload",
      `header.${Buffer.from("not json").toString("base64url")}.signature`,
      "codex access token payload is not json",
    ],
    [
      "a missing auth claim",
      `header.${Buffer.from("{}").toString("base64url")}.signature`,
      "codex access token missing https://api.openai.com/auth claim",
    ],
    [
      "a missing account id",
      jwtWithAuthClaim(undefined, false),
      "codex access token missing chatgpt_account_id claim",
    ],
    [
      "a blank account id",
      jwtWithAuthClaim("   ", false),
      "codex access token missing chatgpt_account_id claim",
    ],
  ];

  it.each(failures)(
    "rejects %s as a request-kind provider error",
    (_label, token, message) => {
      let caught: unknown;
      try {
        codexAccessClaimsFromJwt(token);
      } catch (error) {
        caught = error;
      }
      expect(caught).toBeInstanceOf(ProviderError);
      const provider = caught as ProviderError;
      expect(provider.kind).toBe("request");
      expect(provider.message).toBe(message);
    },
  );
});
