import type { ModuleKind, SymbolKind } from "./types.js";

export interface SymbolTagInput {
  packageName: string;
  modulePath: string;
  moduleKind: ModuleKind;
  name: string;
  kind: SymbolKind;
  exported: boolean;
  signature: string;
  fields: readonly { name: string; ty: string }[];
  extends: readonly string[];
  implements: readonly string[];
  importSources: readonly string[];
  schemaKinds: readonly string[];
}

export function packageTags(
  packageName: string,
  sourceModuleCount: number,
): string[] {
  const tags = new Set<string>();
  if (sourceModuleCount === 0) {
    tags.add("empty-package");
  }
  if (packageName.endsWith("/contracts")) {
    tags.add("contracts");
  }
  if (packageName.endsWith("/engine") || packageName.endsWith("/runtime")) {
    tags.add("runtime");
  }
  if (packageName.endsWith("/llm-client")) {
    tags.add("provider-boundary");
  }
  if (packageName.endsWith("/testkit")) {
    tags.add("testkit");
  }
  if (packageName.endsWith("/db")) {
    tags.add("storage");
  }
  if (packageName.endsWith("/observability")) {
    tags.add("observability");
  }
  return [...tags].sort();
}

export function moduleKind(path: string): ModuleKind {
  if (path.endsWith("/src/index.ts")) {
    return "entrypoint";
  }
  if (path.includes("/tests/support.ts") || path.includes("/tests/fixtures/")) {
    return "test-support";
  }
  if (path.includes("/tests/") || path.endsWith(".test.ts")) {
    return "test";
  }
  return "source";
}

export function moduleTags(path: string, kind: ModuleKind): string[] {
  const tags = new Set<string>();
  tags.add(kind);
  if (kind === "entrypoint") {
    tags.add("package-entrypoint");
    tags.add("public-api");
  }
  if (kind === "test" || kind === "test-support") {
    tags.add("test-only");
  }
  if (path.includes("/wires/")) {
    tags.add("provider-wire");
  }
  if (path.includes("/providers/")) {
    tags.add("provider-client");
  }
  if (path.includes("/access/")) {
    tags.add("access-boundary");
  }
  if (path.includes("/config")) {
    tags.add("config");
  }
  if (path.includes("/events")) {
    tags.add("event");
  }
  return [...tags].sort();
}

export function symbolTags(input: SymbolTagInput): string[] {
  const tags = new Set<string>();
  if (input.exported) {
    tags.add("public-api");
    tags.add("exported");
  } else {
    tags.add("internal");
  }
  if (input.moduleKind === "test" || input.moduleKind === "test-support") {
    tags.add("test-only");
  }
  if (input.moduleKind === "entrypoint") {
    tags.add("package-entrypoint");
  }
  if (input.packageName.endsWith("/contracts")) {
    tags.add("contract");
  }
  if (input.kind === "schema") {
    tags.add("schema:zod");
  }
  for (const schemaKind of input.schemaKinds) {
    tags.add(schemaKind);
  }
  const lowerName = input.name.toLowerCase();
  const signature = input.signature;
  if (isDtoName(input.name)) {
    tags.add("dto");
  }
  if (lowerName.endsWith("id") || input.name.endsWith("IdSchema")) {
    tags.add("typed-id");
  }
  if (lowerName.includes("config") || signature.includes("Config")) {
    tags.add("config");
  }
  if (lowerName.includes("error") || input.extends.includes("Error")) {
    tags.add("error");
  }
  if (signature.includes("AbortSignal")) {
    tags.add("abortable");
  }
  if (signature.includes("AsyncIterable") || signature.includes("async *")) {
    tags.add("async-iterable");
  }
  if (signature.includes("async ")) {
    tags.add("async");
  }
  if (input.kind === "class") {
    tags.add("stateful");
  }
  if (lowerName.includes("event") || input.modulePath.includes("/events")) {
    tags.add("event");
  }
  if (lowerName.includes("handle")) {
    tags.add("run-handle");
  }
  if (lowerName.includes("factory") || lowerName.startsWith("create")) {
    tags.add("factory");
  }
  if (lowerName.includes("retry")) {
    tags.add("retry");
  }
  if (lowerName.includes("secret") || input.modulePath.includes("/secret")) {
    tags.add("secret");
  }
  if (
    input.name.endsWith("Client") ||
    input.implements.some((name) => name.includes("LlmClient"))
  ) {
    tags.add("provider-client");
  }
  if (input.modulePath.includes("/wires/")) {
    tags.add("provider-wire");
  }
  if (input.modulePath.includes("/access/")) {
    tags.add("access-boundary");
  }
  if (input.importSources.some((source) => source === "openai" || source.startsWith("@anthropic-ai/"))) {
    tags.add("sdk-edge");
  }
  if (
    input.fields.some((field) =>
      ["LlmClient", "ToolExecutor", "AbortSignal"].some((boundaryType) =>
        field.ty.includes(boundaryType),
      ),
    )
  ) {
    tags.add("di-boundary");
  }
  if (signature.includes("JsonObject") || signature.includes("JsonValue")) {
    tags.add("json-boundary");
  }
  return [...tags].sort();
}

function isDtoName(name: string): boolean {
  return /(?:Input|Output|Request|Response|Dto|DTO)$/.test(name);
}
