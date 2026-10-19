// The API's shapes, mirroring backend/api/schemas.py.

// buy_stock's confirmation payload (backend/app/tools.py). A payload without
// order fields still renders, from its message alone.
export interface InterruptValue {
    type?: string;
    step: number;
    of: number;
    message: string;
    symbol?: string;
    quantity?: number;
    estimated_price?: number | null;
    estimated_cost?: number | null;
}

export interface MessageView {
    role: "user" | "assistant";
    content: string;
}

export interface ThreadView {
    thread_id: string;
    messages: MessageView[];
    interrupt: InterruptValue | null;
    active_run: boolean;
}

export type RunEvent =
    | { type: "token"; id: string; text: string }
    | { type: "status"; label: string }
    | { type: "interrupt"; value: InterruptValue }
    | { type: "done" }
    | { type: "error"; message: string };
