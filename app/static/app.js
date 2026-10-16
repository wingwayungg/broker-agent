let threadId = null;
let pendingInterrupt = false;
let busy = false;

const STREAM_MODES = ["messages", "updates"];

const log = document.getElementById("log");
const logInner = document.getElementById("log-inner");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const resetBtn = document.getElementById("reset");

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

function setContent(div, text, cls) {
    if (cls === "assistant") {
        div.innerHTML = renderMarkdown(text);
    } else {
        div.textContent = text;
    }
}

function addMsg(text, cls) {
    log.classList.remove("empty", "loading");
    const div = document.createElement("div");
    div.className = "msg " + cls;
    setContent(div, text, cls);
    logInner.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
}

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

// Renders buy_stock's interrupt payload as an order ticket. A payload without
// order fields falls back to its plain message. Answering disables the
// buttons so a stale ticket can't later send "yes" as a fresh message.
function addConfirm(value) {
    const isFinal = value.step === value.of;
    let div;
    if (value.symbol) {
        div = addMsg("", "ticket");
        const head = el("div", "ticket-head");
        head.append(el("span", "", isFinal ? "Final confirmation" : "Confirm buy order"), el("span", "", `Step ${value.step} of ${value.of}`));
        const body = el("div", "ticket-body");
        const order = el("div", "ticket-order");
        const shares = value.quantity === 1 ? "1 share" : `${value.quantity} shares`;
        order.append(el("span", "ticket-symbol", value.symbol), el("span", "ticket-qty", shares));
        body.append(order);
        if (value.estimated_price != null) {
            const dl = el("dl");
            dl.append(el("dt", "", "Est. price"), el("dd", "", usd.format(value.estimated_price)));
            dl.append(el("dt", "", "Est. cost"), el("dd", "", usd.format(value.estimated_cost)));
            body.append(dl);
        }
        if (isFinal) body.append(el("p", "ticket-warning", "This submits the order and can't be undone."));
        div.append(head, body);
    } else {
        div = addMsg(value.message, "confirm");
    }
    const actions = el("div", "confirm-actions");
    const buttons = [
        [isFinal ? "Place order" : "Continue", "yes", "primary"],
        ["Cancel", "no", ""],
    ].map(([label, reply, cls]) => {
        const btn = el("button", cls, label);
        btn.type = "button";
        btn.onclick = () => {
            if (busy) return;
            buttons.forEach((b) => (b.disabled = true));
            sendMessage(reply);
        };
        return btn;
    });
    actions.append(...buttons);
    (div.querySelector(".ticket-body") || div).appendChild(actions);
}

// The id lives in the URL so a reload or a shared link reopens the same
// conversation; frontend.py redirects a malformed or unknown one to "/".
function storeThread(id) {
    const url = new URL(location.href);
    if (id) url.searchParams.set("thread_id", id);
    else url.searchParams.delete("thread_id");
    history.replaceState(null, "", url);
}

async function ensureThread() {
    if (threadId) return;
    const res = await fetch("/threads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
    });
    const data = await res.json();
    threadId = data.thread_id;
    storeThread(threadId);
}

// The server keeps thread records in memory, so after a restart a stored id
// 404s even though its checkpoints are still in Atlas (app/checkpointer.py).
// Recreating the record under the same id brings the history back. The
// graph_id metadata is what a first run would have set; without it the state
// endpoint can't tell which graph to read the checkpoint with and returns {}.
async function recreateThread(id) {
    const res = await fetch("/threads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id: id, if_exists: "do_nothing", metadata: { graph_id: "agent" } }),
    });
    return res.ok;
}

// Splits a text/event-stream chunk buffer into individual "event:"/"data:"
// records, tolerating records that arrive split across network reads.
function parseSSEEvent(raw) {
    let eventType = "message";
    const dataLines = [];
    for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) eventType = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) return null;
    try {
        return { eventType, data: JSON.parse(dataLines.join("\n")) };
    } catch {
        return null;
    }
}

function postRun(text, asResume) {
    const body = asResume
        ? { assistant_id: "agent", command: { resume: text }, stream_mode: STREAM_MODES }
        : {
              assistant_id: "agent",
              input: { messages: [{ role: "user", content: text }] },
              stream_mode: STREAM_MODES,
          };
    return fetch(`/threads/${threadId}/runs/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
}

const TOOL_LABELS = {
    get_positions: "Checking your positions",
    get_account_summary: "Checking your account",
    get_open_orders: "Checking open orders",
    get_recent_fills: "Checking recent fills",
    get_bracket_order_status: "Checking bracket orders",
    research_stock: "Researching",
    search_filings: "Searching 10-K filings",
    buy_stock: "Preparing your order",
};

function toolLabel(call) {
    const label = TOOL_LABELS[call.name] || call.name;
    const symbol = call.name === "research_stock" && call.args?.symbol;
    return symbol ? `${label} ${String(symbol).toUpperCase()}` : label;
}

// Consumes a run's SSE stream, rendering into the log as events arrive:
// `status` is the placeholder bubble ("Thinking...") that gets retitled for
// tool calls and replaced by the streaming assistant reply, or removed if a
// confirmation interrupt arrives instead. Shared by a fresh POST run and by
// re-joining an in-flight run after a page reload.
//
// "messages" stream mode carries *every* LLM call in the graph, including the
// sub-agent and synthesis calls research_stock makes inside the tools node.
// Those are working notes, not the reply -- rendering them put the synthesis's
// three paragraphs in the bubble and then wiped them when the agent's own
// reply started streaming into the same div. So only ids that a
// messages/metadata event attributed to the agent node are rendered. Joining a
// run mid-flight can miss the metadata event for a message already streaming;
// that message then stays on "Thinking..." until the post-join re-render from
// thread state, which is authoritative anyway.
async function consumeStream(res, status) {
    let assistantDiv = null;
    let assistantMsgId = null;
    let interrupted = false;
    const agentMsgIds = new Set();
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        // Server sends CRLF line endings; normalize the whole buffer, since a
        // read can end between \r and \n.
        buf = (buf + decoder.decode(value, { stream: true })).replaceAll("\r\n", "\n");
        let idx;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
            const record = parseSSEEvent(buf.slice(0, idx));
            buf = buf.slice(idx + 2);
            if (!record) continue;

            if (record.eventType === "messages/metadata") {
                for (const [msgId, entry] of Object.entries(record.data)) {
                    if (entry?.metadata?.langgraph_node === "agent") agentMsgIds.add(msgId);
                }
            } else if (record.eventType === "updates" && record.data.__interrupt__) {
                interrupted = true;
                pendingInterrupt = true;
                status.remove();
                if (assistantDiv) assistantDiv.remove();
                addConfirm(record.data.__interrupt__[0].value);
            } else if (record.eventType === "messages/partial" || record.eventType === "messages/complete") {
                const msg = record.data[0];
                if (msg?.type !== "ai" || !agentMsgIds.has(msg.id)) continue;
                if (msg.id !== assistantMsgId) {
                    assistantMsgId = msg.id;
                    if (!msg.content && msg.tool_calls?.length) continue;
                }
                if (msg.content) {
                    if (!assistantDiv) {
                        status.remove();
                        assistantDiv = addMsg(msg.content, "assistant");
                    } else {
                        setContent(assistantDiv, msg.content, "assistant");
                    }
                    log.scrollTop = log.scrollHeight;
                } else if (msg.tool_calls?.length && !assistantDiv) {
                    status.textContent = toolLabel(msg.tool_calls[0]);
                }
            }
        }
    }
    return { interrupted, assistantDiv };
}

async function sendMessage(text) {
    if (busy || !text) return;
    busy = true;
    sendBtn.disabled = true;
    input.disabled = true;

    addMsg(text, "user");
    resetBtn.classList.remove("hidden");
    let status = addMsg("Thinking", "system pending");

    try {
        await ensureThread();
        let res = await postRun(text, pendingInterrupt);

        if (res.status === 404) {
            // The server restarted since this tab last talked to it and lost
            // the thread record. The history is still in Atlas, so recreate
            // the record under the same id and resend; a pending confirmation
            // is in the checkpoint too, so the resume stays valid.
            if (await recreateThread(threadId)) {
                res = await postRun(text, pendingInterrupt);
            } else {
                // Recreating failed (e.g. Atlas unreachable): fall back to a
                // fresh thread, where a resume no longer makes sense.
                storeThread(null);
                threadId = null;
                pendingInterrupt = false;
                status.textContent = "Starting a new conversation";
                await ensureThread();
                res = await postRun(text, false);
            }
        }

        if (!res.ok || !res.body) {
            status.remove();
            addMsg("Something went wrong (" + res.status + "). Try again.", "error");
            return;
        }

        const { interrupted, assistantDiv } = await consumeStream(res, status);

        if (!interrupted) {
            pendingInterrupt = false;
            if (!assistantDiv) {
                status.remove();
                addMsg("(no response)", "assistant");
            }
        }
    } catch (err) {
        status.remove();
        addMsg("Network error — try again.", "error");
    } finally {
        busy = false;
        sendBtn.disabled = false;
        input.disabled = false;
        input.value = "";
        input.focus();
    }
}

// Re-renders the whole log from the thread's server-side state. Human
// messages and assistant replies with text are shown; tool-call-only
// assistant messages and tool results are internal and skipped. A thread
// paused on buy_stock's interrupt gets its Yes/No prompt back too.
function renderThreadState(state) {
    const messages = state.values?.messages || [];
    if (!messages.length) {
        showEmptyHero();
        return;
    }
    logInner.innerHTML = "";
    log.classList.remove("empty", "loading");
    resetBtn.classList.remove("hidden");
    for (const msg of messages) {
        if (msg.type === "human") addMsg(msg.content, "user");
        else if (msg.type === "ai" && msg.content) addMsg(msg.content, "assistant");
    }
    const interrupt = state.interrupts?.[0];
    pendingInterrupt = Boolean(interrupt);
    if (interrupt) addConfirm(interrupt.value);
}

// frontend.py inlines the thread's history (see its docstring). A run still in
// flight is joined here, and since joining only delivers events emitted after
// connecting, the log is re-rendered from state once it finishes.
async function restoreThread() {
    const bootstrap = document.getElementById("bootstrap");
    if (!bootstrap) return;
    const { thread_id, state, active_run_id } = JSON.parse(bootstrap.textContent);
    threadId = thread_id;
    renderThreadState(state);
    if (!active_run_id) return;
    busy = true;
    sendBtn.disabled = true;
    input.disabled = true;
    try {
        const status = addMsg("Thinking", "system pending");
        const modes = encodeURIComponent(JSON.stringify(STREAM_MODES));
        const joinRes = await fetch(`/threads/${threadId}/runs/${active_run_id}/stream?stream_mode=${modes}`);
        if (joinRes.ok && joinRes.body) await consumeStream(joinRes, status);
        const finalRes = await fetch(`/threads/${threadId}/state`);
        if (finalRes.ok) renderThreadState(await finalRes.json());
        else status.remove();
    } catch (err) {
        // Leave whatever rendered; the next send will surface real errors.
    } finally {
        busy = false;
        sendBtn.disabled = false;
        input.disabled = false;
    }
}

sendBtn.onclick = () => sendMessage(input.value.trim());
document.querySelector(".suggestions").addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (btn) sendMessage(btn.textContent);
});
input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendMessage(input.value.trim());
});

// Reuse the server-rendered hero (it carries the inline logo SVG that
// frontend.py splices in) rather than rebuilding it from a template here.
const emptyHero = document.querySelector(".empty-hero");

function showEmptyHero() {
    resetBtn.classList.add("hidden");
    log.classList.add("empty");
    logInner.replaceChildren(emptyHero);
}

resetBtn.onclick = () => {
    storeThread(null);
    threadId = null;
    pendingInterrupt = false;
    showEmptyHero();
};

// A thread page starts in "loading" so it never flashes the welcome screen
// before its history renders; a thread re-joined before its first message
// checkpoint can still end up empty.
restoreThread().finally(() => {
    log.classList.remove("loading");
    if (!logInner.querySelector(".msg")) showEmptyHero();
});
