import type { InterruptValue } from "../../app/static/render.js";
import type { Item } from "./types";

export type Action =
    | { type: "append"; item: Item }
    | { type: "text"; key: number; text: string }
    | { type: "replace"; key: number; item: Item | null }
    | { type: "answer"; key: number }
    | { type: "load"; items: Item[] };

let lastKey = 0;
const nextKey = () => ++lastKey;

export const textItem = (kind: "user" | "assistant" | "status" | "error", text: string): Item => ({
    key: nextKey(),
    kind,
    text,
});

export const ticketItem = (value: InterruptValue): Item => ({ key: nextKey(), kind: "ticket", value, answered: false });

// Actions naming a key that is gone (the log was reset or re-rendered from
// state meanwhile) are no-ops.
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

// The visible part of a thread, matching threadHtml in render.js: human
// messages and assistant replies with text, then a pending interrupt's ticket.
export function threadItems(messages: { type: string; content: string }[], interrupt: InterruptValue | null): Item[] {
    const items: Item[] = [];
    for (const m of messages) {
        if (m.type === "human") items.push(textItem("user", m.content));
        else if (m.type === "ai" && m.content) items.push(textItem("assistant", m.content));
    }
    if (interrupt) items.push(ticketItem(interrupt));
    return items;
}
