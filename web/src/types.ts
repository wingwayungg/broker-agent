import type { InterruptValue } from "../../app/static/render.js";
import type { MessageItem } from "./Message";
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

export type Item = MessageItem | TicketItem;
