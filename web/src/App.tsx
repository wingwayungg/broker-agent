import { useEffect, useEffectEvent, useLayoutEffect, useReducer, useRef, useState } from "react";
import { consumeStream, fetchState, joinRun, postWithRecovery, storeThread } from "./api";
import type { ThreadState } from "./api";
import { reducer, textItem, threadItems, ticketItem } from "./items";
import { Message } from "./Message";
import { Ticket } from "./Ticket";
import type { Bootstrap, Shell } from "./types";

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
    }, [empty, shell]);

    useLayoutEffect(() => {
        shell.log.scrollTop = shell.log.scrollHeight;
    }, [items, shell]);

    useEffect(() => {
        shell.input.disabled = shell.send.disabled = busy;
        if (!busy && refocus.current) {
            refocus.current = false;
            shell.input.focus();
        }
    }, [busy, shell]);

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

    function sendMessage(text: string) {
        const s = session.current;
        if (s.busy || !text) return;
        setBusyBoth(true);
        dispatch({ type: "append", item: textItem("user", text) });
        const status = textItem("status", "Thinking");
        dispatch({ type: "append", item: status });

        postWithRecovery(s, text, () => dispatch({ type: "text", key: status.key, text: "Starting a new conversation" }))
            .then(async (res: Response) => {
                if (!res.ok || !res.body) {
                    dispatch({ type: "replace", key: status.key, item: textItem("error", `Something went wrong (${res.status}). Try again.`) });
                    return;
                }

                const { replied, interrupted } = await streamInto(res, status.key);
                if (!interrupted) {
                    s.pendingInterrupt = false;
                    if (!replied) dispatch({ type: "replace", key: status.key, item: textItem("assistant", "(no response)") });
                }
            })
            .catch(() => dispatch({ type: "replace", key: status.key, item: textItem("error", "Network error — try again.") }))
            .finally(() => {
                shell.input.value = "";
                refocus.current = true;
                setBusyBoth(false);
            });
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
        setBusyBoth(true);
        joinRun(threadId, runId)
            .then(async (joinRes) => {
                if (joinRes.ok && joinRes.body) await streamInto(joinRes, joinStatus.key);
                return fetchState(threadId);
            })
            .then((state: ThreadState | null) => {
                if (state) {
                    const interrupt = state.interrupts?.[0]?.value ?? null;
                    session.current.pendingInterrupt = Boolean(interrupt);
                    dispatch({ type: "load", items: threadItems(state.values?.messages ?? [], interrupt) });
                } else {
                    dispatch({ type: "replace", key: joinStatus.key, item: null });
                }
            })
            // Leave whatever rendered; the next send will surface real errors.
            .catch(() => {})
            .finally(() => setBusyBoth(false));
        // Both are fixed for the page's lifetime, so this runs once.
    }, [bootstrap, joinStatus]);

    const send = useEffectEvent(sendMessage);

    useEffect(() => {
        const onSend = () => send(shell.input.value.trim());
        const onKeyDown = (e: Event) => {
            if ((e as KeyboardEvent).key === "Enter") send(shell.input.value.trim());
        };
        const onSuggestion = (e: Event) => {
            const btn = (e.target as Element).closest("button");
            if (btn) send(btn.textContent!);
        };
        const onReset = () => {
            storeThread(null);
            session.current.threadId = null;
            session.current.pendingInterrupt = false;
            dispatch({ type: "load", items: [] });
        };
        const listeners: [HTMLElement, string, (e: Event) => void][] = [
            [shell.send, "click", onSend],
            [shell.input, "keydown", onKeyDown],
            [shell.suggestions, "click", onSuggestion],
            [shell.reset, "click", onReset],
        ];
        for (const [el, type, fn] of listeners) el.addEventListener(type, fn);
        return () => {
            for (const [el, type, fn] of listeners) el.removeEventListener(type, fn);
        };
    }, [shell]);

    return items.map((item) => (item.kind === "ticket" ? <Ticket key={item.key} item={item} onReply={answerTicket} /> : <Message key={item.key} item={item} />));
}
