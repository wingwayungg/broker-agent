import { createRoot } from "react-dom/client";
import { App } from "./App";
import type { Bootstrap, Shell } from "./types";

// frontend.py renders the page's static parts; React owns only #messages,
// re-rendering the history the server put there from the same messages in
// #bootstrap (through the same render.js).
const byId = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const shell: Shell = {
    log: byId("log"),
    reset: byId("reset"),
    input: byId("input"),
    send: byId("send"),
    suggestions: document.querySelector(".suggestions")!,
};
const data = byId("bootstrap")?.textContent;
const bootstrap: Bootstrap | null = data ? JSON.parse(data) : null;

createRoot(byId("messages")).render(<App bootstrap={bootstrap} shell={shell} />);
