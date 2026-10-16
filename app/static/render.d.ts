// Types for render.js, which stays plain JS because the server evals it in V8.

// buy_stock's interrupt payload (app/tools.py). A payload without `symbol`
// renders as its plain `message`.
export interface InterruptValue {
    type: string;
    step: number;
    of: number;
    message: string;
    symbol?: string;
    quantity?: number;
    estimated_price?: number | null;
    estimated_cost?: number | null;
}

export function messageBody(text: string, cls: string): string;

export function ticketParts(value: InterruptValue, answered: boolean): { cls: string; body: string };
