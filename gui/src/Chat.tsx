/**
 * The conversation.
 *
 * Two things here are easy to get wrong and both come from the protocol.
 *
 * The input box is re-enabled on `settled`, never on `agent_end`. A follow-up in
 * the steering queue carries a run past agent_end, so enabling on agent_end
 * enables it mid-run.
 *
 * Typing while busy is not refused. The agent queues it as steering and the
 * response says `queued` rather than `started`, so the line is shown as sent and
 * marked as waiting its turn. Refusing would mean the only way to correct a run
 * in flight is to kill it, and killing it throws away work that was fine.
 */

import { useEffect, useRef, useState } from "react";

import type { Call, Detail } from "./wire";

export type Bubble =
  | { kind: "mine"; text: string; queued?: boolean }
  | { kind: "theirs"; text: string }
  | {
      kind: "tool";
      call: Call;
      done: boolean;
      staged: boolean;
      failed: boolean;
      detail: Detail | null;
    }
  | { kind: "notice"; text: string; bad?: boolean };

interface Props {
  bubbles: Bubble[];
  busy: boolean;
  queued: number;
  onSay: (text: string) => void;
  onInterrupt: () => void;
}

export function Chat({ bubbles, busy, queued, onSay, onInterrupt }: Props) {
  const [draft, setDraft] = useState("");
  const foot = useRef<HTMLDivElement>(null);

  useEffect(() => {
    foot.current?.scrollIntoView({ block: "end" });
  }, [bubbles.length, busy]);

  const say = () => {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    onSay(text);
  };

  return (
    <div className="pane">
      <header>
        <h2>aven</h2>
        <p>
          {busy
            ? queued > 0
              ? `working · ${queued} queued`
              : "working"
            : "ready"}
        </p>
      </header>

      <div className="scroll">
        {bubbles.length === 0 && (
          <div className="card empty">
            Ask for something. Anything that cannot be undone will wait for you
            in Waiting for you.
          </div>
        )}

        {bubbles.map((bubble, at) => (
          <div className="turn" key={at}>
            {bubble.kind === "mine" && (
              <div
                className={`said mine${bubble.queued ? " queued" : ""}`}
                title={bubble.queued ? "queued until this turn finishes" : ""}
              >
                {bubble.text}
              </div>
            )}
            {bubble.kind === "theirs" && (
              <div className="said theirs">{bubble.text}</div>
            )}
            {bubble.kind === "tool" && <ToolRow bubble={bubble} />}
            {bubble.kind === "notice" && (
              <div className={`notice${bubble.bad ? " bad" : ""}`}>
                {bubble.text}
              </div>
            )}
          </div>
        ))}
        <div ref={foot} />
      </div>

      <div className="composer">
        <textarea
          value={draft}
          placeholder={busy ? "say something — it goes in next" : "say something"}
          rows={1}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends, Shift+Enter is a newline. A composer that needs a
            // button click for every line is a composer nobody types in.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              say();
            }
          }}
        />
        {busy ? (
          <button className="pill" onClick={onInterrupt}>
            Stop
          </button>
        ) : (
          <button className="pill go" onClick={say} disabled={!draft.trim()}>
            Send
          </button>
        )}
      </div>
    </div>
  );
}

function ToolRow({ bubble }: { bubble: Extract<Bubble, { kind: "tool" }> }) {
  const mark = bubble.failed
    ? "✗"
    : bubble.staged
      ? "⏸"
      : bubble.done
        ? "✓"
        : "·";
  const state = bubble.failed ? " failed" : bubble.staged ? " staged" : "";
  return (
    <div className={`toolrow${state}`}>
      <span className="mark">{mark}</span>
      <span>
        {bubble.call.name}({brief(bubble.call.args)})
        {bubble.staged && " — waiting for you"}
      </span>
    </div>
  );
}

/**
 * Arguments, with anything long folded away.
 *
 * A file's whole contents arrive as one argument. Printing it would take the row
 * with it, and the length is the only part worth seeing anyway.
 */
function brief(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([key, value]) => {
      if (typeof value === "string" && value.length > 40)
        return `${key}=<${value.length} chars>`;
      return `${key}=${JSON.stringify(value)}`;
    })
    .join(", ");
}
