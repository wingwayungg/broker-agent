// The chat log as a keyed list of bubbles, and the reducer the Chat component
// drives it with.

import type { InterruptValue, ThreadView } from "./types";

export interface TextItem {
    key: number;
    kind: "user" | "assistant" | "status" | "error";
    text: string;
}

export interface TicketItem {
    key: number;
    kind: "ticket";
    value: InterruptValue;
    answered: boolean;
}

export type Item = TextItem | TicketItem;

export type Action =
    | { type: "append"; item: Item }
    | { type: "text"; key: number; text: string }
    | { type: "replace"; key: number; item: Item | null }
    | { type: "answer"; key: number }
    | { type: "load"; items: Item[] };

let lastKey = 0;
const nextKey = () => ++lastKey;

export const textItem = (kind: TextItem["kind"], text: string): TextItem => ({ key: nextKey(), kind, text });

export const ticketItem = (value: InterruptValue): TicketItem => ({ key: nextKey(), kind: "ticket", value, answered: false });

// An action naming a key that is gone (the log was reset or reloaded since) is a no-op.
export function reducer(items: Item[], action: Action): Item[] {
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

export function threadItems(thread: Pick<ThreadView, "messages" | "interrupt">): Item[] {
    const items: Item[] = thread.messages.map((m) => textItem(m.role, m.content));
    if (thread.interrupt) items.push(ticketItem(thread.interrupt));
    return items;
}
