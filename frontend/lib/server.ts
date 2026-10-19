// Server-side access to the agent API, which listens on localhost next to the
// Next.js server (scripts/start.sh), so server rendering skips the proxy.

import { cache } from "react";
import type { ThreadView } from "./types";

export const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";

export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Cached per request, so the page and its metadata share one read.
export const getThread = cache(async (threadId: string): Promise<ThreadView | null> => {
    const res = await fetch(`${API_URL}/api/threads/${threadId}`, { cache: "no-store" });
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`GET /api/threads/${threadId} ${res.status}`);
    return res.json();
});
