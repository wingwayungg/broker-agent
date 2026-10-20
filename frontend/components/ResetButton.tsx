"use client";

import { usePathname, useRouter } from "next/navigation";
import { useChat } from "./ChatShell";

export function ResetButton() {
    const live = useChat((s) => s.live);
    const empty = useChat((s) => s.items.length === 0);
    const busy = useChat((s) => s.busy);
    const reset = useChat((s) => s.reset);
    const pathname = usePathname();
    const router = useRouter();
    // Before <Chat> mounts (server render, loading, error), a thread URL means
    // there is a conversation to leave, and leaving it is a navigation home.
    const show = live ? !empty : pathname.startsWith("/thread/");
    if (!show) return null;
    return (
        <button
            id="reset"
            type="button"
            onClick={live ? reset : () => router.push("/")}
            disabled={busy}
        >
            New conversation
        </button>
    );
}
