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
