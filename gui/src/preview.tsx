/**
 * The panes, drawn from canned data, at `/preview.html?pane=chat|waiting`.
 *
 * A design harness rather than a test. Layout is the one thing a passing type
 * check says nothing about, and the alternative is driving a real agent through
 * a real conversation every time a margin moves - which is slow enough that in
 * practice the margin just doesn't get looked at.
 *
 * Only `index.html` is an entry point, so nothing here reaches a build. The
 * fixtures below are deliberately awkward: a long paragraph, a queued message,
 * a staged tool and an irreversible line, because those are the shapes that
 * break a layout and the ones a happy demo never produces.
 */
import { createRoot } from "react-dom/client";
import { Chat, type Bubble } from "./Chat";
import { Approvals } from "./Approvals";
import { ChatIcon, ConnectionsIcon, WaitingIcon } from "./Icons";
import "./styles.css";

const t = Date.now();
const bubbles: Bubble[] = [
  {
    kind: "mine",
    text: "Tidy up my Downloads folder — group the screenshots by month.",
    at: t - 9e5,
  },
  {
    kind: "theirs",
    text: "I'll look first. Nothing moves until you say so.",
    at: t - 89e4,
  },
  {
    kind: "tool",
    call: { name: "list_dir", args: { path: "~/Downloads" } } as any,
    at: t - 88e4,
    done: true,
    staged: false,
    failed: false,
    detail: null,
  },
  {
    kind: "theirs",
    text: "There are 47 screenshots spanning March to September. I've staged the moves into month folders — 47 files, nothing overwritten. Have a look at the list and approve it when you're happy.",
    at: t - 87e4,
  },
  {
    kind: "tool",
    call: { name: "move_files", args: { count: 47 } } as any,
    at: t - 86e4,
    done: true,
    staged: true,
    failed: false,
    detail: null,
  },
  { kind: "notice", text: "3 things are waiting for you", at: t - 85e4 },
  {
    kind: "mine",
    text: "Also delete the ones from March.",
    at: t - 60,
    queued: true,
  },
];
const tray = {
  pending: [
    {
      id: "1",
      preview: "Move 47 screenshots into month folders",
      risk: "reversible",
      state: "staged",
      detail: null,
      output: "",
      approved_by: null,
    },
    {
      id: "2",
      preview: "Delete 12 files from ~/Downloads/March ⚠ this cannot be undone",
      risk: "irreversible",
      state: "staged",
      detail: null,
      output: "",
      approved_by: null,
    },
  ],
  undoable: [
    {
      id: "3",
      preview: "Wrote notes/september.md",
      risk: "reversible",
      state: "done",
      detail: null,
      output: "",
      approved_by: "standing approval: write notes/**",
    },
  ],
} as any;

const which = new URLSearchParams(location.search).get("pane") ?? "chat";
createRoot(document.getElementById("root")!).render(
  <div className="shell">
    <nav className="rail">
      <button aria-current={which === "chat"}>
        <ChatIcon />
      </button>
      <button aria-current={which === "waiting"}>
        <WaitingIcon />
        <span className="count">2</span>
      </button>
      <button aria-current={which === "connections"}>
        <ConnectionsIcon />
      </button>
      <div className="foot" title="4 requests · in 12400 · out 900">
        13.3k
      </div>
    </nav>
    {which === "chat" ? (
      <Chat
        bubbles={bubbles}
        busy={true}
        queued={1}
        onSay={() => {}}
        onInterrupt={() => {}}
      />
    ) : (
      <Approvals
        tray={tray}
        busy={false}
        onApprove={() => {}}
        onDiscard={() => {}}
        onUndo={() => {}}
      />
    )}
  </div>,
);
