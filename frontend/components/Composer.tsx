"use client";

import { useEffect, useRef, useState } from "react";

export function Composer({ disabled, onSend }: { disabled: boolean; onSend?: (text: string) => void }) {
    const [text, setText] = useState("");
    const input = useRef<HTMLInputElement>(null);
    // Focus returns after a send this box made, not when a page that re-joined
    // a run becomes idle (that would pop the keyboard on a phone).
    const sent = useRef(false);

    useEffect(() => {
        if (!disabled && sent.current) {
            sent.current = false;
            input.current?.focus();
        }
    }, [disabled]);

    return (
        <footer>
            <form
                id="input-card"
                onSubmit={(e) => {
                    e.preventDefault();
                    const message = text.trim();
                    if (!message || disabled || !onSend) return;
                    sent.current = true;
                    setText("");
                    onSend(message);
                }}
            >
                <input
                    id="input"
                    ref={input}
                    type="text"
                    placeholder="Ask the AI assistant about your portfolio, a stock, or a 10-K..."
                    autoComplete="off"
                    aria-label="Message"
                    value={text}
                    disabled={disabled}
                    onChange={(e) => setText(e.target.value)}
                />
                <button id="send" type="submit" disabled={disabled}>
                    Send
                </button>
            </form>
        </footer>
    );
}
