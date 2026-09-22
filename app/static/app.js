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
    s = escapeHtml(s);
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

function tableToHtml(lines) {
    const header = splitRow(lines[0]);
    const aligns = splitRow(lines[1]).map((cell) => {
        const left = cell.startsWith(":");
        const right = cell.endsWith(":");
        if (left && right) return "center";
        if (right) return "right";
        return "left";
    });
    let html = '<div class="table-wrap"><table><thead><tr>';
    header.forEach((h, i) => {
        html += `<th style="text-align:${aligns[i] || "left"}">${inlineMd(h)}</th>`;
    });
    html += "</tr></thead><tbody>";
    lines.slice(2).forEach((line) => {
        html += "<tr>";
        splitRow(line).forEach((c, i) => {
            html += `<td style="text-align:${aligns[i] || "left"}">${inlineMd(c)}</td>`;
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
    log.classList.remove("empty");
    const div = document.createElement("div");
    div.className = "msg " + cls;
    setContent(div, text, cls);
    logInner.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
}

function addConfirm(text) {
    const div = addMsg(text, "confirm");
    const actions = document.createElement("div");
    actions.className = "confirm-actions";
    ["Yes", "No"].forEach((label) => {
        const btn = document.createElement("button");
        btn.textContent = label;
        btn.onclick = () => sendMessage(label.toLowerCase());
        actions.appendChild(btn);
    });
    div.appendChild(actions);
}

async function ensureThread() {
    const stored = sessionStorage.getItem("thread_id");
    if (stored) {
        threadId = stored;
        return;
    }
    const res = await fetch("/threads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
    });
    const data = await res.json();
    threadId = data.thread_id;
    sessionStorage.setItem("thread_id", threadId);
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
        // Server sends CRLF line endings; normalize before boundary-matching.
        buf += decoder.decode(value, { stream: true }).replaceAll("\r\n", "\n");
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
                addConfirm(record.data.__interrupt__[0].value.message);
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
                    status.textContent = "Calling " + msg.tool_calls[0].name + "...";
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
    let status = addMsg("Thinking...", "system");

    try {
        await ensureThread();
        let res = await postRun(text, pendingInterrupt);

        if (res.status === 404) {
            // The thread doesn't exist server-side anymore (e.g. the server
            // restarted/redeployed since this tab last talked to it, wiping its
            // in-memory threads). Start a fresh thread transparently and resend
            // as a new message -- any pending confirmation from the old thread
            // is gone regardless, so a resume no longer makes sense.
            sessionStorage.removeItem("thread_id");
            threadId = null;
            pendingInterrupt = false;
            status.textContent = "Starting a new conversation...";
            await ensureThread();
            res = await postRun(text, false);
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
    log.classList.remove("empty");
    resetBtn.classList.remove("hidden");
    for (const msg of messages) {
        if (msg.type === "human") addMsg(msg.content, "user");
        else if (msg.type === "ai" && msg.content) addMsg(msg.content, "assistant");
    }
    const interrupt = state.interrupts?.[0] || state.tasks?.flatMap((t) => t.interrupts || [])[0];
    pendingInterrupt = Boolean(interrupt);
    if (interrupt) addConfirm(interrupt.value.message);
}

// The log lives only in the DOM, so any reload (a manual refresh, Chrome
// discarding a background tab while a slow research call runs, pull-to-
// refresh on mobile) used to leave the page blank even though the thread
// and its run were still alive on the server. Rebuild the log from the
// thread's state, and if a run is still in flight, show "Thinking..." and
// join its stream so the reply lands in this page when it finishes.
async function restoreThread() {
    const stored = sessionStorage.getItem("thread_id");
    if (!stored) {
        await ensureThread();
        return;
    }
    threadId = stored;
    busy = true;
    sendBtn.disabled = true;
    input.disabled = true;
    try {
        const stateRes = await fetch(`/threads/${threadId}/state`);
        if (stateRes.status === 404) {
            sessionStorage.removeItem("thread_id");
            threadId = null;
            await ensureThread();
            return;
        }
        if (!stateRes.ok) return;
        renderThreadState(await stateRes.json());

        const runsRes = await fetch(`/threads/${threadId}/runs?limit=1`);
        if (!runsRes.ok) return;
        const latest = (await runsRes.json())[0];
        if (!latest || !["pending", "running"].includes(latest.status)) return;

        const status = addMsg("Thinking...", "system");
        const modes = encodeURIComponent(JSON.stringify(STREAM_MODES));
        const joinRes = await fetch(`/threads/${threadId}/runs/${latest.run_id}/stream?stream_mode=${modes}`);
        if (joinRes.ok && joinRes.body) await consumeStream(joinRes, status);
        // Joining only delivers events emitted after we connected; the state
        // is authoritative for whatever streamed before the reload.
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
    sessionStorage.removeItem("thread_id");
    threadId = null;
    pendingInterrupt = false;
    showEmptyHero();
};

restoreThread();
