/**
 * The pane the window exists for.
 *
 * A terminal can offer "commit everything" or "discard everything". A list with
 * a box beside each line can offer neither - and it has to, because a batch
 * holding one purchase is not something anybody should accept whole in order to
 * accept any of it. `approve` takes the ids that are ticked; an empty list
 * approves nothing, which the protocol is careful to distinguish from leaving
 * the ids out entirely.
 *
 * Two lists, because two kinds of thing deserve different treatment. Waiting
 * work has not happened and gets its detail shown without being asked for -
 * reading a line is not deciding. Work that already ran is reversible work,
 * listed so it can be taken back, with no detail because a diff of something
 * done is noise.
 */

import { useState } from "react";

import { DetailCard } from "./Detail";
import type { Entry, Tray } from "./wire";

interface Props {
  tray: Tray;
  busy: boolean;
  onApprove: (ids: string[]) => void;
  onDiscard: () => void;
  onUndo: () => void;
}

export function Approvals({ tray, busy, onApprove, onDiscard, onUndo }: Props) {
  const [ticked, setTicked] = useState<Set<string>>(new Set());

  const waiting = tray.pending;
  const shown = new Set(waiting.map((entry) => entry.id));
  // Anything approved or discarded is gone from the tray; its id must not linger
  // in the selection and reappear against a later entry.
  const live = new Set([...ticked].filter((id) => shown.has(id)));

  const toggle = (id: string) => {
    const next = new Set(live);
    next.has(id) ? next.delete(id) : next.add(id);
    setTicked(next);
  };

  const all = () =>
    setTicked(live.size === waiting.length ? new Set() : new Set(shown));

  return (
    <div className="pane">
      <header>
        <h2>Waiting for you</h2>
        <p>
          {waiting.length === 0
            ? "nothing needs a decision"
            : `${waiting.length} to decide · ${live.size} ticked`}
        </p>
      </header>

      <div className="scroll">
        {waiting.length === 0 && tray.undoable.length === 0 && (
          <div className="card empty">
            Nothing is waiting, and nothing has run that could be taken back.
          </div>
        )}

        {waiting.length > 0 && (
          <div className="card waiting-card">
            {waiting.map((entry) => (
              <label className="entry" key={entry.id}>
                <input
                  type="checkbox"
                  checked={live.has(entry.id)}
                  onChange={() => toggle(entry.id)}
                />
                <div className="body">
                  <Line entry={entry} />
                  {entry.detail && (
                    <div className="detail-wrap">
                      <DetailCard detail={entry.detail} />
                    </div>
                  )}
                </div>
              </label>
            ))}

            <div className="actions">
              <button className="pill quiet" onClick={all}>
                {live.size === waiting.length ? "Untick all" : "Tick all"}
              </button>
              <span className="spacer" />
              <button className="pill quiet" onClick={onDiscard} disabled={busy}>
                Discard all
              </button>
              <button
                className="pill go"
                disabled={busy || live.size === 0}
                onClick={() => onApprove([...live])}
              >
                {live.size === 0
                  ? "Approve"
                  : `Approve ${live.size} of ${waiting.length}`}
              </button>
            </div>
          </div>
        )}

        {tray.undoable.length > 0 && (
          <div className="card">
            {tray.undoable.map((entry) => (
              <div className="entry" key={entry.id}>
                <span className="mark" aria-hidden>
                  ✓
                </span>
                <div className="body">
                  <Line entry={entry} />
                </div>
              </div>
            ))}
            <div className="actions">
              <span className="spacer" />
              <button className="pill" onClick={onUndo} disabled={busy}>
                Undo {tray.undoable.length} · rewinds the conversation too
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * The line, its risk, and whether it was actually decided just now.
 *
 * `approved_by` being visible is not decoration. A standing approval lets work
 * happen without being asked, so the one place it must never be invisible is the
 * list of what happened - otherwise the audit trail depends on which surface you
 * were looking at.
 */
function Line({ entry }: { entry: Entry }) {
  const [line, warning] = split(entry.preview);
  return (
    <>
      <div className="line">
        {line}
        <span className={`tag ${entry.risk}`}>{entry.risk}</span>
        {entry.approved_by && <span className="tag standing">pre-approved</span>}
      </div>
      {warning && <div className="meta warn">⚠ {warning}</div>}
      {entry.approved_by && <div className="meta">by {entry.approved_by}</div>}
      {entry.state === "failed" && (
        <div className="meta warn">failed: {entry.output}</div>
      )}
    </>
  );
}

/**
 * A policy warning arrives appended to the preview with a "⚠" between.
 *
 * Splitting it out rather than printing the joined string, because the warning
 * is the most important thing on the line and reads as an afterthought when it
 * trails the sentence.
 */
function split(preview: string): [string, string | null] {
  const at = preview.indexOf("⚠");
  if (at === -1) return [preview, null];
  return [preview.slice(0, at).trimEnd(), preview.slice(at + 1).trim()];
}
