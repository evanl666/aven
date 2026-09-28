/**
 * The client half of the protocol.
 *
 * The one thing in here that is not obvious, and that a naive client gets
 * wrong: **a response is not ordered against the events.** aven starts a run
 * before writing the response that says it started - so that "started" is true
 * when it says so, and an interrupt arriving next cancels something that
 * actually began. Which means `agent_start` can reach this file ahead of the
 * response to the `prompt` that caused it.
 *
 * So every command carries an id and waits on a promise keyed by it. Reading
 * "the next line" would eventually resolve a prompt with somebody else's
 * answer, and it would do it intermittently, which is the worst kind.
 */

import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

import type { Event, Record_, Response } from "./wire";

type Waiting = { resolve: (r: Response) => void; reject: (e: Error) => void };

const waiting = new Map<string, Waiting>();
const listeners = new Set<(event: Event) => void>();
const goneListeners = new Set<(code: number | null) => void>();

let sequence = 0;
let wired = false;

/** Start listening. Safe to call more than once; only the first one wires up. */
export async function attach(): Promise<void> {
  if (wired) return;
  wired = true;

  await listen<string>("aven", ({ payload }) => {
    let record: Record_;
    try {
      record = JSON.parse(payload);
    } catch {
      // A line we cannot parse is a bug in the transport, not in the agent.
      // Losing it loudly beats crashing the window.
      console.error("unparseable line from aven:", payload);
      return;
    }

    if (record.type === "response") {
      const held = record.id ? waiting.get(record.id) : undefined;
      if (held) {
        waiting.delete(record.id!);
        record.ok
          ? held.resolve(record)
          : held.reject(new Error(record.error ?? "the command failed"));
      }
      return;
    }

    for (const listener of listeners) listener(record);
  });

  await listen<number | null>("aven-gone", ({ payload }) => {
    // Everything still waiting will never be answered. Failing them is the
    // honest move: a spinner that never stops is worse than an error.
    for (const [, held] of waiting) held.reject(new Error("the agent stopped"));
    waiting.clear();
    for (const listener of goneListeners) listener(payload);
  });
}

/** Spawn the agent. Idempotent on the Rust side. */
export async function start(roots: string[]): Promise<void> {
  await invoke("agent_start", { roots });
}

export async function stop(): Promise<void> {
  await invoke("agent_stop");
}

/** Send one command and wait for its response. */
export function send(command: Record<string, unknown>): Promise<Response> {
  const id = String(++sequence);
  return new Promise<Response>((resolve, reject) => {
    waiting.set(id, { resolve, reject });
    invoke("agent_write", { line: JSON.stringify({ id, ...command }) }).catch(
      (problem) => {
        waiting.delete(id);
        reject(problem instanceof Error ? problem : new Error(String(problem)));
      },
    );
  });
}

/** Subscribe to everything that is not a response. Returns an unsubscriber. */
export function onEvent(listener: (event: Event) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function onGone(listener: (code: number | null) => void): () => void {
  goneListeners.add(listener);
  return () => goneListeners.delete(listener);
}
