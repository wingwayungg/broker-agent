import { describe, expect, it } from "vitest";
import { readEvents } from "./sse";
import type { RunEvent } from "./types";

// A run's stream as the API sends it, delivered `size` bytes at a time so
// records, CRLF pairs and multi-byte characters land split across reads.
function body(records: [string, unknown][], size: number, newline = "\r\n") {
    const text = records.map(([event, data]) => `event: ${event}${newline}data: ${JSON.stringify(data)}${newline}${newline}`).join("");
    const bytes = new TextEncoder().encode(text);
    return new ReadableStream<Uint8Array>({
        start(controller) {
            for (let i = 0; i < bytes.length; i += size) controller.enqueue(bytes.slice(i, i + size));
            controller.close();
        },
    });
}

async function collect(stream: ReadableStream<Uint8Array>) {
    const events: RunEvent[] = [];
    for await (const event of readEvents(stream)) events.push(event);
    return events;
}

const RUN: [string, unknown][] = [
    ["status", { label: "Researching NVDA" }],
    ["token", { id: "m1", text: "NVIDIA — " }],
    ["token", { id: "m1", text: "up 3% 📈" }],
    ["interrupt", { type: "confirm_buy_order", step: 1, of: 2, message: "Confirm?", symbol: "NVDA", quantity: 1 }],
    ["error", { message: "boom" }],
    ["done", {}],
];

const EXPECTED: RunEvent[] = [
    { type: "status", label: "Researching NVDA" },
    { type: "token", id: "m1", text: "NVIDIA — " },
    { type: "token", id: "m1", text: "up 3% 📈" },
    { type: "interrupt", value: { type: "confirm_buy_order", step: 1, of: 2, message: "Confirm?", symbol: "NVDA", quantity: 1 } },
    { type: "error", message: "boom" },
    { type: "done" },
];

describe("readEvents", () => {
    // Every read size up to a record's length, so a read ends at every offset,
    // including between the \r and \n of a record boundary.
    it.each(Array.from({ length: 60 }, (_, i) => i + 1))("parses a run delivered %i bytes at a time", async (size) => {
        expect(await collect(body(RUN, size))).toEqual(EXPECTED);
    });

    it("accepts LF line endings and skips unknown or malformed records", async () => {
        const stream = body([["mystery", {}], ...RUN], 4096, "\n");
        expect(await collect(stream)).toEqual(EXPECTED);
    });
});
