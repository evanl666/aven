/**
 * The shell, and the one place events become interface state.
 *
 * Every pane below is a pure function of what it is handed. All the translating
 * happens here, in one reducer-ish handler, so there is a single place to read
 * when the window and the agent disagree about what is going on.
 *
 * The event stream is append-only and the interface is not, which is where the
 * work is: `message_delta` has to find the bubble it belongs to, `tool_end` has
 * to find the row `tool_start` created, and `tray` replaces wholesale because
 * the agent sends the whole tray rather than a patch.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Approvals } from "./Approvals";
import { Chat, type Bubble, type Unstamped } from "./Chat";
import { Connections } from "./Connections";
import { FirstRun } from "./Folders";
import { ChatIcon, ConnectionsIcon, WaitingIcon } from "./Icons";
import * as agent from "./agent";
import { pickFolders, readRoots, writeRoots } from "./roots";
import {
  EXPECTS_VERSION,
  type Connector,
  type State,
  type Tray,
  type Usage,
} from "./wire";
import "./styles.css";

type Pane = "chat" | "waiting" | "connections";

/**
 * A token count at sidebar width.
 *
 * Rounded hard on purpose. The exact figure is a hover away in the tooltip; what
 * belongs in the corner of the eye is the order of magnitude, which is the part
 * that tells you a conversation has got expensive.
 */
function brief(tokens: number): string {
  if (tokens < 1000) return String(tokens);
  if (tokens < 1_000_000)
    return `${(tokens / 1000).toFixed(tokens < 10_000 ? 1 : 0)}k`;
  return `${(tokens / 1_000_000).toFixed(1)}M`;
}

export default function App() {
  const [pane, setPane] = useState<Pane>("chat");
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [tray, setTray] = useState<Tray>({ pending: [], undoable: [] });
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [standing, setStanding] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [queued, setQueued] = useState(0);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [trouble, setTrouble] = useState<string | null>(null);

  /**
   * The folders aven may act in. `null` means "not read yet" and `[]` means
   * "read, and there are none" - which is a first run and a different screen.
   * Collapsing the two would flash the setup screen on every launch.
   */
  const [roots, setRoots] = useState<string[] | null>(null);

  // Whether the assistant is mid-sentence. A streamed reply arrives as many
  // deltas and one final message, so the deltas append to a bubble that has to
  // already be there.
  const streaming = useRef(false);

  /**
   * Add a bubble, stamped with when it arrived.
   *
   * Stamped here rather than carried on the wire: this is when the window saw
   * it, which is what a reader of the transcript is actually asking about.
   */
  const say = useCallback((bubble: Unstamped) => {
    setBubbles((before) => [
      ...before,
      { ...bubble, at: Date.now() } as Bubble,
    ]);
  }, []);

  const absorb = useCallback((state: State) => {
    if (state.version !== EXPECTS_VERSION) {
      setTrouble(
        `This window speaks protocol ${EXPECTS_VERSION} and aven speaks ` +
          `${state.version}. One of them needs updating.`,
      );
    }
    setTray(state.tray);
    setConnectors(state.connectors);
    setStanding(state.standing);
    setBusy(state.busy);
    setQueued(state.queued);
    setUsage(state.usage);
  }, []);

  // --- wire up once --------------------------------------------------------

  useEffect(() => {
    let alive = true;

    const stopListening = agent.onEvent((event) => {
      switch (event.type) {
        case "message_delta":
          setBubbles((before) => {
            if (streaming.current) {
              const last = before[before.length - 1];
              if (last?.kind === "theirs")
                return [
                  ...before.slice(0, -1),
                  { kind: "theirs", text: last.text + event.text, at: last.at },
                ];
            }
            streaming.current = true;
            return [
              ...before,
              { kind: "theirs", text: event.text, at: Date.now() },
            ];
          });
          break;

        case "message_end":
          if (event.message.kind === "assistant") {
            // A model that streams has already filled the bubble; one that does
            // not gets its whole reply here. Doing both would print it twice.
            if (streaming.current) streaming.current = false;
            else if (event.message.text)
              say({ kind: "theirs", text: event.message.text });
          }
          if (event.message.kind === "summary")
            say({
              kind: "notice",
              text: "The conversation got long, so the earlier part is now a summary. Every word is still in the session file.",
            });
          break;

        case "tool_start":
          streaming.current = false;
          say({
            kind: "tool",
            call: event.call,
            done: false,
            staged: false,
            failed: false,
            detail: null,
          });
          break;

        case "tool_end":
          setBubbles((before) => {
            const at = before.findLastIndex(
              (bubble) =>
                bubble.kind === "tool" && bubble.call.id === event.call.id,
            );
            if (at === -1) return before;
            const next = [...before];
            next[at] = {
              kind: "tool",
              call: event.call,
              at: before[at].at,
              done: true,
              staged: event.staged,
              failed: Boolean(event.result.is_error),
              detail: event.detail,
            };
            return next;
          });
          break;

        // Pushed after every tool call, so the pane never has to poll.
        case "tray":
          setTray({ pending: event.pending, undoable: event.undoable });
          break;

        case "agent_end":
          if (event.reason === "max_turns")
            say({ kind: "notice", text: "Hit the turn limit; not finished." });
          if (event.reason === "truncated")
            say({
              kind: "notice",
              text: "The reply was cut off. Ask it to finish, or split the task up.",
            });
          break;

        case "interrupted":
          say({
            kind: "notice",
            text: "Stopped. Anything undoable is still in Waiting for you.",
          });
          break;

        case "failed":
          say({ kind: "notice", text: event.error, bad: true });
          break;

        // Not agent_end. A follow-up carries a run past that.
        case "settled":
          streaming.current = false;
          setBusy(false);
          setQueued(0);
          break;
      }
    });

    const stopWatchingForDeath = agent.onGone((code) => {
      setBusy(false);
      setTrouble(`aven stopped (exit ${code ?? "?"}). Restart the window.`);
    });

    (async () => {
      try {
        await agent.attach();
        const configured = await readRoots();
        if (!alive) return;
        setRoots(configured);
        // Nothing configured is a first run. Starting the agent with no folders
        // would default it to the current directory, which for a bundled app is
        // wherever the launcher happened to be - never what anybody meant.
        if (configured.length === 0) return;

        await agent.start(configured);
        const reply = await agent.send({ type: "state" });
        if (alive) absorb(reply.data as State);
      } catch (problem) {
        if (alive)
          setTrouble(
            problem instanceof Error ? problem.message : String(problem),
          );
      }
    })();

    return () => {
      alive = false;
      stopListening();
      stopWatchingForDeath();
    };
  }, [say, absorb]);

  // --- what the panes ask for ---------------------------------------------

  const onSay = async (text: string) => {
    try {
      const reply = await agent.send({ type: "prompt", message: text });
      const disposition = reply.data?.disposition;
      say({ kind: "mine", text, queued: disposition === "queued" });
      if (disposition === "queued") setQueued(reply.data.queued ?? queued + 1);
      else setBusy(true);
    } catch (problem) {
      say({
        kind: "notice",
        text: problem instanceof Error ? problem.message : String(problem),
        bad: true,
      });
    }
  };

  const onInterrupt = async () => {
    const reply = await agent.send({ type: "interrupt" });
    // What was queued was never read, so it comes back rather than vanishing
    // with the run that would have read it.
    for (const text of (reply.data?.returned ?? []) as string[])
      say({ kind: "notice", text: `Not sent: ${text}` });
    setQueued(0);
  };

  const onApprove = async (ids: string[]) => {
    const reply = await agent.send({ type: "approve", ids });
    setTray(reply.data.tray);
    const done = reply.data.committed?.length ?? 0;
    say({ kind: "notice", text: `Approved ${done}.` });
    for (const entry of reply.data.failed ?? [])
      say({
        kind: "notice",
        text: `${entry.preview} failed: ${entry.output}`,
        bad: true,
      });
  };

  const onDiscard = async () => {
    const reply = await agent.send({ type: "discard" });
    setTray(reply.data.tray);
    say({
      kind: "notice",
      text: `Discarded ${reply.data.discarded}; they never happened.`,
    });
  };

  const onUndo = async () => {
    const reply = await agent.send({ type: "undo" });
    setTray(reply.data.tray);
    say({
      kind: "notice",
      text: `Undid ${reply.data.undone}, and the conversation went back with it.`,
    });
  };

  /**
   * Save the folders and (re)start the agent on them.
   *
   * `--root` is a startup argument, so a change means a new process - and a new
   * process means a new tray. The caller checks for pending work first; this is
   * the plumbing, not the guard.
   */
  const applyRoots = async (next: string[]) => {
    try {
      await writeRoots(next);
      setRoots(next);

      const running = roots !== null && roots.length > 0;
      if (running) {
        await agent.stop();
        // The tray, the connectors and the transcript all belonged to the
        // process that just left. Saying so beats letting stale panes sit there.
        setTray({ pending: [], undoable: [] });
        setBubbles([]);
        say({
          kind: "notice",
          text: "Folders changed, so the agent restarted. This conversation starts fresh.",
        });
      }

      await agent.start(next);
      const reply = await agent.send({ type: "state" });
      absorb(reply.data as State);
      setTrouble(null);
    } catch (problem) {
      setTrouble(problem instanceof Error ? problem.message : String(problem));
    }
  };

  const onAddFolders = async () => {
    const chosen = await pickFolders(roots ?? []);
    if (chosen.length === 0) return;
    await applyRoots([...(roots ?? []), ...chosen]);
  };

  const onRemoveFolder = async (root: string) => {
    const next = (roots ?? []).filter((kept) => kept !== root);
    if (next.length === 0) return; // at least one, always
    await applyRoots(next);
  };

  const onConnect = async (name: string) => {
    const reply = await agent.send({ type: "connect", group: name });
    setConnectors(reply.data.connectors);
  };

  // Still reading the config. A flash of the wrong screen is worse than a beat
  // of nothing.
  if (roots === null) return <div className="shell" />;

  if (roots.length === 0) {
    return (
      <div className="shell">
        <nav className="rail" />
        <FirstRun onAdd={onAddFolders} />
      </div>
    );
  }

  return (
    <div className="shell">
      <nav className="rail">
        <button
          aria-current={pane === "chat"}
          onClick={() => setPane("chat")}
          title="Chat"
        >
          <ChatIcon />
        </button>
        <button
          aria-current={pane === "waiting"}
          onClick={() => setPane("waiting")}
          title="Waiting for you"
        >
          <WaitingIcon />
          {tray.pending.length > 0 && (
            <span className="count">{tray.pending.length}</span>
          )}
        </button>
        <button
          aria-current={pane === "connections"}
          onClick={() => setPane("connections")}
          title="Connections"
        >
          <ConnectionsIcon />
        </button>
        {usage !== null && usage.requests > 0 && (
          <div className="foot" title={usage.line}>
            {brief(usage.input + usage.output)}
          </div>
        )}
      </nav>

      {trouble && <div className="notice bad">{trouble}</div>}

      {pane === "chat" && (
        <Chat
          bubbles={bubbles}
          busy={busy}
          queued={queued}
          onSay={onSay}
          onInterrupt={onInterrupt}
        />
      )}
      {pane === "waiting" && (
        <Approvals
          tray={tray}
          busy={busy}
          onApprove={onApprove}
          onDiscard={onDiscard}
          onUndo={onUndo}
        />
      )}
      {pane === "connections" && (
        <Connections
          connectors={connectors}
          standing={standing}
          busy={busy}
          onConnect={onConnect}
          roots={roots}
          pending={tray.pending.length}
          onAddFolders={onAddFolders}
          onRemoveFolder={onRemoveFolder}
        />
      )}
    </div>
  );
}
