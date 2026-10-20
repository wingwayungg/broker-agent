import { memo } from "react";
import type { TicketItem } from "@/lib/items";

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

// buy_stock's confirmation as an order ticket. Once answered its buttons are
// disabled, so it can't send another "yes" later.
export const OrderTicket = memo(function OrderTicket({ item, onReply }: { item: TicketItem; onReply: (key: number, reply: string) => void }) {
    const { value } = item;
    const isFinal = value.step === value.of;
    const actions = (
        <div className="confirm-actions">
            <button className="primary" type="button" disabled={item.answered} onClick={() => onReply(item.key, "yes")}>
                {isFinal ? "Place order" : "Continue"}
            </button>
            <button type="button" disabled={item.answered} onClick={() => onReply(item.key, "no")}>
                Cancel
            </button>
        </div>
    );

    if (!value.symbol) {
        return (
            <div className="msg confirm">
                {value.message}
                {actions}
            </div>
        );
    }
    return (
        <div className="msg ticket">
            <div className="ticket-head">
                <span>{isFinal ? "Final confirmation" : "Confirm buy order"}</span>
                <span>
                    Step {value.step} of {value.of}
                </span>
            </div>
            <div className="ticket-body">
                <div className="ticket-order">
                    <span className="ticket-symbol">{value.symbol}</span>
                    <span className="ticket-qty">{value.quantity === 1 ? "1 share" : `${value.quantity} shares`}</span>
                </div>
                {value.estimated_price != null && (
                    <dl>
                        <dt>Est. price</dt>
                        <dd>{usd.format(value.estimated_price)}</dd>
                        <dt>Est. cost</dt>
                        <dd>{usd.format(value.estimated_cost ?? 0)}</dd>
                    </dl>
                )}
                {isFinal && <p className="ticket-warning">This submits the order and can&apos;t be undone.</p>}
                {actions}
            </div>
        </div>
    );
});
