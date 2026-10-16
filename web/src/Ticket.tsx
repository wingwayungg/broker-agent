import type { MouseEvent } from "react";
import { ticketParts } from "../../app/static/render.js";
import type { InterruptValue } from "../../app/static/render.js";

export interface TicketItem {
    key: number;
    kind: "ticket";
    value: InterruptValue;
    answered: boolean;
}

export function Ticket({ item, onReply }: { item: TicketItem; onReply: (key: number, reply: string) => void }) {
    const { cls, body } = ticketParts(item.value, item.answered);
    // The buttons are in render.js's HTML, so their clicks are caught here.
    const onClick = (e: MouseEvent) => {
        const btn = (e.target as Element).closest<HTMLButtonElement>("button[data-reply]");
        if (btn && !btn.disabled) onReply(item.key, btn.dataset.reply!);
    };
    return <div className={cls} onClick={onClick} dangerouslySetInnerHTML={{ __html: body }} />;
}
