// The LangGraph API calls and SSE parsing behind the chat page, kept free of
// React so App.tsx only deals in state.

import type { InterruptValue } from "../../app/static/render.js";

const STREAM_MODES = ["messages", "updates"];

interface ToolCall {
    name: string;
    args?: { symbol?: unknown };
}

interface MessageChunk {
    type: string;
    id: string;
    content: string;
    tool_calls?: ToolCall[];
}

// The id lives in the URL so a reload or a shared link reopens the same
// conversation; frontend.py redirects a malformed or unknown one to "/".
export function storeThread(id: string | null): void {
    const url = new URL(location.href);
    if (id) url.searchParams.set("thread_id", id);
    else url.searchParams.delete("thread_id");
    history.replaceState(null, "", url);
}

export async function createThread(): Promise<string> {
    const res = await fetch("/threads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
    });
    const data = await res.json();
    storeThread(data.thread_id);
    return data.thread_id;
}

// The server keeps thread records in memory, so after a restart a stored id
// 404s even though its checkpoints are still in Atlas (app/checkpointer.py).
// Recreating the record under the same id brings the history back. The
// graph_id metadata is what a first run would have set; without it the state
// endpoint can't tell which graph to read the checkpoint with and returns {}.
export async function recreateThread(id: string): Promise<boolean> {
    const res = await fetch("/threads", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id: id, if_exists: "do_nothing", metadata: { graph_id: "agent" } }),
    });
    return res.ok;
}

export function postRun(threadId: string, text: string, asResume: boolean): Promise<Response> {
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

export function joinRun(threadId: string, runId: string): Promise<Response> {
    const modes = encodeURIComponent(JSON.stringify(STREAM_MODES));
    return fetch(`/threads/${threadId}/runs/${runId}/stream?stream_mode=${modes}`);
}

export interface ThreadState {
    values?: { messages?: { type: string; content: string }[] };
    interrupts?: { value: InterruptValue }[];
}

export async function fetchState(threadId: string): Promise<ThreadState | null> {
    const res = await fetch(`/threads/${threadId}/state`);
    return res.ok ? res.json() : null;
}

const TOOL_LABELS: Record<string, string> = {
    get_positions: "Checking your positions",
    get_account_summary: "Checking your account",
    get_open_orders: "Checking open orders",
    get_recent_fills: "Checking recent fills",
    get_bracket_order_status: "Checking bracket orders",
    research_stock: "Researching",
    search_filings: "Searching 10-K filings",
    buy_stock: "Preparing your order",
};

function toolLabel(call: ToolCall): string {
    const label = TOOL_LABELS[call.name] || call.name;
    const symbol = call.name === "research_stock" && call.args?.symbol;
    return symbol ? `${label} ${String(symbol).toUpperCase()}` : label;
}

function parseSSEEvent(raw: string): { eventType: string; data: any } | null {
    let eventType = "message";
    const dataLines: string[] = [];
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

export interface StreamHandlers {
    // A tool call started before any reply text; retitles the "Thinking" bubble.
    onStatus(label: string): void;
    // The agent's reply so far (the whole text, not a delta).
    onReply(content: string): void;
    onInterrupt(value: InterruptValue): void;
}

// Consumes a run's SSE stream, reporting to `handlers` as events arrive.
// Shared by a fresh POST run and by re-joining an in-flight run after a page
// reload. Resolves to whether a reply or an interrupt arrived.
//
// "messages" stream mode carries *every* LLM call in the graph, including the
// sub-agent and synthesis calls research_stock makes inside the tools node.
// Those are working notes, not the reply -- rendering them put the synthesis's
// three paragraphs in the bubble and then wiped them when the agent's own
// reply started streaming into the same bubble. So only ids that a
// messages/metadata event attributed to the agent node are reported. Joining a
// run mid-flight can miss the metadata event for a message already streaming;
// that message then stays on "Thinking..." until the post-join re-render from
// thread state, which is authoritative anyway.
export async function consumeStream(
    res: Response,
    handlers: StreamHandlers,
): Promise<{ replied: boolean; interrupted: boolean }> {
    let replied = false;
    let interrupted = false;
    let currentMsgId: string | null = null;
    const agentMsgIds = new Set<string>();
    const reader = res.body!.getReader();
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
                for (const [msgId, entry] of Object.entries<any>(record.data)) {
                    if (entry?.metadata?.langgraph_node === "agent") agentMsgIds.add(msgId);
                }
            } else if (record.eventType === "updates" && record.data.__interrupt__) {
                interrupted = true;
                handlers.onInterrupt(record.data.__interrupt__[0].value);
            } else if (record.eventType === "messages/partial" || record.eventType === "messages/complete") {
                const msg: MessageChunk | undefined = record.data[0];
                if (msg?.type !== "ai" || !agentMsgIds.has(msg.id)) continue;
                if (msg.id !== currentMsgId) {
                    currentMsgId = msg.id;
                    if (!msg.content && msg.tool_calls?.length) continue;
                }
                if (msg.content) {
                    replied = true;
                    handlers.onReply(msg.content);
                } else if (msg.tool_calls?.length && !replied) {
                    handlers.onStatus(toolLabel(msg.tool_calls[0]));
                }
            }
        }
    }
    return { replied, interrupted };
}
