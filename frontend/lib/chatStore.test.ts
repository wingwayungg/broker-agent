import { beforeEach, describe, expect, it, vi } from "vitest";
import { postMessage } from "./api";
import { createChatStore } from "./chatStore";
import type { ThreadView } from "./types";

vi.mock("./api", () => ({ createThread: vi.fn(), joinRun: vi.fn(), postMessage: vi.fn(), readThread: vi.fn() }));

function sse(records: [string, unknown][]) {
    const text = records.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join("");
    return new Response(text, { status: 200 });
}

const THREAD: ThreadView = { thread_id: "t1", messages: [{ role: "user", content: "hi" }], interrupt: null, active_run: false };

const kinds = (store: ReturnType<typeof createChatStore>) => store.getState().items.map((it) => (it.kind === "ticket" ? "ticket" : `${it.kind}:${it.text}`));

describe("chat store", () => {
    beforeEach(() => {
        vi.mocked(postMessage).mockReset();
    });

    it("streams a reply into the log and is busy until it ends", async () => {
        let release!: () => void;
        const gate = new Promise<void>((resolve) => (release = resolve));
        vi.mocked(postMessage).mockImplementation(() =>
            gate.then(() => sse([["status", { label: "Looking up positions" }], ["token", { id: "m1", text: "You hold " }], ["token", { id: "m1", text: "AAPL." }], ["done", {}]])),
        );
        const store = createChatStore(THREAD);

        store.getState().send("positions?");
        expect(store.getState().busy).toBe(true);
        expect(kinds(store)).toEqual(["user:hi", "user:positions?", "status:Thinking"]);

        await vi.waitFor(() => expect(postMessage).toHaveBeenCalledTimes(1));
        store.getState().send("ignored while busy");
        expect(kinds(store)).toHaveLength(3);

        release();
        await vi.waitFor(() => expect(store.getState().busy).toBe(false));
        expect(kinds(store)).toEqual(["user:hi", "user:positions?", "assistant:You hold AAPL."]);
    });

    it("turns an interrupt into a ticket that can be answered once", async () => {
        vi.mocked(postMessage).mockResolvedValueOnce(
            sse([["interrupt", { type: "confirm_buy_order", step: 1, of: 2, message: "Confirm?", symbol: "NVDA", quantity: 1 }], ["done", {}]]),
        );
        vi.mocked(postMessage).mockResolvedValueOnce(sse([["token", { id: "m2", text: "Cancelled." }], ["done", {}]]));
        const store = createChatStore(THREAD);

        store.getState().send("buy 1 NVDA");
        await vi.waitFor(() => expect(store.getState().busy).toBe(false));
        const ticket = store.getState().items.at(-1)!;
        expect(ticket.kind).toBe("ticket");

        store.getState().answer(ticket.key, "no");
        await vi.waitFor(() => expect(store.getState().busy).toBe(false));
        expect(store.getState().items.find((it) => it.key === ticket.key)).toMatchObject({ answered: true });
        expect(vi.mocked(postMessage).mock.calls.map((c) => c[1])).toEqual(["buy 1 NVDA", "no"]);
    });
});
