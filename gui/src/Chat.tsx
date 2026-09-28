/**
 * The conversation.
 *
 * Laid out as a messaging app: a centred column, filled bubbles with no borders,
 * and one composer holding the attach, dictate and send controls rather than a
 * field with a button beside it. The difference between that and a form is most
 * of whether somebody wants to type in it.
 *
 * Two things here are protocol rather than taste.
 *
 * The input unlocks on `settled`, never on `agent_end`. A follow-up in the
 * steering queue carries a run past agent_end, so unlocking there unlocks
 * mid-run.
 *
 * Typing while busy is not refused. The agent queues it and the response says
 * `queued` rather than `started`, so the line is shown as sent with a note that
 * it goes in next. Refusing would mean the only way to correct a run in flight
 * is to kill it, and killing it throws away work that was fine.
 */

import { useEffect, useRef, useState } from "react";

import { MicIcon, PlusIcon, SendIcon, StopIcon } from "./Icons";
import type { Call, Detail } from "./wire";

export type Bubble =
  | { kind: "mine"; text: string; at: number; queued?: boolean }
  | { kind: "theirs"; text: string; at: number }
  | {
      kind: "tool";
      call: Call;
      at: number;
      done: boolean;
      staged: boolean;
      failed: boolean;
      detail: Detail | null;
    }
  | { kind: "notice"; text: string; at: number; bad?: boolean };

/**
 * A bubble before the window stamps it with a time.
 *
 * Spelled out with the conditional rather than `Omit<Bubble, "at">`, because
 * `Omit` on a union collapses the members into one object with only their common
 * keys - so `text` and `call` both stop existing. The `T extends any` makes it
 * distribute over the union instead.
 */
export type Unstamped<T = Bubble> = T extends unknown ? Omit<T, "at"> : never;

interface Props {
  bubbles: Bubble[];
  busy: boolean;
  queued: number;
  onSay: (text: string) => void;
  onInterrupt: () => void;
}

/** Longer than this between messages and the conversation gets a date on it. */
const APART = 10 * 60 * 1000;

export function Chat({ bubbles, busy, queued, onSay, onInterrupt }: Props) {
  const [draft, setDraft] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const foot = useRef<HTMLDivElement>(null);

  useEffect(() => {
    foot.current?.scrollIntoView({ block: "end" });
  }, [bubbles.length, busy]);

  // Grow with the text, up to the cap the stylesheet sets.
  useEffect(() => {
    const field = box.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${field.scrollHeight}px`;
  }, [draft]);

  const say = () => {
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    onSay(text);
  };

  return (
    <div className="pane">
      <div className="scroll">
        <div className="column">
          {bubbles.length === 0 && (
            <div className="empty">
              Ask for something. Anything that cannot be undone will wait for
              you, rather than happening.
            </div>
          )}

          {bubbles.map((bubble, at) => (
            <div className="turn" key={at}>
              {parted(bubbles[at - 1], bubble) && (
                <div className="when">{when(bubble.at)}</div>
              )}

              {bubble.kind === "mine" && (
                <>
                  <div className={`said mine${bubble.queued ? " queued" : ""}`}>
                    {bubble.text}
                  </div>
                  {bubble.queued && (
                    <div className="queued-note">goes in next</div>
                  )}
                </>
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
      </div>

      <div className="composer-wrap">
        <div className="column">
          <div className="composer">
            <button
              className="icon-button"
              title="Attaching files is not wired up yet"
              disabled
            >
              <PlusIcon />
            </button>

            <textarea
              ref={box}
              value={draft}
              rows={1}
              placeholder={busy ? "Message — it goes in next" : "Message"}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                // Enter sends and Shift+Enter is a newline, because a composer
                // that needs a click for every line is one nobody types in.
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  say();
                }
              }}
            />

            <button
              className="icon-button"
              title="Dictation is not wired up yet"
              disabled
            >
              <MicIcon />
            </button>

            {busy ? (
              <button
                className="icon-button send"
                onClick={onInterrupt}
                title="Stop"
              >
                <StopIcon />
              </button>
            ) : (
              <button
                className="icon-button send"
                onClick={say}
                disabled={!draft.trim()}
                title="Send"
              >
                <SendIcon />
              </button>
            )}
          </div>
          {busy && queued > 0 && (
            <div className="queued-note under">{queued} waiting to go in</div>
          )}
        </div>
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
    .map(([key, value]) =>
      typeof value === "string" && value.length > 40
        ? `${key}=<${value.length} chars>`
        : `${key}=${JSON.stringify(value)}`,
    )
    .join(", ");
}

function parted(before: Bubble | undefined, now: Bubble): boolean {
  return before === undefined || now.at - before.at > APART;
}

function when(at: number): string {
  const moment = new Date(at);
  const today = new Date().toDateString() === moment.toDateString();
  const clock = moment.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
  if (today) return clock;
  return `${moment.toLocaleDateString([], { month: "short", day: "numeric" })} at ${clock}`;
}
