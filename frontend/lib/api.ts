// Browser-side calls to the agent API, through the same-origin /api proxy
// (app/api/[...path]/route.ts).

import type { ThreadView } from "./types";

const JSON_HEADERS = { "Content-Type": "application/json" };

export async function createThread(): Promise<string> {
    const res = await fetch("/api/threads", { method: "POST" });
    if (!res.ok) throw new Error(`POST /api/threads ${res.status}`);
    return (await res.json()).thread_id;
}

// Streams the run as server-sent events; 409 if the thread already has one running.
export function postMessage(threadId: string, text: string) {
    return fetch(`/api/threads/${threadId}/messages`, {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({ text }),
    });
}

// The thread's run in progress, replayed from its start; 204 if none.
export function joinRun(threadId: string, signal: AbortSignal) {
    return fetch(`/api/threads/${threadId}/stream`, { signal });
}

export async function readThread(threadId: string, signal?: AbortSignal): Promise<ThreadView | null> {
    const res = await fetch(`/api/threads/${threadId}`, { signal });
    return res.ok ? res.json() : null;
}
