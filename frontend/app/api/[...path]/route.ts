// Same-origin proxy from the browser to the agent API, which only listens on
// localhost. A route handler rather than a rewrite, so long event streams pass
// through as they arrive with no proxy timeout, and a closed tab aborts the
// upstream request (the run itself carries on; see backend/api/runs.py).

import { API_URL } from "@/lib/server";

// Hop-by-hop, or describing an encoding fetch has already undone.
const DROPPED_HEADERS = ["host", "connection", "content-length", "content-encoding", "transfer-encoding"];

async function proxy(req: Request, { params }: { params: Promise<{ path: string[] }> }) {
    const { path } = await params;
    const target = `${API_URL}/api/${path.map(encodeURIComponent).join("/")}${new URL(req.url).search}`;
    const headers = new Headers(req.headers);
    for (const name of DROPPED_HEADERS) headers.delete(name);
    let upstream: Response;
    try {
        upstream = await fetch(target, {
            method: req.method,
            headers,
            body: req.method === "GET" || req.method === "HEAD" ? undefined : req.body,
            signal: req.signal,
            redirect: "manual",
            // Required by Node to send a streamed request body.
            duplex: "half",
        } as RequestInit);
    } catch {
        return Response.json({ detail: "The agent API is unavailable." }, { status: 502 });
    }
    const responseHeaders = new Headers(upstream.headers);
    for (const name of DROPPED_HEADERS) responseHeaders.delete(name);
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export { proxy as GET, proxy as POST };
