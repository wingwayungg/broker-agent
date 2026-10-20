import { Fragment, type ReactNode } from "react";

// Markdown to React elements for the agent's replies. Pure, so a reply renders to
// the same markup during server rendering and in the browser while it streams.
//
// Hand-rolled for the small surface the system prompt asks for (paragraphs,
// bold/italic/code, lists, GFM tables), plus finance-specific table touches: a
// column of figures right-aligns and signed figures are colored as gains/losses.
// The model's text only ever becomes React text children, so React escapes it.

// The leftmost marker wins, so `**` inside backticks stays literal and `code`
// inside bold still renders as code.
const INLINE_RE = /`([^`]+)`|\*\*([^*]+)\*\*|(?<!\*)\*([^*]+)\*(?!\*)/g;

function inline(s: string): ReactNode[] {
    const out: ReactNode[] = [];
    let last = 0;
    for (const m of s.matchAll(INLINE_RE)) {
        if (m.index > last) out.push(s.slice(last, m.index));
        if (m[1] !== undefined) out.push(<code key={m.index}>{m[1]}</code>);
        else if (m[2] !== undefined) out.push(<strong key={m.index}>{inline(m[2])}</strong>);
        else out.push(<em key={m.index}>{inline(m[3])}</em>);
        last = m.index + m[0].length;
    }
    if (last < s.length) out.push(s.slice(last));
    return out;
}

// Same rewrite as normalize_citations in backend/app/tools.py, applied here
// too because streamed tokens arrive before the server normalizes the reply.
const inlineMd = (s: string) => inline(s.replace(/【(\d+)(?:†[^】]*)?】/g, "[$1]"));

function splitRow(line: string) {
    return line
        .trim()
        .replace(/^\||\|$/g, "")
        .split("|")
        .map((c) => c.trim());
}

const TABLE_SEP_RE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/;
const NUMERIC_CELL_RE = /^[+\-−]?\$?[+\-−]?[\d,]+(\.\d+)?%?$/;

const cellValue = (cell: string) => cell.replace(/\*\*|\*/g, "").trim();

function signClass(cell: string) {
    const v = cellValue(cell);
    if (!NUMERIC_CELL_RE.test(v)) return undefined;
    if (/^\$?\+/.test(v)) return "gain";
    if (/^\$?[-−]/.test(v)) return "loss";
    return undefined;
}

// Columns the model left unaligned (a bare ---) are right-aligned when every
// body cell is a figure, so prices and P&L line up on the decimal.
function Table({ lines }: { lines: string[] }) {
    const header = splitRow(lines[0]);
    const rows = lines.slice(2).map(splitRow);
    const aligns = splitRow(lines[1]).map((cell, col): "left" | "right" | "center" => {
        const left = cell.startsWith(":");
        const right = cell.endsWith(":");
        if (left && right) return "center";
        if (right) return "right";
        if (left) return "left";
        const values = rows.map((r) => cellValue(r[col] || "")).filter((v) => v && !/^([-–—]|n\/a)$/i.test(v));
        return values.length && values.every((v) => NUMERIC_CELL_RE.test(v)) ? "right" : "left";
    });
    const textAlign = (i: number) => ({ textAlign: aligns[i] || "left" });
    return (
        <div className="table-wrap">
            <table>
                <thead>
                    <tr>
                        {header.map((h, i) => (
                            <th key={i} style={textAlign(i)}>
                                {inlineMd(h)}
                            </th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row, r) => (
                        <tr key={r}>
                            {row.map((c, i) => (
                                <td key={i} className={signClass(c)} style={textAlign(i)}>
                                    {inlineMd(c)}
                                </td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

export function renderMarkdown(text: string) {
    // Strip ``` fence delimiters rather than rendering a code block: models
    // sometimes wrap a markdown table in a ```markdown fence, and it should
    // still render as a table.
    const lines = text.split("\n").filter((l) => !/^\s*```(\w*)?\s*$/.test(l));
    const isListLine = (l: string) => /^\s*[-*]\s+/.test(l);
    const isOrderedLine = (l: string) => /^\s*\d+\.\s+/.test(l);
    const isTableStart = (idx: number) =>
        lines[idx].trim().startsWith("|") && idx + 1 < lines.length && TABLE_SEP_RE.test(lines[idx + 1]);

    const blocks: ReactNode[] = [];
    let i = 0;
    const takeWhile = (pred: (l: string) => boolean) => {
        const taken: string[] = [];
        while (i < lines.length && pred(lines[i])) taken.push(lines[i++]);
        return taken;
    };
    const listItems = (items: string[], marker: RegExp) =>
        items.map((l, j) => <li key={j}>{inlineMd(l.replace(marker, ""))}</li>);

    while (i < lines.length) {
        const key = blocks.length;
        if (lines[i].trim() === "") {
            i++;
        } else if (isTableStart(i)) {
            const head = [lines[i], lines[i + 1]];
            i += 2;
            blocks.push(<Table key={key} lines={[...head, ...takeWhile((l) => l.trim().startsWith("|"))]} />);
        } else if (isListLine(lines[i])) {
            blocks.push(<ul key={key}>{listItems(takeWhile(isListLine), /^\s*[-*]\s+/)}</ul>);
        } else if (isOrderedLine(lines[i])) {
            blocks.push(<ol key={key}>{listItems(takeWhile(isOrderedLine), /^\s*\d+\.\s+/)}</ol>);
        } else {
            const para: string[] = [];
            while (i < lines.length && lines[i].trim() !== "" && !isTableStart(i) && !isListLine(lines[i]) && !isOrderedLine(lines[i])) {
                para.push(lines[i++]);
            }
            blocks.push(
                <p key={key}>
                    {para.map((l, j) => (
                        <Fragment key={j}>
                            {j > 0 && <br />}
                            {inlineMd(l)}
                        </Fragment>
                    ))}
                </p>,
            );
        }
    }
    return blocks;
}
