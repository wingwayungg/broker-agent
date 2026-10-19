"use client";

import { usePathname, useRouter } from "next/navigation";
import { useChatShell } from "./ChatShell";
import { Logo } from "./Logo";

export function Header() {
    const { controls } = useChatShell();
    const pathname = usePathname();
    const router = useRouter();
    // Before <Chat> mounts (server render, loading, error), a thread URL means
    // there is a conversation to leave, and leaving it is a navigation home.
    const showReset = controls ? !controls.empty : pathname.startsWith("/thread/");
    return (
        <header>
            <div className="title">
                <Logo />
                Broker Portfolio Agent
            </div>
            {showReset && (
                <button
                    id="reset"
                    type="button"
                    onClick={controls ? controls.reset : () => router.push("/")}
                    disabled={controls?.busy ?? false}
                >
                    New conversation
                </button>
            )}
        </header>
    );
}
