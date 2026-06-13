import type {
  ContextDirEntry,
  ContextFileEntry,
  PursuitContext,
} from "./paths.js";

export type ResolvedContextPath =
  | { kind: "file"; path: string; entry: ContextFileEntry }
  | { kind: "directory"; path: string; entry: ContextDirEntry }
  | { kind: "error"; message: string };

/**
 * Resolve a read path against the latest universe. Paths are relative to
 * the pursuit root; a leading `pursuit_<id>` segment is accepted and
 * stripped. Unknown paths error naming the valid children at the deepest
 * resolved segment - after a refocus that is how an agent holding an old
 * live attempt path discovers `superseded/`.
 */
export function resolveContextPath(
  context: PursuitContext,
  rawPath: string | undefined,
): ResolvedContextPath {
  const path = normalize(context, rawPath);
  const file = context.files.get(path);
  if (file) return { kind: "file", path, entry: file };
  const directory = context.directories.get(path);
  if (directory) return { kind: "directory", path, entry: directory };

  const segments = path.split("/");
  let deepest = "";
  for (let count = segments.length - 1; count > 0; count -= 1) {
    const prefix = segments.slice(0, count).join("/");
    if (context.directories.has(prefix)) {
      deepest = prefix;
      break;
    }
  }
  const children = directChildren(context, deepest);
  const at = deepest === "" ? context.rootPath : deepest;
  return {
    kind: "error",
    message: `unknown context path "${path}"; valid children of "${at}": ${
      children.join(", ") || "none"
    }`,
  };
}

/** Direct child names (files and directories) of one resolved directory. */
function directChildren(context: PursuitContext, dirPath: string): string[] {
  const prefix = dirPath === "" ? "" : `${dirPath}/`;
  const names = new Set<string>();
  for (const candidate of [...context.files.keys(), ...context.directories.keys()]) {
    if (candidate === "" || !candidate.startsWith(prefix)) continue;
    const rest = candidate.slice(prefix.length);
    if (rest === "") continue;
    const name = rest.split("/", 1)[0];
    if (name) names.add(name);
  }
  return [...names].sort();
}

function normalize(context: PursuitContext, rawPath: string | undefined): string {
  const trimmed = (rawPath ?? "").replace(/^\/+|\/+$/g, "");
  if (trimmed === "" || trimmed === context.rootPath) return "";
  return trimmed.startsWith(`${context.rootPath}/`)
    ? trimmed.slice(context.rootPath.length + 1)
    : trimmed;
}
