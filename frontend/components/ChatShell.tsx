"use client";

import { createContext, useContext, useState } from "react";
import { useStore } from "zustand";
import { type ChatState, type ChatStore, IDLE } from "@/lib/chatStore";

// The mounted <Chat>'s store, lent to the layout's Header and Composer. Null
// while no Chat is mounted: during the server render, loading.tsx and error.tsx.
// Two contexts so <Chat>, which only sets it, doesn't re-render when it changes.
const StoreContext = createContext<ChatStore | null>(null);
const SetStoreContext = createContext<((store: ChatStore | null) => void) | null>(null);

export function ChatShellProvider({ children }: { children: React.ReactNode }) {
    const [store, setStore] = useState<ChatStore | null>(null);
    return (
        <SetStoreContext.Provider value={setStore}>
            <StoreContext.Provider value={store}>{children}</StoreContext.Provider>
        </SetStoreContext.Provider>
    );
}

// Re-renders only when the selected slice changes; reads IDLE while no Chat is mounted.
export function useChat<T>(selector: (state: ChatState) => T): T {
    return useStore(useContext(StoreContext) ?? IDLE, selector);
}

export function useSetChatStore() {
    const setStore = useContext(SetStoreContext);
    if (!setStore) throw new Error("useSetChatStore outside ChatShellProvider");
    return setStore;
}
