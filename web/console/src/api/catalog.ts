import { useQuery } from "@tanstack/react-query";

export type ArgKind = "string" | "integer" | "float" | "path" | "json_array";

export interface ArgSpecDoc {
  name: string;
  kind: ArgKind;
  required: boolean;
  help: string;
  default: string | null;
  cli: { flag: string | null; positional: string | null } | null;
}

export interface OperationSpecDoc {
  name: string;
  family: string;
  summary: string;
  description: string;
  args: ArgSpecDoc[];
}

export interface CatalogDoc {
  operation_execution_space: string;
  families: { id: string; title: string; summary: string }[];
  operations: OperationSpecDoc[];
}

export interface Catalogs {
  management: CatalogDoc;
  runtime: CatalogDoc;
  observability: CatalogDoc;
}

async function fetchCatalogs(): Promise<Catalogs> {
  const response = await fetch("/api/catalog");
  if (!response.ok) {
    throw new Error(`catalog fetch failed: HTTP ${response.status}`);
  }
  return (await response.json()) as Catalogs;
}

export function useCatalogs() {
  return useQuery({
    queryKey: ["catalog"],
    queryFn: fetchCatalogs,
    staleTime: Infinity,
    gcTime: Infinity,
  });
}

export function findOperation(
  catalog: CatalogDoc | undefined,
  name: string,
): OperationSpecDoc | undefined {
  return catalog?.operations.find((operation) => operation.name === name);
}

/**
 * Validate and convert one form field against its catalog spec. Returns the
 * protocol-typed value, `undefined` when an optional field is blank (the
 * argument must then be omitted entirely), or an error string.
 */
export function parseArgValue(
  spec: ArgSpecDoc,
  raw: string,
): { value?: unknown; error?: string } {
  const trimmed = raw.trim();
  if (trimmed === "") {
    if (spec.required) return { error: `${spec.name} is required` };
    return {};
  }
  switch (spec.kind) {
    case "integer": {
      if (!/^\d+$/.test(trimmed)) {
        return { error: `${spec.name} must be an unsigned integer` };
      }
      return { value: Number(trimmed) };
    }
    case "float": {
      const parsed = Number(trimmed);
      if (!Number.isFinite(parsed)) {
        return { error: `${spec.name} must be a finite number` };
      }
      return { value: parsed };
    }
    case "json_array": {
      let parsed: unknown;
      try {
        parsed = JSON.parse(trimmed);
      } catch {
        return { error: `${spec.name} must be a JSON array` };
      }
      if (!Array.isArray(parsed)) {
        return { error: `${spec.name} must be a JSON array` };
      }
      return { value: parsed };
    }
    default:
      return { value: raw };
  }
}

/**
 * Build an args object from raw form values, dropping blank optionals and
 * collecting per-field errors keyed by arg name.
 */
export function buildArgs(
  specs: ArgSpecDoc[],
  values: Record<string, string>,
): { args: Record<string, unknown>; errors: Record<string, string> } {
  const args: Record<string, unknown> = {};
  const errors: Record<string, string> = {};
  for (const spec of specs) {
    const parsed = parseArgValue(spec, values[spec.name] ?? "");
    if (parsed.error) errors[spec.name] = parsed.error;
    else if (parsed.value !== undefined) args[spec.name] = parsed.value;
  }
  return { args, errors };
}
