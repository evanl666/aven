/**
 * The folders aven may act in, and the screen that asks for them.
 *
 * On a first run this is the whole window. That is deliberate rather than a
 * placeholder: the first thing the product does is ask what it may touch, which
 * is the same sentence as its reason for existing. A setup step you click past
 * would be a worse version of the same information.
 *
 * Changing the list restarts the agent, because `--root` is a startup argument.
 * Restarting throws away the tray with it, so the list refuses to change while
 * anything is waiting on a decision - losing somebody's pending approvals to a
 * settings change would be the exact failure this product is arranged against.
 */

import { leaf } from "./roots";

interface Props {
  roots: string[];
  /** Blocks changes: restarting the agent would discard these. */
  pending: number;
  busy: boolean;
  onAdd: () => void;
  onRemove: (root: string) => void;
}

export function FirstRun({ onAdd }: { onAdd: () => void }) {
  return (
    <div className="pane">
      <div className="firstrun">
        <h2>Which folders may aven act in?</h2>
        <p>
          It can read and change files inside the folders you choose, and nothing
          outside them — <code>../</code>, <code>~/.ssh</code> and symlinks
          pointing out are refused by the tools themselves, not by asking the
          model nicely.
        </p>
        <p className="quiet">
          The first folder you choose is the working folder. You can change all of
          this later, here or by editing <code>~/.aven/desktop.toml</code>.
        </p>
        <button className="pill go" onClick={onAdd}>
          Choose folders
        </button>
      </div>
    </div>
  );
}

export function Folders({ roots, pending, busy, onAdd, onRemove }: Props) {
  const frozen = pending > 0 || busy;

  return (
    <div className="card">
      <div className="entry">
        <div className="body">
          <div className="name">Folders</div>
          <div className="about">
            aven may act inside these and nowhere else. The first is the working
            folder — a bare path resolves against it.
          </div>
        </div>
        <button className="pill" onClick={onAdd} disabled={frozen}>
          Add
        </button>
      </div>

      {roots.map((root, at) => (
        <div className="connector" key={root}>
          <div className="body">
            <div className="name">
              {leaf(root)}
              {at === 0 && <span className="tag reversible">working</span>}
            </div>
            <div className="tools">{root}</div>
          </div>
          <button
            className="pill quiet"
            onClick={() => onRemove(root)}
            disabled={frozen || roots.length === 1}
            title={
              roots.length === 1
                ? "aven needs at least one folder"
                : "removes it and restarts the agent"
            }
          >
            Remove
          </button>
        </div>
      ))}

      {frozen && (
        <div className="actions">
          <div className="meta warn">
            {pending > 0
              ? `${pending} decision${pending === 1 ? "" : "s"} waiting. ` +
                "Changing folders restarts the agent, which would discard them — " +
                "approve or discard first."
              : "Running. Changing folders restarts the agent."}
          </div>
        </div>
      )}
    </div>
  );
}
