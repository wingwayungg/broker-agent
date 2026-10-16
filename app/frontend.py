"""
Minimal browser chat UI, mounted onto the LangGraph API server via the
`http.app` hook in langgraph.json. Deliberately a plain Starlette app (not
FastAPI) so it doesn't register its own /docs, /redoc, /openapi.json and
shadow LangGraph's own API explorer at those paths.

The page talks directly to the LangGraph API's own /threads,
/threads/{id}/runs/stream and /threads/{id}/state endpoints (same origin,
so no CORS needed) rather than going through the ask() helper in
local_api_cli/session.py that the local CLI and FastAPI use.
That matters for buy_stock's two-step confirmation: the API distinguishes a
fresh message (input) from a resume (command.resume) as separate request
fields, so the page can know from the previous response's __interrupt__
field which one to send next, instead of guessing at plain text the way
ask() does.

A ?thread_id= page is streamed: the shell (showing "Loading...") goes out at
once, and the thread's history follows in the same response once Atlas answers,
already rendered, so it paints without waiting on app.js. The server renders it
by running static/render.js in V8, the same code the page uses for streamed
replies, so there is one renderer rather than a Python port to keep in sync.
app.js joins a still-running run itself rather than the response staying open.
"""

import asyncio
import hashlib
import json
import re
from contextlib import suppress
from functools import cache
from pathlib import Path
from urllib.parse import quote

from langgraph_sdk import get_client
from langgraph_sdk.errors import APIError, NotFoundError
from py_mini_racer import MiniRacer
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, RedirectResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static"

# Content hashes appended as cache-busting query params, so the files can be
# cached as immutable (see _VersionedStatic) without a stale one surviving a
# deploy that changes it.
_APP_JS_VERSION, _RENDER_JS_VERSION, _STYLE_CSS_VERSION = (
    hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:8]
    for name in ("app.js", "render.js", "style.css")
)

# 3-candlestick mark (irregular heights, mixed hollow/solid bodies). Inline
# (stroke/fill="currentColor") for the in-page logo so it follows --accent;
# the favicon variant hardcodes the same color since a data: URI has no
# page CSS context. Each wick is drawn as two segments (above/below the
# body) rather than one line so it doesn't cut through the hollow body.
LOGO_SVG = (
    '<span class="logo"><svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" '
    'fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round">'
    '<line x1="6" y1="13" x2="6" y2="14"/>'
    '<line x1="6" y1="18" x2="6" y2="20"/>'
    '<rect x="4" y="14" width="4" height="4" fill="currentColor" stroke="none"/>'
    '<line x1="12" y1="3" x2="12" y2="6"/>'
    '<line x1="12" y1="13" x2="12" y2="17"/>'
    '<rect x="10" y="6" width="4" height="7"/>'
    '<line x1="18" y1="8" x2="18" y2="10"/>'
    '<line x1="18" y1="16" x2="18" y2="19"/>'
    '<rect x="16" y="10" width="4" height="6" fill="currentColor" stroke="none"/>'
    "</svg></span>"
)
_FAVICON_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' "
    "fill='none' stroke='#1e3a64' stroke-width='1.3' stroke-linecap='round'>"
    "<line x1='6' y1='13' x2='6' y2='14'/>"
    "<line x1='6' y1='18' x2='6' y2='20'/>"
    "<rect x='4' y='14' width='4' height='4' fill='#1e3a64' stroke='none'/>"
    "<line x1='12' y1='3' x2='12' y2='6'/>"
    "<line x1='12' y1='13' x2='12' y2='17'/>"
    "<rect x='10' y='6' width='4' height='7'/>"
    "<line x1='18' y1='8' x2='18' y2='10'/>"
    "<line x1='18' y1='16' x2='18' y2='19'/>"
    "<rect x='16' y='10' width='4' height='6' fill='#1e3a64' stroke='none'/>"
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
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/style.css?v=__STYLE_CSS_VERSION__">
<script src="/static/render.js?v=__RENDER_JS_VERSION__" defer></script>
<script src="/static/app.js?v=__APP_JS_VERSION__" defer></script>
</head>
<body>
<header>
  <div class="title"><span class="logo"></span>Broker Portfolio Agent</div>
  <button id="reset" class="hidden">New conversation</button>
</header>
<main>
  <div id="log" class="__LOG_CLASS__">
    <div id="log-inner">
      <div class="loading-hero">
        <span class="logo"></span>
        <h2>Loading...</h2>
      </div>
      <div class="empty-hero">
        <span class="logo"></span>
        <h2>Broker Portfolio Agent</h2>
        <p>Ask about your positions, research a ticker, or place a trade.</p>
        <div class="suggestions">
          <button type="button">Show my positions</button>
          <button type="button">Research NVDA</button>
          <button type="button">Risks in Apple's latest 10-K</button>
        </div>
      </div>
    </div>
  </div>
</main>
<footer>
  <div id="input-card">
    <input id="input" type="text" placeholder="Ask the AI assistant about your portfolio, a stock, or a 10-K..." autocomplete="off">
    <button id="send">Send</button>
  </div>
</footer>
__THREAD__</body>
</html>"""

PAGE = (
    PAGE.replace("__FAVICON_HREF__", FAVICON_HREF)
    .replace("__APP_JS_VERSION__", _APP_JS_VERSION)
    .replace("__RENDER_JS_VERSION__", _RENDER_JS_VERSION)
    .replace("__STYLE_CSS_VERSION__", _STYLE_CSS_VERSION)
    .replace('<span class="logo"></span>', LOGO_SVG)
)


PAGE_HEAD, PAGE_TAIL = PAGE.split("__THREAD__")
PAGE_EMPTY = PAGE_HEAD.replace("__LOG_CLASS__", "empty") + PAGE_TAIL

# The history arrives after the footer has been sent, so it comes in a <template>
# that this moves into the log as soon as it is parsed, before app.js runs.
_SHOW_THREAD = (
    '<script>document.getElementById("log-inner").append(document.getElementById("thread").content);'
    'document.getElementById("log").className = "";'
    'document.getElementById("reset").classList.remove("hidden");</script>'
)

THREAD_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


@cache
def _renderer() -> MiniRacer:
    js = MiniRacer()
    js.eval((STATIC_DIR / "render.js").read_text())
    return js


def _thread_html(messages: list[dict], interrupt: dict | None) -> str:
    return str(_renderer().call("threadHtml", messages, interrupt))


async def _read_thread(thread_id: str) -> tuple[str, dict] | None:
    """The thread's log HTML and app.js's bootstrap data, or None if there is nothing to restore.

    Run status is read before state, so a run finishing in between isn't missed.
    """
    async with get_client() as client:
        try:
            runs = await client.runs.list(thread_id, limit=1)
        except NotFoundError:
            await client.threads.create(
                thread_id=thread_id, if_exists="do_nothing", metadata={"graph_id": "agent"}
            )
            runs = []
        state = await client.threads.get_state(thread_id)

    # Tool results (filing excerpts) are never rendered, so they don't go to V8.
    messages = [
        {"type": m["type"], "content": m["content"]}
        for m in state["values"].get("messages", [])  # pyright: ignore[reportAttributeAccessIssue]
        if m["type"] in ("human", "ai")
    ]
    interrupts = state["interrupts"]
    # Off the event loop: mini-racer binds to a loop running where it is created
    # (TestClient runs one per client), and otherwise starts its own.
    interrupt = interrupts[0]["value"] if interrupts else None
    html = await asyncio.to_thread(_thread_html, messages, interrupt)
    active_run_id = next((r["run_id"] for r in runs if r["status"] in ("pending", "running")), None)
    if not html and not active_run_id:
        return None
    bootstrap = {
        "thread_id": thread_id,
        "pending_interrupt": bool(interrupts),
        "active_run_id": active_run_id,
    }
    return html, bootstrap


async def _stream_thread(thread_id: str):
    yield PAGE_HEAD.replace("__LOG_CLASS__", "loading")
    thread = None
    with suppress(APIError):
        thread = await _read_thread(thread_id)
    if thread is None:
        # Too late to redirect; without a bootstrap app.js shows the welcome screen.
        yield '<script>history.replaceState(null, "", "/")</script>'
    else:
        html, bootstrap = thread
        if html:
            yield f'<template id="thread">{html}</template>{_SHOW_THREAD}'
        data = json.dumps(bootstrap).replace("<", "\\u003c")
        yield f'<script id="bootstrap" type="application/json">{data}</script>'
    yield PAGE_TAIL


async def index(request):
    thread_id = request.query_params.get("thread_id")
    if thread_id is None:
        return HTMLResponse(PAGE_EMPTY)
    if not THREAD_ID_RE.fullmatch(thread_id):
        return RedirectResponse("/", status_code=307)
    return StreamingResponse(
        _stream_thread(thread_id), media_type="text/html", headers={"Cache-Control": "no-store"}
    )


class _VersionedStatic(StaticFiles):
    """Every page reference carries a ?v= content hash, so a cached copy is never stale.

    Without a Cache-Control header browsers guess freshness from Last-Modified, which
    right after a deploy means re-checking app.js and style.css with Fly on each load.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


# Exact-match Route for "/" plus a *prefixed* static mount. langgraph_api
# splices these routes ahead of its own, so a Mount("/") would prefix-match
# /threads, /assistants, /docs etc. and swallow the whole API.
app = Starlette(
    routes=[
        Route("/", index, methods=["GET"]),
        Mount("/static", _VersionedStatic(directory=STATIC_DIR), name="static"),
    ]
)
