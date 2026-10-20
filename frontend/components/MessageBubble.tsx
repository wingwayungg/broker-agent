import { memo } from "react";
import type { TextItem } from "@/lib/items";
import { renderMarkdown } from "@/lib/markdown";

const CLASSES = { user: "msg user", assistant: "msg assistant", status: "msg system pending", error: "msg error" };

// Memoized so a streaming reply re-renders only its own bubble's markdown.
export const MessageBubble = memo(function MessageBubble({ item }: { item: TextItem }) {
    if (item.kind === "assistant") {
        return <div className={CLASSES.assistant}>{renderMarkdown(item.text)}</div>;
    }
    return <div className={CLASSES[item.kind]}>{item.text}</div>;
});
