/**
 * The small part of Markdown a chat reply actually uses.
 *
 * Written by hand rather than reached for from a package, for one reason that
 * matters and one that is merely nice.
 *
 * **It never produces HTML.** Every renderer worth using takes Markdown to an
 * HTML string, which then has to go through `dangerouslySetInnerHTML`, which
 * then has to be sanitised, and the sanitiser is now a security boundary in the
 * middle of a chat window. This returns React elements. A model that emits
 * `<img onerror=...>` gets a paragraph containing that text, because there is
 * no path from here to the DOM parser at all.
 *
 * And it is about a hundred lines against a dependency that brings a parser, a
 * sanitiser and their update treadmill, for headings, bold and code blocks.
 *
 * What is deliberately not supported: tables, footnotes, nested lists, inline
 * HTML. A reply that needs them is a reply that should have been a file.
 */

import type { ReactNode } from "react";

export function Markdown({ text }: { text: string }) {
  return <>{blocks(text)}</>;
}

/**
 * Split into blocks first, because a fenced code block suspends every other
 * rule inside it - a `#` in a shell script is a comment, not a heading.
 */
function blocks(text: string): ReactNode[] {
  const lines = text.split("\n");
  const out: ReactNode[] = [];
  let at = 0;

  while (at < lines.length) {
    const line = lines[at];

    if (line.startsWith("```")) {
      const language = line.slice(3).trim();
      const body: string[] = [];
      at++;
      while (at < lines.length && !lines[at].startsWith("```")) {
        body.push(lines[at]);
        at++;
      }
      at++; // the closing fence, or the end of the text
      out.push(
        <pre key={out.length} className="md-code">
          {language && <span className="md-lang">{language}</span>}
          <code>{body.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      const depth = heading[1].length;
      const Tag = `h${Math.min(depth + 2, 6)}` as "h3" | "h4" | "h5" | "h6";
      out.push(
        <Tag key={out.length} className="md-head">
          {inline(heading[2])}
        </Tag>,
      );
      at++;
      continue;
    }

    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      const numbered = /^\s*\d+\./.test(line);
      const items: string[] = [];
      while (at < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[at])) {
        items.push(lines[at].replace(/^\s*([-*+]|\d+\.)\s+/, ""));
        at++;
      }
      const List = numbered ? "ol" : "ul";
      out.push(
        <List key={out.length} className="md-list">
          {items.map((item, n) => (
            <li key={n}>{inline(item)}</li>
          ))}
        </List>,
      );
      continue;
    }

    if (line.startsWith("> ")) {
      const said: string[] = [];
      while (at < lines.length && lines[at].startsWith("> ")) {
        said.push(lines[at].slice(2));
        at++;
      }
      out.push(
        <blockquote key={out.length} className="md-quote">
          {inline(said.join(" "))}
        </blockquote>,
      );
      continue;
    }

    if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) {
      out.push(<hr key={out.length} className="md-rule" />);
      at++;
      continue;
    }

    if (!line.trim()) {
      at++;
      continue;
    }

    // A paragraph runs until a blank line or the start of another block.
    const said: string[] = [];
    while (
      at < lines.length &&
      lines[at].trim() &&
      !lines[at].startsWith("```") &&
      !lines[at].startsWith("> ") &&
      !/^(#{1,4})\s/.test(lines[at]) &&
      !/^\s*([-*+]|\d+\.)\s+/.test(lines[at])
    ) {
      said.push(lines[at]);
      at++;
    }
    out.push(
      <p key={out.length} className="md-p">
        {inline(said.join("\n"))}
      </p>,
    );
  }

  return out;
}

/**
 * Inline spans, innermost first.
 *
 * Code is matched before everything else and its contents are never looked at
 * again: `**not bold**` inside backticks is four asterisks and two words, which
 * is the whole point of writing it in backticks.
 */
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const pattern =
    /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\n]+\*|_[^_\n]+_)|(\[[^\]]+\]\([^)\s]+\))/g;

  let last = 0;
  let found: RegExpExecArray | null;

  while ((found = pattern.exec(text)) !== null) {
    if (found.index > last) out.push(text.slice(last, found.index));
    const piece = found[0];

    if (piece.startsWith("`")) {
      out.push(
        <code key={out.length} className="md-tick">
          {piece.slice(1, -1)}
        </code>,
      );
    } else if (piece.startsWith("**")) {
      out.push(<strong key={out.length}>{piece.slice(2, -2)}</strong>);
    } else if (piece.startsWith("[")) {
      const split = piece.indexOf("](");
      const label = piece.slice(1, split);
      const href = piece.slice(split + 2, -1);
      // Only the two schemes that mean "a page". A link is the one place a
      // model's output would otherwise become something the browser executes,
      // and `javascript:` is exactly that.
      const safe = /^https?:\/\//i.test(href);
      out.push(
        safe ? (
          <a key={out.length} href={href} target="_blank" rel="noreferrer">
            {label}
          </a>
        ) : (
          <span key={out.length}>
            {label} ({href})
          </span>
        ),
      );
    } else {
      out.push(<em key={out.length}>{piece.slice(1, -1)}</em>);
    }
    last = pattern.lastIndex;
  }

  if (last < text.length) out.push(text.slice(last));
  return out;
}
