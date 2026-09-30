/**
 * The screen that asks for an API key, when nothing on this machine has one.
 *
 * A window launched from the dock has no shell, so "export ANTHROPIC_API_KEY"
 * is not an instruction anybody can follow from here. It is asked for once and
 * kept where this machine keeps credentials - the login keychain on a Mac,
 * which is the same place Safari keeps its own.
 *
 * The field is a password field and the key is never echoed back afterwards.
 * There is nothing to be gained from showing somebody a secret they already
 * pasted, and screens get shared.
 *
 * It goes to the agent over the protocol rather than being written from here,
 * because the agent is the only part that knows where credentials belong on
 * this platform - and keeping that knowledge in one place is why the window has
 * no filesystem permissions at all.
 */

import { useState } from "react";

interface Props {
  /** Where it will be kept, in the agent's own words. */
  keeps: string;
  onSave: (key: string) => Promise<void>;
}

export function NeedsKey({ keeps, onSave }: Props) {
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [trouble, setTrouble] = useState<string | null>(null);

  const save = async () => {
    const trimmed = key.trim();
    if (!trimmed || saving) return;
    setSaving(true);
    setTrouble(null);
    try {
      await onSave(trimmed);
    } catch (problem) {
      setTrouble(problem instanceof Error ? problem.message : String(problem));
      setSaving(false);
    }
  };

  return (
    <div className="pane">
      <div className="firstrun">
        <h2>aven needs an API key</h2>
        <p>
          It talks to Anthropic's models on your behalf, so it needs a key of
          your own. Make one at <code>console.anthropic.com</code> → API keys.
        </p>

        <input
          className="secret"
          type="password"
          value={key}
          autoFocus
          spellCheck={false}
          placeholder="sk-ant-..."
          onChange={(event) => setKey(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") save();
          }}
        />

        <p className="quiet">
          {keeps
            ? `Kept in ${keeps}. It never reaches the conversation or a session file.`
            : "Kept where this machine keeps credentials. It never reaches the conversation or a session file."}
        </p>

        {trouble && <div className="meta warn">⚠ {trouble}</div>}

        <button
          className="pill go"
          onClick={save}
          disabled={!key.trim() || saving}
        >
          {saving ? "Saving…" : "Save and start"}
        </button>

        <p className="quiet">
          Prefer the shell? Export <code>ANTHROPIC_API_KEY</code> before
          launching and this screen will not appear — an exported key always
          wins over a stored one.
        </p>
      </div>
    </div>
  );
}

/**
 * The same thing as a row in Connections, for a key that is already set.
 *
 * The first-run screen is not enough on its own. A key gets rotated, revoked,
 * or pasted with a character missing, and a setup screen that only ever appears
 * once leaves no way to find out or put it right - the only remedy would be
 * editing a keychain by hand.
 *
 * Never shows the key back, not even masked. A row of dots proves nothing about
 * which key is there, and the honest thing to report is where it is kept, which
 * is something the person can go and check for themselves.
 */
export function KeyRow({
  keeps,
  from,
  onSave,
}: {
  keeps: string;
  /** Where the key in use came from, so Replace cannot silently do nothing. */
  from: "environment" | "keychain" | "";
  onSave: (key: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [trouble, setTrouble] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const save = async () => {
    const trimmed = key.trim();
    if (!trimmed || saving) return;
    setSaving(true);
    setTrouble(null);
    try {
      await onSave(trimmed);
      setKey("");
      setOpen(false);
      setDone(true);
    } catch (problem) {
      setTrouble(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card">
      <div className="connector">
        <div className="body">
          <div className="name">
            Anthropic API key
            <span className="tag standing">
              {from === "environment" ? "from the shell" : "set"}
            </span>
          </div>
          <div className="about">
            What aven talks to the models with. It is never shown back and never
            reaches the conversation.
          </div>

          {/*
           * An exported key wins, which is right: somebody who exported one
           * meant that one. The reverse surprise is worse for being invisible -
           * replace the key here, and a months-old export goes on being used
           * while the button appears to have worked.
           */}
          {from === "environment" ? (
            <div className="meta warn">
              ⚠ An exported <code>ANTHROPIC_API_KEY</code> is in use and wins
              over anything kept here. Replacing the stored key changes nothing
              until you unset it in the shell aven was started from.
            </div>
          ) : (
            keeps && <div className="meta">Kept in {keeps}.</div>
          )}
          {done && (
            <div className="meta">Replaced. The next message uses it.</div>
          )}
          {trouble && <div className="meta warn">⚠ {trouble}</div>}
        </div>
        <button
          className="pill quiet"
          onClick={() => {
            setOpen(!open);
            setDone(false);
          }}
        >
          {open ? "Cancel" : "Replace"}
        </button>
      </div>

      {open && (
        <div className="connector">
          <div className="body">
            <input
              className="secret"
              type="password"
              value={key}
              autoFocus
              spellCheck={false}
              placeholder="sk-ant-..."
              onChange={(event) => setKey(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") save();
              }}
            />
          </div>
          <button
            className="pill go"
            onClick={save}
            disabled={!key.trim() || saving}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      )}
    </div>
  );
}
