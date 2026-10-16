let threadId = null;
let pendingInterrupt = false;
let busy = false;

const STREAM_MODES = ["messages", "updates"];

const log = document.getElementById("log");
const logInner = document.getElementById("log-inner");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const resetBtn = document.getElementById("reset");

function setContent(div, text, cls) {
    if (cls === "assistant") {
        div.innerHTML = renderMarkdown(text);
    } else {
        div.textContent = text;
    }
}

function appendHtml(html) {
    log.classList.remove("empty", "loading");
    logInner.insertAdjacentHTML("beforeend", html);
    log.scrollTop = log.scrollHeight;
    return logInner.lastElementChild;
}

const addMsg = (text, cls) => appendHtml(messageHtml(text, cls));

const addConfirm = (value) => appendHtml(ticketHtml(value));

// Delegated, since tickets arrive as HTML (ticketHtml), some rendered by the
// server. Answering disables the buttons so a stale ticket can't later send
// "yes" as a fresh message.
log.addEventListener("click", (e) => {
    const btn = e.target.closest(".confirm-actions button");
    if (!btn || busy) return;
    btn.parentElement.querySelectorAll("button").forEach((b) => (b.disabled = true));
    sendMessage(btn.dataset.reply);
});

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

// Re-renders the whole log from the thread's server-side state, including a
// pending buy_stock ticket.
function renderThreadState(state) {
    const messages = state.values?.messages || [];
    if (!messages.length) {
        showEmptyHero();
        return;
    }
    const interrupt = state.interrupts?.[0];
    pendingInterrupt = Boolean(interrupt);
    logInner.innerHTML = threadHtml(messages, interrupt?.value);
    log.classList.remove("empty", "loading");
    resetBtn.classList.remove("hidden");
    log.scrollTop = log.scrollHeight;
}

// frontend.py renders the thread's history into the page (see its docstring). A
// run still in flight is joined here, and since joining only delivers events
// emitted after connecting, the log is re-rendered from state once it finishes.
async function restoreThread() {
    const bootstrap = document.getElementById("bootstrap");
    if (!bootstrap) return;
    const { thread_id, pending_interrupt, active_run_id } = JSON.parse(bootstrap.textContent);
    threadId = thread_id;
    pendingInterrupt = pending_interrupt;
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

// A thread page starts in "loading" until its history streams in (see
// frontend.py); one that turns out to have nothing to show ends on the welcome
// screen.
restoreThread().finally(() => {
    log.classList.remove("loading");
    if (!logInner.querySelector(".msg")) showEmptyHero();
});
