"use client";

import { createContext, useContext, useMemo, useState } from "react";

// What the mounted <Chat> lends the layout's Header and Composer. Null while no
// Chat is mounted: during the server render, loading.tsx and error.tsx.
export type ChatControls = { busy: boolean; empty: boolean; send: (text: string) => void; reset: () => void };

const ChatShellContext = createContext<{
    controls: ChatControls | null;
    setControls: (controls: ChatControls | null) => void;
} | null>(null);

export function ChatShellProvider({ children }: { children: React.ReactNode }) {
    const [controls, setControls] = useState<ChatControls | null>(null);
    const value = useMemo(() => ({ controls, setControls }), [controls]);
    return <ChatShellContext.Provider value={value}>{children}</ChatShellContext.Provider>;
}

export function useChatShell() {
    const context = useContext(ChatShellContext);
    if (!context) throw new Error("useChatShell outside ChatShellProvider");
    return context;
}
