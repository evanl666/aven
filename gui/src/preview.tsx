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
import { Connections } from "./Connections";
import { NeedsKey } from "./Key";
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
    text: [
      "Found **47 screenshots** spanning March to September.",
      "",
      "I've grouped them by month:",
      "",
      "- `2026-03/` — 12 files",
      "- `2026-05/` — 9 files",
      "- `2026-09/` — 26 files",
      "",
      "Then one thing needs you:",
      "",
      "```sh",
      "rm ~/Downloads/March/*.png    # 12 files, cannot be undone",
      "```",
      "",
      "> Nothing has been deleted yet.",
    ].join("\n"),
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
  {
    kind: "notice",
    text: "Done:\n\u00b7 Move 47 screenshots into month folders\n\u00b7 Wrote notes/september.md",
    at: t - 85e4,
  },
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

const connectors = [
  {
    name: "memory",
    about: "Remember or forget lasting facts about this person.",
    state: "ready",
    connected: true,
    tools: ["remember", "forget"],
    keeps: "",
    trouble: null,
  },
  {
    name: "calendar",
    about: "Read and create calendar events on this Mac.",
    state: "ready",
    connected: false,
    tools: ["list_calendars", "list_events", "create_event"],
    keeps: "",
    trouble: null,
  },
  {
    name: "google",
    about: "Google Calendar: read what is scheduled and add events.",
    state: "needs_sign_in",
    connected: false,
    tools: [],
    keeps: "the macOS login keychain",
    trouble: null,
  },
  {
    name: "drive",
    about: "Google Drive, via an MCP server.",
    state: "signing_in",
    connected: false,
    tools: [],
    keeps: "the macOS login keychain",
    trouble: null,
  },
  {
    name: "notes",
    about: "Tools from the notes MCP server.",
    state: "ready",
    connected: false,
    tools: [],
    keeps: "",
    trouble: null,
  },
  // Signed in, but this conversation has not needed it: the case the two
  // separate fields exist for.
  {
    name: "dropbox",
    about: "Files in Dropbox.",
    state: "ready",
    connected: false,
    tools: [],
    keeps: "the macOS login keychain",
    trouble: null,
  },
  {
    name: "robinhood",
    about: "Positions and orders.",
    state: "needs_sign_in",
    connected: false,
    tools: [],
    keeps: "the macOS login keychain",
    trouble: "you said no",
  },
] as any;

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
        waiting={tray.pending}
        onSay={() => {}}
        onInterrupt={() => {}}
        onApprove={() => {}}
        onDiscard={() => {}}
        onSeeWaiting={() => {}}
      />
    ) : which === "key" ? (
      <NeedsKey keeps="the macOS login keychain" onSave={async () => {}} />
    ) : which === "connections" ? (
      <Connections
        connectors={connectors}
        standing={["write where the preview contains 'notes/'"]}
        busy={false}
        onConnect={() => {}}
        onDisconnect={() => {}}
        keeps="the macOS login keychain"
        onKey={async () => {}}
        onBrowse={async () => ({
          where: "live",
          servers: [
            {
              name: "com.stripe/mcp",
              description:
                "MCP server integrating with Stripe - tools for customers, products, payments, and more.",
              publisher: "stripe.com",
              suggested: "stripe",
              remote: true,
              runs: "https://mcp.stripe.com",
              needs: [],
            },
            {
              name: "io.github.someone/stripe-helper",
              description: "Unofficial helper around the Stripe API.",
              publisher: "github.com/someone",
              suggested: "stripe_helper",
              remote: false,
              runs: "npx -y stripe-helper-mcp",
              needs: ["STRIPE_SECRET_KEY"],
            },
          ],
        })}
        onAdd={async () => {}}
        start="stripe"
        roots={["/Users/evan/Downloads"]}
        pending={2}
        onAddFolders={() => {}}
        onRemoveFolder={() => {}}
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
