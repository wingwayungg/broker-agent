// Pure functions from text to HTML strings, shared by the page (app.js) and the
// server, which runs this file in V8 to render a thread's history into the
// page it serves (app/frontend.py) -- so both always produce the same markup.

// Minimal GFM-ish markdown renderer for assistant replies: paragraphs,
// bold/italic/code, lists, and (the main point) tables, so a markdown
// table from the model renders as an actual <table> like agentchat's UI
// instead of raw pipe characters. Deliberately hand-rolled rather than a
// CDN markdown library, since this page has no other external script
// dependency and the surface area we need is small.
function escapeHtml(s) {
    return s.replace(/&/g, "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function inlineMd(s) {
    // Same rewrite as normalize_citations in app/tools.py, applied here too
    // because streamed tokens reach the page before call_model returns.
    s = escapeHtml(s).replace(/【(\d+)(?:†[^】]*)?】/g, "[$1]");
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>");
    return s;
}

function splitRow(line) {
    return line
        .trim()
        .replace(/^\||\|$/g, "")
        .split("|")
        .map((c) => c.trim());
}

const TABLE_SEP_RE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/;

const NUMERIC_CELL_RE = /^[+\-−]?\$?[+\-−]?[\d,]+(\.\d+)?%?$/;

function cellValue(cell) {
    return cell.replace(/\*\*|\*/g, "").trim();
}

// Columns the model left unaligned (a bare ---) are right-aligned when every
// body cell is a figure, so prices and P&L line up on the decimal.
function tableToHtml(lines) {
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
    const signClass = (cell) => {
        const v = cellValue(cell);
        if (!NUMERIC_CELL_RE.test(v)) return "";
        if (/^\$?\+/.test(v)) return ' class="gain"';
        if (/^\$?[-−]/.test(v)) return ' class="loss"';
        return "";
    };
    let html = '<div class="table-wrap"><table><thead><tr>';
    header.forEach((h, i) => {
        html += `<th style="text-align:${aligns[i] || "left"}">${inlineMd(h)}</th>`;
    });
    html += "</tr></thead><tbody>";
    rows.forEach((row) => {
        html += "<tr>";
        row.forEach((c, i) => {
            html += `<td${signClass(c)} style="text-align:${aligns[i] || "left"}">${inlineMd(c)}</td>`;
        });
        html += "</tr>";
    });
    html += "</tbody></table></div>";
    return html;
}

function renderMarkdown(text) {
    // Strip ``` fence delimiters (with or without a language tag) rather than
    // rendering them as a literal code block -- models sometimes wrap a
    // markdown table in a ```markdown fence, and we still want that table
    // parsed as a table, not shown as one big preformatted blob.
    const lines = text.split("\n").filter((l) => !/^\s*```(\w*)?\s*$/.test(l));
    let html = "";
    let i = 0;
    const isListLine = (l) => /^\s*[-*]\s+/.test(l);
    const isOrderedLine = (l) => /^\s*\d+\.\s+/.test(l);
    const isTableStart = (idx) => lines[idx].trim().startsWith("|") && idx + 1 < lines.length && TABLE_SEP_RE.test(lines[idx + 1]);

    while (i < lines.length) {
        if (lines[i].trim() === "") {
            i++;
            continue;
        }
        if (isTableStart(i)) {
            const tableLines = [lines[i], lines[i + 1]];
            i += 2;
            while (i < lines.length && lines[i].trim().startsWith("|")) {
                tableLines.push(lines[i]);
                i++;
            }
            html += tableToHtml(tableLines);
            continue;
        }
        if (isListLine(lines[i])) {
            const items = [];
            while (i < lines.length && isListLine(lines[i])) {
                items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
                i++;
            }
            html += "<ul>" + items.map((it) => `<li>${inlineMd(it)}</li>`).join("") + "</ul>";
            continue;
        }
        if (isOrderedLine(lines[i])) {
            const items = [];
            while (i < lines.length && isOrderedLine(lines[i])) {
                items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
                i++;
            }
            html += "<ol>" + items.map((it) => `<li>${inlineMd(it)}</li>`).join("") + "</ol>";
            continue;
        }
        const para = [];
        while (i < lines.length && lines[i].trim() !== "" && !isTableStart(i) && !isListLine(lines[i]) && !isOrderedLine(lines[i])) {
            para.push(lines[i]);
            i++;
        }
        html += `<p>${para.map(inlineMd).join("<br>")}</p>`;
    }
    return html;
}

function messageHtml(text, cls) {
    const body = cls === "assistant" ? renderMarkdown(text) : escapeHtml(text);
    return `<div class="msg ${cls}">${body}</div>`;
}

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

// buy_stock's interrupt payload as an order ticket. A payload without order
// fields falls back to its plain message.
function ticketHtml(value) {
    const isFinal = value.step === value.of;
    const actions =
        '<div class="confirm-actions">' +
        `<button class="primary" type="button" data-reply="yes">${isFinal ? "Place order" : "Continue"}</button>` +
        '<button type="button" data-reply="no">Cancel</button></div>';
    if (!value.symbol) return `<div class="msg confirm">${escapeHtml(value.message || "")}${actions}</div>`;
    const shares = value.quantity === 1 ? "1 share" : `${value.quantity} shares`;
    const price =
        value.estimated_price == null
            ? ""
            : `<dl><dt>Est. price</dt><dd>${usd.format(value.estimated_price)}</dd>` +
              `<dt>Est. cost</dt><dd>${usd.format(value.estimated_cost)}</dd></dl>`;
    const warning = isFinal ? '<p class="ticket-warning">This submits the order and can\'t be undone.</p>' : "";
    return (
        '<div class="msg ticket"><div class="ticket-head">' +
        `<span>${isFinal ? "Final confirmation" : "Confirm buy order"}</span><span>Step ${value.step} of ${value.of}</span></div>` +
        '<div class="ticket-body"><div class="ticket-order">' +
        `<span class="ticket-symbol">${escapeHtml(value.symbol)}</span><span class="ticket-qty">${shares}</span></div>` +
        `${price}${warning}${actions}</div></div>`
    );
}

// The visible part of a thread: human messages and assistant replies with text
// (tool calls and results are internal), then a pending interrupt's ticket.
function threadHtml(messages, interrupt) {
    let html = "";
    for (const m of messages) {
        if (m.type === "human") html += messageHtml(m.content, "user");
        else if (m.type === "ai" && m.content) html += messageHtml(m.content, "assistant");
    }
    return interrupt ? html + ticketHtml(interrupt) : html;
}
