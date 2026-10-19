"use client";

import { usePathname, useRouter } from "next/navigation";
import { useChatShell } from "./ChatShell";

export function ResetButton() {
    const { controls } = useChatShell();
    const pathname = usePathname();
    const router = useRouter();
    // Before <Chat> mounts (server render, loading, error), a thread URL means
    // there is a conversation to leave, and leaving it is a navigation home.
    const show = controls ? !controls.empty : pathname.startsWith("/thread/");
    if (!show) return null;
    return (
        <button
            id="reset"
            type="button"
            onClick={controls ? controls.reset : () => router.push("/")}
            disabled={controls?.busy ?? false}
        >
            New conversation
        </button>
    );
}
