import type { InterruptValue, RunEvent } from "./types";

function toRunEvent(raw: string): RunEvent | null {
    let event = "message";
    const dataLines: string[] = [];
    for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    if (!dataLines.length) return null;
    let data;
    try {
        data = JSON.parse(dataLines.join("\n"));
    } catch {
        return null;
    }
    switch (event) {
        case "token":
            return { type: "token", id: data.id, text: data.text };
        case "status":
            return { type: "status", label: data.label };
        case "interrupt":
            return { type: "interrupt", value: data as InterruptValue };
        case "done":
            return { type: "done" };
        case "error":
            return { type: "error", message: data.message };
        default:
            return null;
    }
}

// A run's server-sent events (schema in backend/api/schemas.py), parsed from a
// fetch body as they arrive.
export async function* readEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<RunEvent> {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
        const { done, value } = await reader.read();
        if (done) return;
        // Normalized over the whole buffer, not just this chunk: a read can end
        // between the \r and \n of a CRLF record boundary.
        buf = (buf + decoder.decode(value, { stream: true })).replaceAll("\r\n", "\n");
        let idx;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
            const event = toRunEvent(buf.slice(0, idx));
            buf = buf.slice(idx + 2);
            if (event) yield event;
        }
    }
}
