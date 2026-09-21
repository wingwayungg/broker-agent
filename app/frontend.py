"""
Minimal browser chat UI, mounted onto the LangGraph API server via the
`http.app` hook in langgraph.json. Deliberately a plain Starlette app (not
FastAPI) so it doesn't register its own /docs, /redoc, /openapi.json and
shadow LangGraph's own API explorer at those paths.

The page talks directly to the LangGraph API's own /threads and
/threads/{id}/runs/wait endpoints (same origin, so no CORS needed) rather
than going through app/main.py's ask() helper. That matters for
buy_stock's two-step confirmation: the API distinguishes a fresh message
(input) from a resume (command.resume) as separate request fields, so the
page can know from the previous response's __interrupt__ field which one
to send next, instead of guessing at plain text the way ask() does.
"""
from pathlib import Path
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

STYLE_CSS = (Path(__file__).parent / "static" / "style.css").read_text()

# Ascending candlestick-bar mark. Inline (fill="currentColor") for the
# in-page logo so it follows --accent; the favicon variant hardcodes the
# same color since a data: URI has no page CSS context.
LOGO_SVG = (
    '<span class="logo"><svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
    '<rect width="24" height="24" rx="6" fill="currentColor"/>'
    '<rect x="5" y="14" width="3.2" height="5" rx="1" fill="white"/>'
    '<rect x="10.4" y="10" width="3.2" height="9" rx="1" fill="white"/>'
    '<rect x="15.8" y="6" width="3.2" height="13" rx="1" fill="white"/>'
    "</svg></span>"
)
_FAVICON_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'>"
    "<rect width='24' height='24' rx='6' fill='#0a0a0a'/>"
    "<rect x='5' y='14' width='3.2' height='5' rx='1' fill='white'/>"
    "<rect x='10.4' y='10' width='3.2' height='9' rx='1' fill='white'/>"
    "<rect x='15.8' y='6' width='3.2' height='13' rx='1' fill='white'/>"
    "</svg>"
)
FAVICON_HREF = "data:image/svg+xml," + quote(_FAVICON_SVG)

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Broker Portfolio Agent</title>
<link rel="icon" type="image/svg+xml" href="__FAVICON_HREF__">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/style.css">
</head>
<body>
<header>
  <div class="title"><span class="logo"></span>Broker Portfolio Agent</div>
  <button id="reset" class="hidden">New conversation</button>
</header>
<main>
  <div id="log" class="empty">
    <div id="log-inner">
      <div class="empty-hero">
        <span class="logo"></span>
        <h2>Broker Portfolio Agent</h2>
        <p>Ask about your positions, research a ticker, or place a trade.</p>
      </div>
    </div>
  </div>
</main>
<footer>
  <div id="input-card">
    <input id="input" type="text" placeholder="Ask about your portfolio..." autocomplete="off">
    <button id="send">Send</button>
  </div>
</footer>
<script>
let threadId = null;
let pendingInterrupt = false;
let busy = false;

const log = document.getElementById("log");
const logInner = document.getElementById("log-inner");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const resetBtn = document.getElementById("reset");

// Minimal GFM-ish markdown renderer for assistant replies: paragraphs,
// bold/italic/code, lists, and (the main point) tables, so a markdown
// table from the model renders as an actual <table> like agentchat's UI
// instead of raw pipe characters. Deliberately hand-rolled rather than a
// CDN markdown library, since this page has no other external script
// dependency and the surface area we need is small.
function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inlineMd(s) {
  s = escapeHtml(s);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>");
  return s;
}

function splitRow(line) {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
}

const TABLE_SEP_RE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/;

function tableToHtml(lines) {
  const header = splitRow(lines[0]);
  const aligns = splitRow(lines[1]).map((cell) => {
    const left = cell.startsWith(":");
    const right = cell.endsWith(":");
    if (left && right) return "center";
    if (right) return "right";
    return "left";
  });
  let html = '<div class="table-wrap"><table><thead><tr>';
  header.forEach((h, i) => {
    html += `<th style="text-align:${aligns[i] || "left"}">${inlineMd(h)}</th>`;
  });
  html += "</tr></thead><tbody>";
  lines.slice(2).forEach((line) => {
    html += "<tr>";
    splitRow(line).forEach((c, i) => {
      html += `<td style="text-align:${aligns[i] || "left"}">${inlineMd(c)}</td>`;
    });
    html += "</tr>";
  });
  html += "</tbody></table></div>";
  return html;
}

function renderMarkdown(text) {
  // Strip ``` fence delimiters (with or without a language tag) rather than
  // rendering them as a literal code block -- models sometimes wrap a
  // markdown table in a ```markdown fence, and we still want that table
  // parsed as a table, not shown as one big preformatted blob.
  const lines = text.split("\n").filter((l) => !/^\s*```(\w*)?\s*$/.test(l));
  let html = "";
  let i = 0;
  const isListLine = (l) => /^\s*[-*]\s+/.test(l);
  const isOrderedLine = (l) => /^\s*\d+\.\s+/.test(l);
  const isTableStart = (idx) =>
    lines[idx].trim().startsWith("|") && idx + 1 < lines.length && TABLE_SEP_RE.test(lines[idx + 1]);

  while (i < lines.length) {
    if (lines[i].trim() === "") {
      i++;
      continue;
    }
    if (isTableStart(i)) {
      const tableLines = [lines[i], lines[i + 1]];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        tableLines.push(lines[i]);
        i++;
      }
      html += tableToHtml(tableLines);
      continue;
    }
    if (isListLine(lines[i])) {
      const items = [];
      while (i < lines.length && isListLine(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i++;
      }
      html += "<ul>" + items.map((it) => `<li>${inlineMd(it)}</li>`).join("") + "</ul>";
      continue;
    }
    if (isOrderedLine(lines[i])) {
      const items = [];
      while (i < lines.length && isOrderedLine(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i++;
      }
      html += "<ol>" + items.map((it) => `<li>${inlineMd(it)}</li>`).join("") + "</ol>";
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() !== "" && !isTableStart(i) && !isListLine(lines[i]) && !isOrderedLine(lines[i])) {
      para.push(lines[i]);
      i++;
    }
    html += `<p>${para.map(inlineMd).join("<br>")}</p>`;
  }
  return html;
}

function setContent(div, text, cls) {
  if (cls === "assistant") {
    div.innerHTML = renderMarkdown(text);
  } else {
    div.textContent = text;
  }
}

function addMsg(text, cls) {
  log.classList.remove("empty");
  const div = document.createElement("div");
  div.className = "msg " + cls;
  setContent(div, text, cls);
  logInner.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

function addConfirm(text) {
  const div = addMsg(text, "confirm");
  const actions = document.createElement("div");
  actions.className = "confirm-actions";
  ["Yes", "No"].forEach((label) => {
    const btn = document.createElement("button");
    btn.textContent = label;
    btn.onclick = () => sendMessage(label.toLowerCase());
    actions.appendChild(btn);
  });
  div.appendChild(actions);
}

async function ensureThread() {
  const stored = sessionStorage.getItem("thread_id");
  if (stored) {
    threadId = stored;
    return;
  }
  const res = await fetch("/threads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  const data = await res.json();
  threadId = data.thread_id;
  sessionStorage.setItem("thread_id", threadId);
}

// Splits a text/event-stream chunk buffer into individual "event:"/"data:"
// records, tolerating records that arrive split across network reads.
function parseSSEEvent(raw) {
  let eventType = "message";
  const dataLines = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) eventType = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;
  try {
    return { eventType, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

function postRun(text, asResume) {
  const body = asResume
    ? { assistant_id: "agent", command: { resume: text }, stream_mode: ["messages", "updates"] }
    : {
        assistant_id: "agent",
        input: { messages: [{ role: "user", content: text }] },
        stream_mode: ["messages", "updates"],
      };
  return fetch(`/threads/${threadId}/runs/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function sendMessage(text) {
  if (busy || !text) return;
  busy = true;
  sendBtn.disabled = true;
  input.disabled = true;

  addMsg(text, "user");
  resetBtn.classList.remove("hidden");
  // Status bubble reused for "thinking" / "calling a tool" updates, then
  // either replaced by the streaming assistant reply or removed if a
  // confirmation interrupt arrives instead.
  let status = addMsg("Thinking...", "system");
  let assistantDiv = null;
  let assistantMsgId = null;
  let interrupted = false;

  try {
    await ensureThread();
    let res = await postRun(text, pendingInterrupt);

    if (res.status === 404) {
      // The thread doesn't exist server-side anymore (e.g. the server
      // restarted/redeployed since this tab last talked to it, wiping its
      // in-memory threads). Start a fresh thread transparently and resend
      // as a new message -- any pending confirmation from the old thread
      // is gone regardless, so a resume no longer makes sense.
      sessionStorage.removeItem("thread_id");
      threadId = null;
      pendingInterrupt = false;
      status.textContent = "Starting a new conversation...";
      await ensureThread();
      res = await postRun(text, false);
    }

    if (!res.ok || !res.body) {
      status.remove();
      addMsg("Something went wrong (" + res.status + "). Try again.", "error");
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      // Server sends CRLF line endings; normalize before boundary-matching.
      buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const record = parseSSEEvent(buf.slice(0, idx));
        buf = buf.slice(idx + 2);
        if (!record) continue;

        if (record.eventType === "updates" && record.data.__interrupt__) {
          interrupted = true;
          pendingInterrupt = true;
          status.remove();
          if (assistantDiv) assistantDiv.remove();
          addConfirm(record.data.__interrupt__[0].value.message);
        } else if (record.eventType === "messages/partial" || record.eventType === "messages/complete") {
          const msg = record.data[0];
          if (!msg || msg.type !== "ai") continue;
          if (msg.id !== assistantMsgId) {
            assistantMsgId = msg.id;
            if (!msg.content && msg.tool_calls && msg.tool_calls.length) continue;
          }
          if (msg.content) {
            if (!assistantDiv) {
              status.remove();
              assistantDiv = addMsg(msg.content, "assistant");
            } else {
              setContent(assistantDiv, msg.content, "assistant");
            }
            log.scrollTop = log.scrollHeight;
          } else if (msg.tool_calls && msg.tool_calls.length && !assistantDiv) {
            status.textContent = "Calling " + msg.tool_calls[0].name + "...";
          }
        }
      }
    }

    if (!interrupted) {
      pendingInterrupt = false;
      if (!assistantDiv) {
        status.remove();
        addMsg("(no response)", "assistant");
      }
    }
  } catch (err) {
    status.remove();
    addMsg("Network error — try again.", "error");
  } finally {
    busy = false;
    sendBtn.disabled = false;
    input.disabled = false;
    input.value = "";
    input.focus();
  }
}

sendBtn.onclick = () => sendMessage(input.value.trim());
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendMessage(input.value.trim());
});

resetBtn.onclick = () => {
  sessionStorage.removeItem("thread_id");
  threadId = null;
  pendingInterrupt = false;
  resetBtn.classList.add("hidden");
  log.classList.add("empty");
  logInner.innerHTML = `<div class="empty-hero">
        <span class="logo"></span>
        <h2>Broker Portfolio Agent</h2>
        <p>Ask about your positions, research a ticker, or place a trade.</p>
      </div>`;
};

ensureThread();
</script>
</body>
</html>"""

PAGE = PAGE.replace("__FAVICON_HREF__", FAVICON_HREF).replace(
    '<span class="logo"></span>', LOGO_SVG
)


async def index(request):
    return HTMLResponse(PAGE)


async def style(request):
    return Response(STYLE_CSS, media_type="text/css")


app = Starlette(
    routes=[
        Route("/", index, methods=["GET"]),
        Route("/style.css", style, methods=["GET"]),
    ]
)
