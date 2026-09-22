"""
Minimal browser chat UI, mounted onto the LangGraph API server via the
`http.app` hook in langgraph.json. Deliberately a plain Starlette app (not
FastAPI) so it doesn't register its own /docs, /redoc, /openapi.json and
shadow LangGraph's own API explorer at those paths.

The page talks directly to the LangGraph API's own /threads,
/threads/{id}/runs/stream and /threads/{id}/state endpoints (same origin,
so no CORS needed) rather than going through the ask() helper that
local_api_cli/api.py uses.
That matters for buy_stock's two-step confirmation: the API distinguishes a
fresh message (input) from a resume (command.resume) as separate request
fields, so the page can know from the previous response's __interrupt__
field which one to send next, instead of guessing at plain text the way
ask() does. On load the page rebuilds its log from the thread's state (and
re-joins a still-running run), so a reload mid-reply doesn't blank it.
"""
import hashlib
from pathlib import Path
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.responses import HTMLResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static"

# Content hashes appended as cache-busting query params, so browsers can cache
# these files aggressively (via StaticFiles' Last-Modified/ETag) without
# serving a stale one across deploys that change it -- see the
# interrupt-payload note in the module docstring for why that matters for
# app.js specifically.
_APP_JS_VERSION = hashlib.sha256((STATIC_DIR / "app.js").read_bytes()).hexdigest()[:8]
_STYLE_CSS_VERSION = hashlib.sha256((STATIC_DIR / "style.css").read_bytes()).hexdigest()[:8]

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
    "fill='none' stroke='#0a0a0a' stroke-width='1.3' stroke-linecap='round'>"
    "<line x1='6' y1='13' x2='6' y2='14'/>"
    "<line x1='6' y1='18' x2='6' y2='20'/>"
    "<rect x='4' y='14' width='4' height='4' fill='#0a0a0a' stroke='none'/>"
    "<line x1='12' y1='3' x2='12' y2='6'/>"
    "<line x1='12' y1='13' x2='12' y2='17'/>"
    "<rect x='10' y='6' width='4' height='7'/>"
    "<line x1='18' y1='8' x2='18' y2='10'/>"
    "<line x1='18' y1='16' x2='18' y2='19'/>"
    "<rect x='16' y='10' width='4' height='6' fill='#0a0a0a' stroke='none'/>"
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
<link rel="stylesheet" href="/static/style.css?v=__STYLE_CSS_VERSION__">
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
<script src="/static/app.js?v=__APP_JS_VERSION__" defer></script>
</body>
</html>"""

PAGE = (
    PAGE.replace("__FAVICON_HREF__", FAVICON_HREF)
    .replace("__APP_JS_VERSION__", _APP_JS_VERSION)
    .replace("__STYLE_CSS_VERSION__", _STYLE_CSS_VERSION)
    .replace('<span class="logo"></span>', LOGO_SVG)
)


def index(_request):
    return HTMLResponse(PAGE)


# Exact-match Route for "/" plus a *prefixed* static mount. langgraph_api
# splices these routes ahead of its own, so a Mount("/") would prefix-match
# /threads, /assistants, /docs etc. and swallow the whole API.
app = Starlette(
    routes=[
        Route("/", index, methods=["GET"]),
        Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
    ]
)
