/**
 * The protocol, as types.
 *
 * Mirrors aven/wire/protocol.py. That file is the source of truth and this one
 * is a transcription, so the names here are not free to drift: if a field is
 * renamed there it has to be renamed here, and `version` is how a mismatch
 * announces itself rather than turning into `undefined` three panes deep.
 */

export const EXPECTS_VERSION = 1;

/** What a call would do, in a shape richer than one line. */
export type Detail =
  | { kind: "diff"; path: string; before: string; after: string }
  | { kind: "body"; title: string; text: string }
  | { kind: "moves"; pairs: [string, string][] }
  | {
      kind: "order";
      items: [string, string][];
      total: string;
      where: string;
      account: string;
      arrives: string;
    };

export type Risk = "read" | "reversible" | "irreversible";
export type EntryState =
  | "pending"
  | "applied"
  | "committed"
  | "discarded"
  | "failed";

/** One thing that changed the world, or wants to. */
export interface Entry {
  id: string;
  tool: string;
  preview: string;
  detail: Detail | null;
  risk: Risk;
  state: EntryState;
  output: string;
  ts: number;
  /** Set when a standing approval let this run without being asked. */
  approved_by: string | null;
  can_undo: boolean;
}

export interface Tray {
  pending: Entry[];
  undoable: Entry[];
}

/** A tool group. Connecting a service is bringing one in. */
export interface Connector {
  name: string;
  about: string;
  connected: boolean;
  tools: string[];
}

export interface SessionInfo {
  path: string;
  name: string | null;
  messages: number;
  branches: number;
  head: string | null;
}

export interface State {
  version: number;
  session: SessionInfo;
  busy: boolean;
  queued: number;
  tools: string[];
  connectors: Connector[];
  tray: Tray;
  standing: string[];
  usage: string;
}

export interface StoredMessage {
  kind: "user" | "assistant" | "tool_result" | "summary" | "note" | "meta";
  id: string;
  text?: string;
  source?: string;
  tool_name?: string;
  output?: string;
  is_error?: boolean;
  tool_calls?: { id: string; name: string; args: Record<string, unknown> }[];
}

export interface Call {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

/**
 * Everything the agent writes that is not a reply to a command.
 *
 * `settled` is the one to watch rather than `agent_end`: a follow-up in the
 * steering queue carries a run past agent_end, so re-enabling an input box on
 * agent_end re-enables it mid-run.
 */
export type Event =
  | { type: "agent_start"; prompt: string }
  | { type: "turn_start"; turn: number }
  | { type: "message_delta"; text: string }
  | { type: "message_end"; message: StoredMessage }
  | { type: "tool_start"; call: Call }
  | {
      type: "tool_end";
      call: Call;
      result: StoredMessage;
      detail: Detail | null;
      /** True means it did NOT happen: it is in the tray, waiting. */
      staged: boolean;
    }
  | { type: "turn_end"; turn: number; message: StoredMessage }
  | { type: "agent_end"; reason: "end_turn" | "max_turns" | "truncated" }
  | { type: "tray"; pending: Entry[]; undoable: Entry[] }
  | { type: "interrupted" }
  | { type: "failed"; error: string }
  | { type: "settled"; busy: false };

export interface Response {
  type: "response";
  id?: string;
  command: string;
  ok: boolean;
  data?: any;
  error?: string;
}

export type Record_ = Response | Event;
