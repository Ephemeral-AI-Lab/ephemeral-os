import { rpc, systemScope } from "@/api/rpc";

export interface DirectoryListing {
  path: string | null;
  parent: string | null;
  truncated: boolean;
  directories: { name: string; path: string }[];
}

export async function listDockerImages(): Promise<string[]> {
  const { images } = await rpc<{ images: string[] }>("list_docker_images", systemScope);
  return images;
}

export function listWorkspaceDirectories(path: string | null): Promise<DirectoryListing> {
  return rpc<DirectoryListing>(
    "list_workspace_directories",
    systemScope,
    path === null ? {} : { path },
  );
}
