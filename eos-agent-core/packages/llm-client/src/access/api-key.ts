import type { SecretString } from "../secret.js";
import type { Access } from "./access.js";

/** Static api-key access: first-party endpoints or any compatible base url. */
export function apiKeyAccess(baseUrl: string, apiKey: SecretString): Access {
  return {
    baseUrl,
    credential: { kind: "api_key", secret: apiKey },
    headers: () => Promise.resolve({}),
  };
}
