"use client";

import { type Dispatch, useCallback, useEffect, useLayoutEffect, useReducer, useRef, useState } from "react";
import { createThread, joinRun, postMessage, readThread } from "@/lib/api";
import { type Action, reducer, textItem, threadItems, ticketItem } from "@/lib/items";
import { readEvents } from "@/lib/sse";
import { APP_TITLE, conversationTitle } from "@/lib/title";
import type { ThreadView } from "@/lib/types";
import { useChatShell } from "./ChatShell";
import { WelcomeHero } from "./Heroes";
import { MessageBubble } from "./MessageBubble";
import { OrderTicket } from "./OrderTicket";

// Plays a run's events into the log behind the `statusKey` bubble: it is
// retitled for tool calls, then replaced by the reply as it streams, or dropped
// for an order ticket.
async function streamRun(body: ReadableStream<Uint8Array>, statusKey: number, dispatch: Dispatch<Action>) {
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

export function Chat({ initial }: { initial: ThreadView | null }) {
    // A run still going when the page was rendered is re-joined on mount, behind
    // a "Thinking" bubble that is already in the server-rendered HTML.
    const [joinStatus] = useState(() => (initial?.active_run ? textItem("status", "Thinking") : null));
    const [items, dispatch] = useReducer(reducer, null, () => [
        ...(initial ? threadItems(initial) : []),
        ...(joinStatus ? [joinStatus] : []),
    ]);
    const [busy, setBusy] = useState(joinStatus !== null);
    // Read after awaits, where state from the render that started them is stale.
    const busyRef = useRef(busy);
    const threadId = useRef(initial?.thread_id ?? null);
    const log = useRef<HTMLDivElement>(null);

    const setBusyBoth = useCallback((value: boolean) => {
        busyRef.current = value;
        setBusy(value);
    }, []);

    const { setControls } = useChatShell();

    useLayoutEffect(() => {
        if (log.current) log.current.scrollTop = log.current.scrollHeight;
    }, [items]);

    useEffect(() => {
        const id = threadId.current;
        if (!joinStatus || !id) return;
        const controller = new AbortController();
        (async () => {
            try {
                const res = await joinRun(id, controller.signal);
                if (res.status === 200 && res.body) {
                    const outcome = await streamRun(res.body, joinStatus.key, dispatch);
                    if (!outcome.replied && !outcome.interrupted && !outcome.failed) {
                        dispatch({ type: "replace", key: joinStatus.key, item: null });
                    }
                } else {
                    // The run ended between the server render and now; the
                    // thread has its result.
                    const thread = await readThread(id, controller.signal);
                    dispatch(thread ? { type: "load", items: threadItems(thread) } : { type: "replace", key: joinStatus.key, item: null });
                }
            } catch {
                // Aborted on unmount, or the network dropped: leave what rendered.
            } finally {
                if (!controller.signal.aborted) setBusyBoth(false);
            }
        })();
        return () => controller.abort();
    }, [joinStatus, setBusyBoth]);

    const send = useCallback(async (text: string) => {
        if (busyRef.current) return;
        setBusyBoth(true);
        dispatch({ type: "append", item: textItem("user", text) });
        const status = textItem("status", "Thinking");
        dispatch({ type: "append", item: status });
        try {
            let id = threadId.current;
            if (!id) {
                id = await createThread();
                threadId.current = id;
                // Next.js keeps its router in sync with replaceState, so the page
                // gets a shareable /thread/ URL without remounting mid-conversation.
                window.history.replaceState(null, "", `/thread/${id}`);
                document.title = `${conversationTitle(text)} · ${APP_TITLE}`;
            }
            const res = await postMessage(id, text);
            if (!res.ok || !res.body) {
                const message = res.status === 409 ? "Still working on the previous message." : `Something went wrong (${res.status}). Try again.`;
                dispatch({ type: "replace", key: status.key, item: textItem("error", message) });
                return;
            }
            const outcome = await streamRun(res.body, status.key, dispatch);
            if (!outcome.replied && !outcome.interrupted && !outcome.failed) {
                dispatch({ type: "replace", key: status.key, item: textItem("assistant", "(no response)") });
            }
        } catch {
            dispatch({ type: "replace", key: status.key, item: textItem("error", "Network error — try again.") });
        } finally {
            setBusyBoth(false);
        }
    }, [setBusyBoth]);

    function answerTicket(key: number, reply: string) {
        if (busyRef.current) return;
        dispatch({ type: "answer", key });
        send(reply);
    }

    const reset = useCallback(() => {
        threadId.current = null;
        dispatch({ type: "load", items: [] });
        window.history.replaceState(null, "", "/");
        document.title = APP_TITLE;
    }, []);

    const empty = items.length === 0;
    // Layout, not passive, so a client navigation never paints the layout's
    // Composer disabled between loading.tsx and this page.
    useLayoutEffect(() => {
        setControls({ busy, empty, send, reset });
        return () => setControls(null);
    }, [busy, empty, send, reset, setControls]);

    return (
        <div id="log" ref={log} className={empty ? "empty" : ""}>
            <div id="log-inner">
                {empty && <WelcomeHero onPick={send} />}
                {items.map((item) =>
                    item.kind === "ticket" ? (
                        <OrderTicket key={item.key} item={item} disabled={busy} onReply={answerTicket} />
                    ) : (
                        <MessageBubble key={item.key} item={item} />
                    ),
                )}
            </div>
        </div>
    );
}
