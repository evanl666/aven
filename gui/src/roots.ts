/**
 * Which folders aven may act in.
 *
 * Stored in `~/.aven/desktop.toml`, beside `approvals.toml` and `triggers.toml`,
 * and read and written by Rust rather than from here. The window's capability is
 * spawning one named sidecar; giving it write access to the home directory in
 * order to save two paths would undo that for a convenience.
 *
 * Not localStorage, either. A list of the folders an agent may touch is a thing a
 * person should be able to read, audit and change without this window running,
 * and browser storage is none of those.
 *
 * Nothing is assumed on a first run. aven does not guess which folders somebody
 * wants it in - the window asks, and asking is the permission model rather than a
 * setup step to get past.
 */

import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";

export async function readRoots(): Promise<string[]> {
  return await invoke<string[]>("roots_read");
}

export async function writeRoots(roots: string[]): Promise<void> {
  await invoke("roots_write", { roots });
}

/**
 * Ask for folders, and return the ones that are new.
 *
 * Returns [] when the picker was dismissed, which is not an error - somebody
 * opened it to look and changed their mind.
 */
export async function pickFolders(already: string[]): Promise<string[]> {
  const chosen = await open({
    directory: true,
    multiple: true,
    title: "Folders aven may act in",
  });
  if (chosen === null) return [];

  const paths = Array.isArray(chosen) ? chosen : [chosen];
  const seen = new Set(already);
  return paths.filter((path) => !seen.has(path));
}

/** The last segment, for showing a path without the whole path. */
export function leaf(path: string): string {
  const parts = path.replace(/\/+$/, "").split("/");
  return parts[parts.length - 1] || path;
}
