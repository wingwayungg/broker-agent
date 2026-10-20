// One conversation's state and the logic that drives it, as a Zustand store
// outside React: <Chat> creates it from the server-rendered thread, and the
// layout's Header and Composer subscribe to the slices they show.

import { createStore } from "zustand/vanilla";
import { createThread, joinRun, postMessage, readThread } from "./api";
import { type Action, type Item, reducer, textItem, threadItems, ticketItem } from "./items";
import { readEvents } from "./sse";
import { APP_TITLE, conversationTitle } from "./title";
import type { ThreadView } from "./types";

export interface ChatState {
    items: Item[];
    busy: boolean;
    // False only for IDLE, the stand-in read while no <Chat> is mounted.
    live: boolean;
    send: (text: string) => void;
    answer: (key: number, reply: string) => void;
    reset: () => void;
    // Re-joins the run that was in progress when the page was rendered, if any.
    join: (signal: AbortSignal) => void;
}

export type ChatStore = ReturnType<typeof createChatStore>;

const noop = () => {};

export const IDLE = createStore<ChatState>()(() => ({
    items: [],
    busy: false,
    live: false,
    send: noop,
    answer: noop,
    reset: noop,
    join: noop,
}));

// Plays a run's events into the log behind the `statusKey` bubble: it is
// retitled for tool calls, then replaced by the reply as it streams, or dropped
// for an order ticket.
async function streamRun(body: ReadableStream<Uint8Array>, statusKey: number, dispatch: (action: Action) => void) {
    let replyKey: number | null = null;
    let messageId: string | null = null;
    let text = "";
    let interrupted = false;
    let failed = false;
    for await (const event of readEvents(body)) {
        switch (event.type) {
            case "token":
                // A new message id (the agent replied again after a tool call)
                // starts over in the same bubble.
                if (event.id !== messageId) {
                    messageId = event.id;
                    text = "";
                }
                text += event.text;
                if (replyKey === null) {
                    const item = textItem("assistant", text);
                    replyKey = item.key;
                    dispatch({ type: "replace", key: statusKey, item });
                } else {
                    dispatch({ type: "text", key: replyKey, text });
                }
                break;
            case "status":
                if (replyKey === null) dispatch({ type: "text", key: statusKey, text: event.label });
                break;
            case "interrupt":
                interrupted = true;
                dispatch({ type: "replace", key: statusKey, item: null });
                if (replyKey !== null) dispatch({ type: "replace", key: replyKey, item: null });
                replyKey = null;
                dispatch({ type: "append", item: ticketItem(event.value) });
                break;
            case "error":
                failed = true;
                if (replyKey === null) dispatch({ type: "replace", key: statusKey, item: textItem("error", event.message) });
                else dispatch({ type: "append", item: textItem("error", event.message) });
                break;
        }
    }
    return { replied: replyKey !== null, interrupted, failed };
}

export function createChatStore(initial: ThreadView | null) {
    // A run still going when the page was rendered is re-joined on mount, behind
    // a "Thinking" bubble that is already in the server-rendered HTML.
    const joinStatus = initial?.active_run ? textItem("status", "Thinking") : null;
    let threadId = initial?.thread_id ?? null;

    return createStore<ChatState>()((set, get) => {
        const dispatch = (action: Action) => set((s) => ({ items: reducer(s.items, action) }));

        const send = (text: string) => {
            if (get().busy) return;
            set({ busy: true });
            dispatch({ type: "append", item: textItem("user", text) });
            const status = textItem("status", "Thinking");
            dispatch({ type: "append", item: status });
            const thread = threadId
                ? Promise.resolve(threadId)
                : createThread().then((id) => {
                      threadId = id;
                      // Next.js keeps its router in sync with replaceState, so the page
                      // gets a shareable /thread/ URL without remounting mid-conversation.
                      window.history.replaceState(null, "", `/thread/${id}`);
                      document.title = `${conversationTitle(text)} · ${APP_TITLE}`;
                      return id;
                  });
            thread
                .then((id) => postMessage(id, text))
                .then((res) => {
                    if (!res.ok || !res.body) {
                        const message = res.status === 409 ? "Still working on the previous message." : `Something went wrong (${res.status}). Try again.`;
                        dispatch({ type: "replace", key: status.key, item: textItem("error", message) });
                        return;
                    }
                    return streamRun(res.body, status.key, dispatch).then((outcome) => {
                        if (!outcome.replied && !outcome.interrupted && !outcome.failed) {
                            dispatch({ type: "replace", key: status.key, item: textItem("assistant", "(no response)") });
                        }
                    });
                })
                .catch(() => {
                    dispatch({ type: "replace", key: status.key, item: textItem("error", "Network error — try again.") });
                })
                .finally(() => set({ busy: false }));
        };

        return {
            items: [...(initial ? threadItems(initial) : []), ...(joinStatus ? [joinStatus] : [])],
            busy: joinStatus !== null,
            live: true,
            send,
            answer: (key, reply) => {
                if (get().busy) return;
                dispatch({ type: "answer", key });
                send(reply);
            },
            reset: () => {
                threadId = null;
                dispatch({ type: "load", items: [] });
                window.history.replaceState(null, "", "/");
                document.title = APP_TITLE;
            },
            join: (signal) => {
                const id = threadId;
                if (!joinStatus || !id) return;
                joinRun(id, signal)
                    .then((res) => {
                        if (res.status === 200 && res.body) {
                            return streamRun(res.body, joinStatus.key, dispatch).then((outcome) => {
                                if (!outcome.replied && !outcome.interrupted && !outcome.failed) {
                                    dispatch({ type: "replace", key: joinStatus.key, item: null });
                                }
                            });
                        }
                        // The run ended between the server render and now; the
                        // thread has its result.
                        return readThread(id, signal).then((thread) => {
                            dispatch(thread ? { type: "load", items: threadItems(thread) } : { type: "replace", key: joinStatus.key, item: null });
                        });
                    })
                    .catch(() => {
                        // Aborted on unmount, or the network dropped: leave what rendered.
                    })
                    .finally(() => {
                        if (!signal.aborted) set({ busy: false });
                    });
            },
        };
    });
}
