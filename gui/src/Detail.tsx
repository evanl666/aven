/**
 * What a call would do, drawn.
 *
 * This is the part a terminal cannot do and the reason the window is worth
 * building. "write ledger/2026-09.md" is enough to recognise a call; the two
 * lines going into the file are enough to decide on one. An order is the case a
 * single line is least adequate for, which is why it gets a table with the
 * prices lined up - a total you have to assemble in your head is a total nobody
 * checks.
 *
 * The switch is exhaustive on `kind`, so adding a shape to the protocol makes
 * TypeScript point at this file rather than rendering nothing at runtime.
 */

import type { Detail } from "./wire";

export function DetailCard({ detail }: { detail: Detail }) {
  switch (detail.kind) {
    case "diff":
      return (
        <div className="detail-body">
          {detail.path && <div className="body-title">{detail.path}</div>}
          <pre className="diff">{unified(detail.before, detail.after)}</pre>
        </div>
      );

    case "body":
      return (
        <div className="detail-body">
          {detail.title && <div className="body-title">{detail.title}</div>}
          <pre className="body-text">{detail.text}</pre>
        </div>
      );

    case "moves":
      return (
        <div className="detail-body">
          <table className="order">
            <tbody>
              {detail.pairs.map(([from, to], at) => (
                <tr key={at}>
                  <td>{from}</td>
                  <td className="price">→ {to}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );

    case "order":
      return (
        <div className="detail-body">
          <table className="order">
            <tbody>
              {detail.items.map(([what, price], at) => (
                <tr key={at}>
                  <td>{what}</td>
                  <td className="price">{price}</td>
                </tr>
              ))}
              <tr className="total">
                <td>Total</td>
                <td className="price">{detail.total}</td>
              </tr>
              {(detail.where || detail.account || detail.arrives) && (
                <tr className="about">
                  <td colSpan={2}>
                    {[
                      detail.where && `from ${detail.where}`,
                      detail.account && `as ${detail.account}`,
                      detail.arrives && `arrives ${detail.arrives}`,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      );
  }
}

/**
 * The lines that differ, rather than both sides.
 *
 * A whole-file write sends the old and new contents, which for a small edit are
 * almost the same text - showing both would bury the change. A plain
 * longest-common-subsequence walk is enough here: these are short files, and the
 * cost of a library that does it better is a dependency in the window.
 */
function unified(before: string, after: string): React.ReactNode[] {
  const old = before.length ? before.replace(/\n$/, "").split("\n") : [];
  const now = after.length ? after.replace(/\n$/, "").split("\n") : [];

  // table[i][j] = length of the longest common run of old[i:] and now[j:]
  const table: number[][] = Array.from({ length: old.length + 1 }, () =>
    new Array(now.length + 1).fill(0),
  );
  for (let i = old.length - 1; i >= 0; i--) {
    for (let j = now.length - 1; j >= 0; j--) {
      table[i][j] =
        old[i] === now[j]
          ? table[i + 1][j + 1] + 1
          : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }

  const out: React.ReactNode[] = [];
  let i = 0;
  let j = 0;
  let key = 0;
  let same = 0;

  const keep = (line: string) => {
    // Three lines of context is enough to place a change and little enough that
    // a long file does not become the whole card.
    same++;
    if (same <= 3) out.push(<span key={key++}>{`  ${line}\n`}</span>);
    else if (same === 4)
      out.push(
        <span className="at" key={key++}>
          {"  …\n"}
        </span>,
      );
  };

  while (i < old.length && j < now.length) {
    if (old[i] === now[j]) {
      keep(old[i]);
      i++;
      j++;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      same = 0;
      out.push(
        <span className="cut" key={key++}>{`- ${old[i++]}\n`}</span>,
      );
    } else {
      same = 0;
      out.push(
        <span className="add" key={key++}>{`+ ${now[j++]}\n`}</span>,
      );
    }
  }
  while (i < old.length)
    out.push(<span className="cut" key={key++}>{`- ${old[i++]}\n`}</span>);
  while (j < now.length)
    out.push(<span className="add" key={key++}>{`+ ${now[j++]}\n`}</span>);

  return out.length ? out : [<span key={0}>(no change)</span>];
}
