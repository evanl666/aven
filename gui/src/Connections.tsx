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
 * The tool names are listed under each one on purpose. Somebody deciding whether
 * to connect something is entitled to know what it would then be able to do, and
 * "Connect Calendar" without that is a request to trust a word.
 *
 * Connecting is one-way within a session: a group comes in and stays. The tool
 * list heads the cached prompt prefix, so bringing one in costs a cache miss for
 * that turn, and letting it be toggled off and on would pay that repeatedly for
 * nothing.
 */

import type { Connector } from "./wire";

interface Props {
  connectors: Connector[];
  standing: string[];
  busy: boolean;
  onConnect: (name: string) => void;
}

export function Connections({
  connectors,
  standing,
  busy,
  onConnect,
}: Props) {
  const on = connectors.filter((connector) => connector.connected).length;

  return (
    <div className="pane">
      <header>
        <h2>Connections</h2>
        <p>
          {connectors.length === 0
            ? "nothing to connect"
            : `${on} of ${connectors.length} connected`}
        </p>
      </header>

      <div className="scroll">
        {connectors.length === 0 && (
          <div className="card empty">
            No connectors in this session. Start aven with <code>--mac</code> to
            offer Calendar, Mail and Spotlight.
          </div>
        )}

        {connectors.length > 0 && (
          <div className="card">
            {connectors.map((connector) => (
              <div className="connector" key={connector.name}>
                <div className="body">
                  <div className="name">{connector.name}</div>
                  <div className="about">{connector.about}</div>
                  <div className="tools">{connector.tools.join(" · ")}</div>
                </div>
                <button
                  className={`pill${connector.connected ? "" : " go"}`}
                  disabled={connector.connected || busy}
                  onClick={() => onConnect(connector.name)}
                  title={
                    connector.connected
                      ? "connected for this session"
                      : "brings these tools into play"
                  }
                >
                  {connector.connected ? "Connected" : "Connect"}
                </button>
              </div>
            ))}
          </div>
        )}

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
  );
}
