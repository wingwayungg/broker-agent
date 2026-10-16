import { useEffect, useLayoutEffect, useReducer, useRef, useState } from "react";
import type { InterruptValue } from "../../app/static/render.js";
import { consumeStream, createThread, fetchState, joinRun, postRun, recreateThread, storeThread } from "./api";
import type { ThreadState } from "./api";
import { Message } from "./Message";
import type { TextItem } from "./Message";
import { Ticket } from "./Ticket";
import type { TicketItem } from "./Ticket";

// The page's static parts, which frontend.py renders and React only wires up:
// it toggles their state and listens to them, but never re-renders them.
export interface Shell {
    log: HTMLElement;
    reset: HTMLButtonElement;
    input: HTMLInputElement;
    send: HTMLButtonElement;
    suggestions: HTMLElement;
}

// What frontend.py embeds in a thread page as #bootstrap.
export interface Bootstrap {
    thread_id: string;
    active_run_id: string | null;
    messages: { type: string; content: string }[];
    interrupt: InterruptValue | null;
}

type Item = TextItem | TicketItem;

type Action = { type: "append"; item: Item } | { type: "text"; key: number; text: string } | { type: "replace"; key: number; item: Item | null } | { type: "answer"; key: number } | { type: "load"; items: Item[] };

let lastKey = 0;
const nextKey = () => ++lastKey;

const textItem = (kind: "user" | "assistant" | "status" | "error", text: string): Item => ({
    key: nextKey(),
    kind,
    text,
});

const ticketItem = (value: InterruptValue): Item => ({ key: nextKey(), kind: "ticket", value, answered: false });

// Actions naming a key that is gone (the log was reset or re-rendered from
// state meanwhile) are no-ops.
function reducer(items: Item[], action: Action): Item[] {
    switch (action.type) {
        case "append":
            return [...items, action.item];
        case "text":
            return items.map((it) => (it.key === action.key && it.kind !== "ticket" ? { ...it, text: action.text } : it));
        case "replace":
            return items.flatMap((it) => (it.key !== action.key ? [it] : action.item ? [action.item] : []));
        case "answer":
            return items.map((it) => (it.key === action.key && it.kind === "ticket" ? { ...it, answered: true } : it));
        case "load":
            return action.items;
    }
}

// The visible part of a thread, matching threadHtml in render.js: human
// messages and assistant replies with text, then a pending interrupt's ticket.
function threadItems(messages: { type: string; content: string }[], interrupt: InterruptValue | null): Item[] {
    const items: Item[] = [];
    for (const m of messages) {
        if (m.type === "human") items.push(textItem("user", m.content));
        else if (m.type === "ai" && m.content) items.push(textItem("assistant", m.content));
    }
    if (interrupt) items.push(ticketItem(interrupt));
    return items;
}

export function App({ bootstrap, shell }: { bootstrap: Bootstrap | null; shell: Shell }) {
    // A run still in flight when the page loaded is joined on mount, behind a
    // "Thinking" bubble that's in the very first render.
    const [joinStatus] = useState(() => (bootstrap?.active_run_id ? textItem("status", "Thinking") : null));
    const [items, dispatch] = useReducer(reducer, null, () => {
        const restored = bootstrap ? threadItems(bootstrap.messages, bootstrap.interrupt) : [];
        return joinStatus ? [...restored, joinStatus] : restored;
    });
    const [busy, setBusy] = useState(false);
    // Read by the async send and join flows after awaits, where state would be stale.
    const session = useRef({
        threadId: bootstrap?.thread_id ?? null,
        pendingInterrupt: Boolean(bootstrap?.interrupt),
        busy: false,
    });
    const refocus = useRef(false);

    const setBusyBoth = (value: boolean) => {
        session.current.busy = value;
        setBusy(value);
    };

    // #log's class shows the server's welcome hero while the log is empty.
    const empty = items.length === 0;
    useLayoutEffect(() => {
        shell.log.className = empty ? "empty" : "";
        shell.reset.classList.toggle("hidden", empty);
    }, [empty]);

    useLayoutEffect(() => {
        shell.log.scrollTop = shell.log.scrollHeight;
    }, [items]);

    useEffect(() => {
        shell.input.disabled = shell.send.disabled = busy;
        if (!busy && refocus.current) {
            refocus.current = false;
            shell.input.focus();
        }
    }, [busy]);

    // Streams a run's events into the log behind the `statusKey` bubble, which
    // is retitled for tool calls, then replaced by the reply or dropped for a
    // confirmation ticket.
    async function streamInto(res: Response, statusKey: number) {
        let replyKey: number | null = null;
        return consumeStream(res, {
            onStatus: (label) => dispatch({ type: "text", key: statusKey, text: label }),
            onReply: (content) => {
                if (replyKey === null) {
                    const item = textItem("assistant", content);
                    replyKey = item.key;
                    dispatch({ type: "replace", key: statusKey, item });
                } else {
                    dispatch({ type: "text", key: replyKey, text: content });
                }
            },
            onInterrupt: (value) => {
                session.current.pendingInterrupt = true;
                dispatch({ type: "replace", key: statusKey, item: null });
                if (replyKey !== null) dispatch({ type: "replace", key: replyKey, item: null });
                dispatch({ type: "append", item: ticketItem(value) });
            },
        });
    }

    async function sendMessage(text: string) {
        const s = session.current;
        if (s.busy || !text) return;
        setBusyBoth(true);
        dispatch({ type: "append", item: textItem("user", text) });
        const status = textItem("status", "Thinking");
        dispatch({ type: "append", item: status });

        try {
            s.threadId ??= await createThread();
            let res = await postRun(s.threadId, text, s.pendingInterrupt);

            if (res.status === 404) {
                // The server restarted since this tab last talked to it and lost
                // the thread record. The history is still in Atlas, so recreate
                // the record under the same id and resend; a pending confirmation
                // is in the checkpoint too, so the resume stays valid.
                if (await recreateThread(s.threadId)) {
                    res = await postRun(s.threadId, text, s.pendingInterrupt);
                } else {
                    // Recreating failed (e.g. Atlas unreachable): fall back to a
                    // fresh thread, where a resume no longer makes sense.
                    storeThread(null);
                    s.threadId = null;
                    s.pendingInterrupt = false;
                    dispatch({ type: "text", key: status.key, text: "Starting a new conversation" });
                    s.threadId = await createThread();
                    res = await postRun(s.threadId, text, false);
                }
            }

            if (!res.ok || !res.body) {
                dispatch({ type: "replace", key: status.key, item: textItem("error", `Something went wrong (${res.status}). Try again.`) });
                return;
            }

            const { replied, interrupted } = await streamInto(res, status.key);
            if (!interrupted) {
                s.pendingInterrupt = false;
                if (!replied) dispatch({ type: "replace", key: status.key, item: textItem("assistant", "(no response)") });
            }
        } catch {
            dispatch({ type: "replace", key: status.key, item: textItem("error", "Network error — try again.") });
        } finally {
            shell.input.value = "";
            refocus.current = true;
            setBusyBoth(false);
        }
    }

    function answerTicket(key: number, reply: string) {
        if (session.current.busy) return;
        dispatch({ type: "answer", key });
        sendMessage(reply);
    }

    // frontend.py renders the thread's history into the page (see its
    // docstring). A run still in flight is joined here, and since joining only
    // delivers events emitted after connecting, the log is re-rendered from
    // state once it finishes.
    useEffect(() => {
        const runId = bootstrap?.active_run_id;
        const threadId = session.current.threadId;
        if (!runId || !threadId || !joinStatus) return;
        (async () => {
            setBusyBoth(true);
            try {
                const joinRes = await joinRun(threadId, runId);
                if (joinRes.ok && joinRes.body) await streamInto(joinRes, joinStatus.key);
                const state: ThreadState | null = await fetchState(threadId);
                if (state) {
                    const interrupt = state.interrupts?.[0]?.value ?? null;
                    session.current.pendingInterrupt = Boolean(interrupt);
                    dispatch({ type: "load", items: threadItems(state.values?.messages ?? [], interrupt) });
                } else {
                    dispatch({ type: "replace", key: joinStatus.key, item: null });
                }
            } catch {
                // Leave whatever rendered; the next send will surface real errors.
            } finally {
                setBusyBoth(false);
            }
        })();
        // Mount only: the bootstrap is fixed for the page's lifetime.
    }, []);

    useEffect(() => {
        const onSend = () => sendMessage(shell.input.value.trim());
        const onKeyDown = (e: KeyboardEvent) => {
            if (e.key === "Enter") sendMessage(shell.input.value.trim());
        };
        const onSuggestion = (e: Event) => {
            const btn = (e.target as Element).closest("button");
            if (btn) sendMessage(btn.textContent!);
        };
        const onReset = () => {
            storeThread(null);
            session.current.threadId = null;
            session.current.pendingInterrupt = false;
            dispatch({ type: "load", items: [] });
        };
        const listeners: [HTMLElement, string, (e: any) => void][] = [
            [shell.send, "click", onSend],
            [shell.input, "keydown", onKeyDown],
            [shell.suggestions, "click", onSuggestion],
            [shell.reset, "click", onReset],
        ];
        for (const [el, type, fn] of listeners) el.addEventListener(type, fn);
        return () => {
            for (const [el, type, fn] of listeners) el.removeEventListener(type, fn);
        };
    }, []);

    return items.map((item) => (item.kind === "ticket" ? <Ticket key={item.key} item={item} onReply={answerTicket} /> : <Message key={item.key} item={item} />));
}
