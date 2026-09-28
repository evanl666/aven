/**
 * Run the Tauri CLI with cargo on PATH.
 *
 * rustup installs cargo to ~/.cargo/bin and is supposed to add that to your
 * shell profile. It does not always manage it - a non-interactive install, a
 * profile it could not write, a machine set up by somebody else - and when it
 * has not, the only symptom is this, from a tool that never mentions rustup:
 *
 *     failed to run 'cargo metadata' command to get workspace directory:
 *     No such file or directory (os error 2)
 *
 * Nothing in that names the problem or the fix. So rather than putting a line
 * in the README that everybody finds only after losing ten minutes, the build
 * looks in the standard place itself.
 *
 * Only ever adds; never replaces. A cargo already on PATH is the one that
 * runs, because somebody with two toolchains has chosen between them and this
 * script is not entitled to a view.
 */

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { delimiter, join } from "node:path";

const cargo = join(homedir(), ".cargo", "bin");
const path = process.env.PATH ?? "";
const already = path.split(delimiter).includes(cargo);

const child = spawn("tauri", process.argv.slice(2), {
  stdio: "inherit",
  // npm puts node_modules/.bin on PATH, so `tauri` resolves without a path.
  shell: process.platform === "win32",
  env:
    already || !existsSync(cargo)
      ? process.env
      : { ...process.env, PATH: `${path}${delimiter}${cargo}` },
});

child.on("exit", (code, signal) => {
  // Signals have no exit code. Reporting 1 for a Ctrl-C would make a deliberate
  // stop look like a failed build in whatever ran this.
  if (signal) process.kill(process.pid, signal);
  else process.exit(code ?? 0);
});
