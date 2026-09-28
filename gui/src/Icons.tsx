/**
 * Inline SVG, no icon library.
 *
 * Six icons do not justify a dependency, and a library would arrive with a
 * thousand more plus its own sizing conventions to fight. These are drawn on a
 * 24-grid with a 1.6 stroke, which is the weight that stops looking spindly next
 * to 15px system text.
 */

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

function Glyph({ children }: { children: React.ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden {...stroke}>
      {children}
    </svg>
  );
}

export const ChatIcon = () => (
  <Glyph>
    <path d="M20.5 11.5a7.5 7.5 0 0 1-10.9 6.7L4.5 19.5l1.3-4.6A7.5 7.5 0 1 1 20.5 11.5Z" />
  </Glyph>
);

export const WaitingIcon = () => (
  <Glyph>
    <rect x="3.75" y="3.75" width="16.5" height="16.5" rx="5" />
    <path d="m8.5 12.2 2.4 2.4 4.6-5" />
  </Glyph>
);

export const ConnectionsIcon = () => (
  <Glyph>
    <circle cx="7" cy="7" r="3.2" />
    <rect x="13.6" y="3.8" width="6.6" height="6.6" rx="2" />
    <rect x="3.7" y="13.6" width="6.6" height="6.6" rx="2" />
    <circle cx="17" cy="17" r="3.2" />
  </Glyph>
);

export const PlusIcon = () => (
  <Glyph>
    <path d="M12 5.5v13M5.5 12h13" />
  </Glyph>
);

export const MicIcon = () => (
  <Glyph>
    <rect x="9.25" y="2.75" width="5.5" height="11" rx="2.75" />
    <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3.2" />
  </Glyph>
);

export const SendIcon = () => (
  <svg viewBox="0 0 24 24" width="19" height="19" aria-hidden {...stroke}>
    <path d="M12 19V5.5M6 11.5 12 5l6 6.5" />
  </svg>
);

export const StopIcon = () => (
  <svg viewBox="0 0 24 24" width="19" height="19" aria-hidden>
    <rect x="7.5" y="7.5" width="9" height="9" rx="2" fill="currentColor" />
  </svg>
);
