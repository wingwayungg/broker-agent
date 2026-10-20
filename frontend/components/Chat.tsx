"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useStore } from "zustand";
import { createChatStore } from "@/lib/chatStore";
import type { ThreadView } from "@/lib/types";
import { useSetChatStore } from "./ChatShell";
import { WelcomeHero } from "./Heroes";
import { MessageBubble } from "./MessageBubble";
import { OrderTicket } from "./OrderTicket";

export function Chat({ initial }: { initial: ThreadView | null }) {
    const [store] = useState(() => createChatStore(initial));
    const items = useStore(store, (s) => s.items);
    const setStore = useSetChatStore();
    const log = useRef<HTMLDivElement>(null);

    useLayoutEffect(() => {
        if (log.current) log.current.scrollTop = log.current.scrollHeight;
    }, [items]);

    // Layout, not passive, so a client navigation never paints the layout's
    // Composer disabled between loading.tsx and this page.
    useLayoutEffect(() => {
        setStore(store);
        return () => setStore(null);
    }, [store, setStore]);

    useEffect(() => {
        const controller = new AbortController();
        store.getState().join(controller.signal);
        return () => controller.abort();
    }, [store]);

    const empty = items.length === 0;
    return (
        <div id="log" ref={log} className={empty ? "empty" : ""}>
            <div id="log-inner">
                {empty && <WelcomeHero />}
                {items.map((item) =>
                    item.kind === "ticket" ? (
                        <OrderTicket key={item.key} item={item} />
                    ) : (
                        <MessageBubble key={item.key} item={item} />
                    ),
                )}
            </div>
        </div>
    );
}
