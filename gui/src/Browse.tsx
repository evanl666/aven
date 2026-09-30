/**
 * Finding something to connect, in the Connections pane.
 *
 * Two things are on every row on purpose, and neither is decoration.
 *
 * **Who published it.** The registry verifies namespace ownership, so
 * `com.stripe/mcp` really is stripe.com and `io.github.someone/stripe-helper`
 * really is not. Both come back from a search for "stripe". A row that showed
 * only a friendly title would hide the only difference that matters.
 *
 * **What it would run.** Adding a connector means letting aven start somebody
 * else's program, or send a credential to an address. That belongs beside the
 * button rather than behind an icon, in the same words the config file will
 * use.
 *
 * Nothing here says whether a server is safe, because nothing here knows. The
 * registry checks who owns a name; it does not read the code. Which is why
 * adding one changes nothing on its own: every tool it brings still waits for
 * a decision until you say otherwise.
 */

import { useEffect, useState } from "react";

import type { Listed } from "./wire";

interface Props {
  onBrowse: (query: string) => Promise<{ servers: Listed[]; where: string }>;
  onAdd: (name: string) => Promise<void>;
  /** Names already in play, so a second copy is not offered. */
  taken: string[];
  /** A search to run on mount, for a link that arrives pointing at one. */
  start?: string;
}

export function Browse({ onBrowse, onAdd, taken, start = "" }: Props) {
  const [query, setQuery] = useState(start);
  const [found, setFound] = useState<Listed[] | null>(null);
  const [where, setWhere] = useState("");
  const [looking, setLooking] = useState(false);
  const [adding, setAdding] = useState("");
  const [trouble, setTrouble] = useState<string | null>(null);

  const look = async () => {
    const asked = query.trim();
    if (!asked || looking) return;
    setLooking(true);
    setTrouble(null);
    try {
      const answer = await onBrowse(asked);
      setFound(answer.servers);
      setWhere(answer.where);
    } catch (problem) {
      setTrouble(problem instanceof Error ? problem.message : String(problem));
      setFound(null);
    } finally {
      setLooking(false);
    }
  };

  // Only ever on mount, and only when something was handed in. Re-running on
  // every change would search on each keystroke.
  useEffect(() => {
    if (start.trim()) look();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const add = async (one: Listed) => {
    setAdding(one.name);
    setTrouble(null);
    try {
      await onAdd(one.name);
      // Gone from the list once it is in the one above: offering to add it
      // again would only produce a name clash.
      setFound(
        (before) => before?.filter((row) => row.name !== one.name) ?? null,
      );
    } catch (problem) {
      setTrouble(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setAdding("");
    }
  };

  return (
    <div className="card">
      <div className="entry">
        <div className="body">
          <div className="name">Add a service</div>
          <div className="about">
            Search the public MCP registry. Nothing is contacted until you add
            it, and everything it brings waits for your approval until you say
            otherwise.
          </div>

          <div className="finder">
            <input
              className="seek"
              value={query}
              spellCheck={false}
              placeholder="stripe, calendar, github, a database…"
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") look();
              }}
            />
            <button
              className="pill go"
              onClick={look}
              disabled={!query.trim() || looking}
            >
              {looking ? "Searching…" : "Search"}
            </button>
          </div>

          {trouble && <div className="meta warn">⚠ {trouble}</div>}
          {found !== null && found.length === 0 && (
            <div className="meta">
              Nothing matches that. Try a shorter word — the search reads names
              and descriptions.
            </div>
          )}
          {found !== null && found.length > 0 && where && (
            <div className="meta">
              {found.length} found ({where})
            </div>
          )}
        </div>
      </div>

      {(found ?? []).map((one) => (
        <Row
          key={one.name}
          one={one}
          busy={adding === one.name}
          already={taken.includes(one.suggested)}
          onAdd={() => add(one)}
        />
      ))}
    </div>
  );
}

function Row({
  one,
  busy,
  already,
  onAdd,
}: {
  one: Listed;
  busy: boolean;
  already: boolean;
  onAdd: () => void;
}) {
  return (
    <div className="connector">
      <div className="body">
        <div className="name">{one.name}</div>
        {/*
         * The publisher gets its own line and is never abbreviated. It is the
         * one thing on this row that a person can actually reason about.
         */}
        <div className="who">published by {one.publisher}</div>
        {one.description && <div className="about">{one.description}</div>}
        <div className="tools">
          {one.remote ? "talks to " : "runs "}
          {one.runs}
        </div>
        {one.needs.length > 0 && (
          <div className="meta">
            Needs {one.needs.join(", ")} — add them under{" "}
            <code>[mcp.{one.suggested}.env]</code> before connecting.
          </div>
        )}
      </div>
      <button
        className={`pill${already ? "" : " go"}`}
        onClick={onAdd}
        disabled={busy || already}
        title={
          already
            ? "something is already called that"
            : "writes it into your connectors file"
        }
      >
        {already ? "Already added" : busy ? "Adding…" : "Add"}
      </button>
    </div>
  );
}
