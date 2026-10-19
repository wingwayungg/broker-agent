// Markdown to an HTML string for the agent's replies. Pure, so a reply renders to
// the same markup during server rendering and in the browser while it streams.
//
// Hand-rolled for the small surface the system prompt asks for (paragraphs,
// bold/italic/code, lists, GFM tables), plus finance-specific table touches: a
// column of figures right-aligns and signed figures are colored as gains/losses.
// Everything from the model is escaped before any markup is added.

export function escapeHtml(s: string): string {
    return s.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function inlineMd(s: string): string {
    // Same rewrite as normalize_citations in backend/app/tools.py, applied here
    // too because streamed tokens arrive before the server normalizes the reply.
    s = escapeHtml(s).replace(/【(\d+)(?:†[^】]*)?】/g, "[$1]");
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>");
    return s;
}

function splitRow(line: string): string[] {
    return line
        .trim()
        .replace(/^\||\|$/g, "")
        .split("|")
        .map((c) => c.trim());
}

const TABLE_SEP_RE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/;
const NUMERIC_CELL_RE = /^[+\-−]?\$?[+\-−]?[\d,]+(\.\d+)?%?$/;

const cellValue = (cell: string) => cell.replace(/\*\*|\*/g, "").trim();

function signClass(cell: string): string {
    const v = cellValue(cell);
    if (!NUMERIC_CELL_RE.test(v)) return "";
    if (/^\$?\+/.test(v)) return ' class="gain"';
    if (/^\$?[-−]/.test(v)) return ' class="loss"';
    return "";
}

// Columns the model left unaligned (a bare ---) are right-aligned when every
// body cell is a figure, so prices and P&L line up on the decimal.
function tableToHtml(lines: string[]): string {
    const header = splitRow(lines[0]);
    const rows = lines.slice(2).map(splitRow);
    const aligns = splitRow(lines[1]).map((cell, col) => {
        const left = cell.startsWith(":");
        const right = cell.endsWith(":");
        if (left && right) return "center";
        if (right) return "right";
        if (left) return "left";
        const values = rows.map((r) => cellValue(r[col] || "")).filter((v) => v && !/^([-–—]|n\/a)$/i.test(v));
        return values.length && values.every((v) => NUMERIC_CELL_RE.test(v)) ? "right" : "left";
    });
    const head = header.map((h, i) => `<th style="text-align:${aligns[i] || "left"}">${inlineMd(h)}</th>`).join("");
    const body = rows
        .map(
            (row) =>
                "<tr>" +
                row.map((c, i) => `<td${signClass(c)} style="text-align:${aligns[i] || "left"}">${inlineMd(c)}</td>`).join("") +
                "</tr>",
        )
        .join("");
    return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

export function renderMarkdown(text: string): string {
    // Strip ``` fence delimiters rather than rendering a code block: models
    // sometimes wrap a markdown table in a ```markdown fence, and it should
    // still render as a table.
    const lines = text.split("\n").filter((l) => !/^\s*```(\w*)?\s*$/.test(l));
    const isListLine = (l: string) => /^\s*[-*]\s+/.test(l);
    const isOrderedLine = (l: string) => /^\s*\d+\.\s+/.test(l);
    const isTableStart = (idx: number) =>
        lines[idx].trim().startsWith("|") && idx + 1 < lines.length && TABLE_SEP_RE.test(lines[idx + 1]);

    let html = "";
    let i = 0;
    const takeWhile = (pred: (l: string) => boolean) => {
        const taken: string[] = [];
        while (i < lines.length && pred(lines[i])) taken.push(lines[i++]);
        return taken;
    };

    while (i < lines.length) {
        if (lines[i].trim() === "") {
            i++;
        } else if (isTableStart(i)) {
            const head = [lines[i], lines[i + 1]];
            i += 2;
            html += tableToHtml([...head, ...takeWhile((l) => l.trim().startsWith("|"))]);
        } else if (isListLine(lines[i])) {
            const items = takeWhile(isListLine).map((l) => `<li>${inlineMd(l.replace(/^\s*[-*]\s+/, ""))}</li>`);
            html += `<ul>${items.join("")}</ul>`;
        } else if (isOrderedLine(lines[i])) {
            const items = takeWhile(isOrderedLine).map((l) => `<li>${inlineMd(l.replace(/^\s*\d+\.\s+/, ""))}</li>`);
            html += `<ol>${items.join("")}</ol>`;
        } else {
            const para: string[] = [];
            while (i < lines.length && lines[i].trim() !== "" && !isTableStart(i) && !isListLine(lines[i]) && !isOrderedLine(lines[i])) {
                para.push(lines[i++]);
            }
            html += `<p>${para.map(inlineMd).join("<br>")}</p>`;
        }
    }
    return html;
}
