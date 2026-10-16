import { memo } from "react";
import { messageBody } from "../../app/static/render.js";

export interface TextItem {
    key: number;
    kind: "user" | "assistant" | "status" | "error";
    text: string;
}

const CLASSES = { user: "msg user", assistant: "msg assistant", status: "msg system pending", error: "msg error" };

// Memoized so a streaming reply re-renders only its own bubble's markdown.
export const Message = memo(function Message({ item }: { item: TextItem }) {
    if (item.kind === "assistant") {
        return <div className={CLASSES.assistant} dangerouslySetInnerHTML={{ __html: messageBody(item.text, "assistant") }} />;
    }
    return <div className={CLASSES[item.kind]}>{item.text}</div>;
});
