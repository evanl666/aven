/**
 * Choose what it can reach.
 *
 * A connector is a tool group, and connecting one is bringing that group into
 * play. The mechanism was built for a different reason - every tool's schema
 * rides in every request, so holding the unused ones back saved about 40% of the
 * per-turn cost - and it turns out to be exactly the right shape for this: a
 * service that is off is not a greyed-out button, it is a set of tools the model
 * cannot see and therefore cannot call.
 *
 * Two different questions are drawn here, and conflating them is the thing to
 * avoid. `state` is whether a service could work at all - signed in, or with
 * nothing to sign in to - and it outlives the conversation. `connected` is
 * whether its tools are in front of the model right now, which is per
 * conversation. A row can say "signed in" and still offer Connect.
 *
 * Signing in is not a round trip. Somebody has to read a consent screen in a
 * browser, so the row goes to "Opening your browser…" and waits for an event
 * rather than for a reply.
 *
 * The tool names are listed under each one on purpose. Somebody deciding whether
 * to connect something is entitled to know what it would then be able to do, and
 * "Connect Calendar" without that is a request to trust a word. A service that
 * runs its tools out of process cannot be asked until it is started, so that one
 * says so instead of pretending to a list.
 */

import { Browse } from "./Browse";
import { Folders } from "./Folders";
import { KeyRow } from "./Key";
import type { Connector, Listed } from "./wire";

interface Props {
  connectors: Connector[];
  standing: string[];
  busy: boolean;
  onConnect: (name: string) => void;
  onDisconnect: (name: string) => void;
  /** Where the API key is kept, and how to replace it. */
  keeps: string;
  onKey: (key: string) => Promise<void>;
  onBrowse: (query: string) => Promise<{ servers: Listed[]; where: string }>;
  onAdd: (name: string) => Promise<void>;
  /** A search to run on mount. Only the design harness uses it today. */
  start?: string;
  roots: string[];
  pending: number;
  onAddFolders: () => void;
  onRemoveFolder: (root: string) => void;
}

export function Connections({
  connectors,
  standing,
  busy,
  onConnect,
  onDisconnect,
  keeps,
  onKey,
  onBrowse,
  onAdd,
  start,
  roots,
  pending,
  onAddFolders,
  onRemoveFolder,
}: Props) {
  const on = connectors.filter((connector) => connector.connected).length;

  return (
    <div className="pane">
      <header>
        <h2>Connections</h2>
        <p>
          {roots.length} folder{roots.length === 1 ? "" : "s"}
          {connectors.length > 0 &&
            ` · ${on} of ${connectors.length} connected`}
        </p>
      </header>

      <div className="scroll">
        <div className="column">
          <Folders
            roots={roots}
            pending={pending}
            busy={busy}
            onAdd={onAddFolders}
            onRemove={onRemoveFolder}
          />

          <KeyRow keeps={keeps} onSave={onKey} />

          {connectors.length === 0 && (
            <div className="card empty">
              Nothing to connect yet. Services go in{" "}
              <code>~/.aven/connectors.toml</code> — a Google account, or any
              MCP server.
            </div>
          )}

          {connectors.length > 0 && (
            <div className="card">
              {connectors.map((connector) => (
                <Row
                  key={connector.name}
                  connector={connector}
                  busy={busy}
                  onConnect={onConnect}
                  onDisconnect={onDisconnect}
                />
              ))}
            </div>
          )}

          <Browse
            onBrowse={onBrowse}
            onAdd={onAdd}
            taken={connectors.map((c) => c.name)}
            start={start}
          />

          {standing.length > 0 && (
            <div className="card">
              <div className="entry">
                <div className="body">
                  <div className="name">Standing approvals</div>
                  <div className="about">
                    These let work happen without being asked. Edit{" "}
                    <code>~/.aven/approvals.toml</code> to change them; delete a
                    line to take one back.
                  </div>
                  {standing.map((approval, at) => (
                    <div className="tools" key={at}>
                      {approval}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({
  connector,
  busy,
  onConnect,
  onDisconnect,
}: {
  connector: Connector;
  busy: boolean;
  onConnect: (name: string) => void;
  onDisconnect: (name: string) => void;
}) {
  const signing = connector.state === "signing_in";
  const needs = connector.state === "needs_sign_in";

  return (
    <div className="connector">
      <div className="body">
        <div className="name">
          {connector.name}
          {connector.connected && (
            <span className="tag reversible">in play</span>
          )}
          {connector.state === "ready" && connector.keeps && (
            <span className="tag standing">signed in</span>
          )}
        </div>
        <div className="about">{connector.about}</div>

        {connector.tools.length > 0 ? (
          <div className="tools">{connector.tools.join(" · ")}</div>
        ) : (
          !needs && (
            <div className="tools">
              its tools are listed once it is connected
            </div>
          )
        )}

        {/* Where the token goes, on the screen that asks for it rather than in
            the documentation. */}
        {needs && connector.keeps && (
          <div className="meta">
            Signing in keeps a token in {connector.keeps}.
          </div>
        )}
        {connector.trouble && (
          <div className="meta warn">⚠ {connector.trouble}</div>
        )}
      </div>

      <div className="choices">
        <button
          className={`pill${connector.connected || signing ? "" : " go"}`}
          disabled={connector.connected || signing || busy}
          onClick={() => onConnect(connector.name)}
          title={
            needs
              ? "opens your browser to sign in"
              : "brings these tools into play"
          }
        >
          {signing
            ? "Opening your browser…"
            : connector.connected
              ? "Connected"
              : needs
                ? "Sign in"
                : "Connect"}
        </button>

        {/* Only where there is a credential to throw away right now. A local
            service has none, and one still opening a browser has not got one
            yet. */}
        {connector.keeps && connector.state === "ready" && (
          <button
            className="pill quiet"
            disabled={busy || signing}
            onClick={() => onDisconnect(connector.name)}
            title="forgets the credential and takes the tools out of play"
          >
            Sign out
          </button>
        )}
      </div>
    </div>
  );
}
