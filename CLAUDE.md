# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
source venv/bin/activate               # project venv at the repo root (see .vscode/settings.json)
make dev                               # API on :8000 + web app on http://localhost:3000 (use localhost, not 127.0.0.1: next dev blocks HMR there)
make test                              # pytest (backend/) + vitest (frontend/)
make lint                              # ruff, ruff format --check, pyright, eslint, tsc

# backend/ (Python paths below are relative to it)
pip install -r requirements-dev.txt    # exact lock; requirements.txt is the runtime-only lock the image installs
# Edit ranges in requirements.in / requirements-dev.in (never the .txt locks), then regenerate:
uv pip compile requirements.in --universal --python-version 3.14 -o requirements.txt [--upgrade]
uv pip compile requirements-dev.in --universal --python-version 3.14 -o requirements-dev.txt [--upgrade]
uvicorn api.main:app --reload --port 8000   # the API alone; docs at /api/docs
python -m pytest tests/test_api.py     # the API end to end with a scripted model
docker run -d -p 27017:27017 mongodb/mongodb-atlas-local   # then:
MONGODB_TEST_URI='mongodb://localhost:27017/?directConnection=true' python -m pytest tests/test_rag_atlas.py
python -m cli                          # terminal chat, in-process, no servers
langgraph dev                          # LangGraph Studio for debugging the graph (dev-only dependency)
python -m evals.filings_eval [--judge] # 10-K retrieval eval (network: EDGAR + Atlas via MONGODB_URI; --judge also uses the LLM)

# frontend/ (pnpm; packageManager pins the version)
pnpm -C frontend install
pnpm -C frontend build                 # standalone server in .next/standalone (what the image runs)
pnpm -C frontend lint | typecheck | test

fly deploy                             # builds the Dockerfile remotely; app name in fly.toml
```

Ruff is the linter and formatter and pyright the type checker (dev-only; `backend/pyproject.toml`, whose pyright `venvPath` points at the root venv). CI (`.github/workflows/ci.yml`) runs `make lint`/`make test`'s steps plus the Next.js build. The repo-root `.env` (found by `load_dotenv` walking up from `backend/app/config.py`; `langgraph.json` loads `../.env`) holds `CEREBRAS_API_KEY`, `TAVILY_API_KEY`, `SEC_USER_AGENT` and `MONGODB_URI` (10-K search), optional `LANGGRAPH_AES_KEY`/`CHECKPOINT_TTL_DAYS`/`MONGODB_DB`/`LLM_PROVIDER`/`CEREBRAS_MODEL`/`GROQ_API_KEY`/`GROQ_MODEL`/`USE_MOCK_BROKER`; `langgraph.json` also loads it.

## Architecture

Read the module docstrings first — each file explains *why* it is shaped the way it is (mock-first broker contract, `requests` instead of MCP/Tavily SDKs, replayable runs), and those rationales are deliberate.

Two halves: `backend/` is the Python project (agent package `app/`, HTTP API `api/`, terminal chat `cli/`, `tests/`, `evals/`, `notebooks/`), `frontend/` the Next.js app. Python imports are rooted at `backend/` (`app.*`, `api.*`), so run Python commands from there. Paths below are relative to their half.

### Layers and dependency direction

```
browser        frontend/ (Next.js: SSR pages, /api proxy route)
                                        |  HTTP + SSE
entry points   cli/ (terminal, ask())    api/ (FastAPI: main.py routes, runs.py RunManager, events.py)    langgraph.json (Studio)
                         \                     |                                   /
graph            app/agent.py   build_graph(checkpointer, model): agent node <-> ToolNode loop over MessagesState
                              |
tools            app/tools.py   (8 @tool functions; ALL_TOOLS)
                     /                    |                         \
domain    app/broker_client.py   app/research.py (3 sub-agents)   app/rag.py (10-K chunk/index/retrieve)
                     \                    /                         |
data           app/market_data.py  (Yahoo chart endpoint, Tavily /search)   app/sec_filings.py (EDGAR), MongoDB Atlas
                              |
config           app/config.py  (settings, get_llm(), get_embedder())      app/models.py (Pydantic contract)
```

`evals/` sits outside this stack (imports `app.rag`/`app.config`, never imported by `app/`) and is excluded from the Docker image.

Imports only go downward; `app/` never imports `api/` or `cli/`. `cli/` (terminal chat via `ask()` in `cli/session.py`, also used by the notebooks, which expect to be started from `backend/`) doesn't ship to Fly (`.dockerignore`). `app/tools.py` is the only place the LLM-facing surface is defined; `app/broker_client.py` is the only file meant to change when wiring a real broker (its methods raise `NotImplementedError` when `USE_MOCK_BROKER=false`). `app/models.py` is the return-type contract between the two — `Position.market_value` and `unrealized_pnl_pct` are computed properties, not stored fields, so a real broker only needs to supply `symbol/quantity/avg_cost/current_price`.

### One turn, end to end

1. Caller sends text for a `thread_id`. If the thread is paused on an interrupt, the text is a **resume value**; otherwise it is a new `("user", text)` message.
2. `call_model` prepends `SYSTEM_PROMPT` if the first message isn't a system message, stubs out `search_filings`/`research_stock` results from earlier turns (`compact_earlier_tool_results` in `app/tools.py` — Groq's free tier caps gpt-oss-120b at 8k tokens/minute, and old filing excerpts pushed later requests over it; the checkpointed thread keeps them whole), invokes the tool-bound LLM, and `should_continue` routes to `ToolNode` if the reply has `tool_calls`, else `END`.
3. `ToolNode` runs the tool. Read-only tools return formatted strings that the LLM turns into prose/tables on the next loop. `buy_stock` may instead hit `interrupt()`, which surfaces as `__interrupt__` in the result — the caller shows `value["message"]` and waits for a human.
4. Loop continues until the model replies without tool calls.

Memory is per `thread_id`, and the checkpoints are the thread: the API's graph runs with `mongo_checkpointer()` from `app/checkpointer.py` (Atlas `checkpoints`/`checkpoint_writes`, AES-encrypted when `LANGGRAPH_AES_KEY` is set, expired after `CHECKPOINT_TTL_DAYS` via a TTL index; in-memory fallback if `MONGODB_URI` is unset or Atlas is unreachable at startup). There is no thread record: `POST /api/threads` only mints a uuid, the first run writes the first checkpoint, and any id with a checkpoint reads back after a restart. The terminal chat uses its own `MemorySaver`.

### The API and the SSR frontend

`app/agent.py` has no import-time side effects: `build_graph(checkpointer=None, model=None)` binds `ALL_TOOLS` to `model or get_llm()` and compiles. The API builds it with the Atlas saver in `api/main.py`'s lifespan, `cli/session.py` with a `MemorySaver`, tests with a scripted model, and `platform_graph()` (a zero-arg factory for `langgraph.json`) with none, because LangGraph Studio supplies its own and refuses a graph that has one.

- **API** (`api/main.py`, models in `api/schemas.py`): `GET /api/health`, `POST /api/threads`, `GET /api/threads/{id}` (`ThreadView`: user messages and agent replies with text, the pending interrupt, `active_run`; 404 with no checkpoint and no run), `POST /api/threads/{id}/messages` (starts a run and streams it; 409 if one is running), `GET /api/threads/{id}/stream` (re-join; 204 if none). Ids are `UUID`-typed, so malformed ones are 422. Resume vs new message is decided server-side from `aget_state().interrupts`, the same rule as `ask()`. `read_thread` checks for an active run *before* reading state, so a run finishing between the two reads can't leave a page with neither the reply nor a run to join.
- **Runs** (`api/runs.py`): `RunManager.start` claims the thread synchronously (before any await), then drives `graph.astream(stream_mode=["messages", "updates"])` in an `asyncio.Task` that outlives the request. Each `Run` keeps its whole SSE event log; `follow()` replays from event 0 then follows live, so a re-joining page gets the complete reply. A client disconnect doesn't cancel the run. Runs live in this process (fine for one Fly machine; several would need Redis).
- **Events** (`api/events.py`, schema documented in `api/schemas.py`): `token {id, text}` deltas only from chunks whose metadata says `langgraph_node == "agent"` — `messages` mode also carries `research_stock`'s sub-agent and synthesis LLM calls from inside the tools node, and those must never reach the reply bubble. `status {label}` comes from the agent node's finished update (tool-call args stream in fragments); `TOOL_LABELS` lives here. Then `interrupt` (the payload as-is), `done`, `error`.
- **Frontend** (`frontend/`): `app/c/[threadId]/page.tsx` is a server component that reads the thread through `lib/server.ts` (`API_URL`, default `http://127.0.0.1:8000`, `cache()`d so `generateMetadata` shares the read) and server-renders `<Chat initial={thread}>`; a malformed or unknown id redirects to `/` (inside the stream, because `loading.tsx` makes the page stream). `components/Chat.tsx` (client) holds the log as a keyed item reducer (`lib/items.ts`), plays SSE through `lib/sse.ts`, re-joins on mount when `initial.active_run` (with an `AbortController`, so StrictMode's double mount is harmless), and on a new conversation's first send `history.replaceState`s to `/c/{id}` (Next keeps its router in sync, nothing remounts). Browser calls go to `/api/*` on the same origin; `app/api/[...path]/route.ts` proxies them to the API as a streaming route handler rather than a rewrite (rewrites have their own proxy timeout and compression). `next.config.ts` sets `compress: false` (gzip held SSE back into one chunk) and `output: "standalone"`, and redirects old `/?thread_id=` links to `/c/`.
- **Container** (`Dockerfile`, `scripts/start.sh`, `fly.toml` `internal_port = 3000`): a Node stage builds the standalone server; the Python image gets only its output and the `node` binary. `start.sh` runs uvicorn on 127.0.0.1:8000, waits for `/api/health`, then `node frontend/server.js` on :3000, and polls both PIDs (not `wait -n`, which macOS's bash 3.2 lacks) so either dying ends the container; it traps TERM/INT because bash as PID 1 otherwise ignores them. Node retitles its process to `next-server (...)`, so match it by port, not `pgrep -f server.js`.

### `buy_stock` is the only mutating tool, gated by `interrupt()`

The safety model is structural, not prompt-based: `buy_stock` validates quantity and buying power *before* any interrupt (so an unaffordable order returns immediately with no confirmation), then calls `interrupt()` twice. Each pause returns control to the caller; the LLM is not re-invoked until an external resume arrives, so it can never answer its own confirmation. Only `_is_affirmative` replies (`y`/`yes`/`confirm`/`confirmed`, case-insensitive) advance; anything else cancels. There is intentionally no sell/modify/cancel tool, and `test_buy_stock_is_the_only_order_placing_tool` asserts that.

The interrupt payload shape (`{"type": "confirm_buy_order", "step": 1|2, "of": 2, "message": ..., "symbol", "quantity", ...}`) is consumed by both `ask()` (reads `["message"]`) and the frontend (`InterruptValue` in `frontend/lib/types.ts`, rendered by `components/OrderTicket.tsx`, which falls back to `message` when the order fields are missing) — change them together. `tests/test_api.py` drives the whole confirmation flow through the API.

Tests exercise this against the real LangGraph engine by building a tiny `ToolNode`-only graph (`tests/test_tools.py::_build_tool_graph`) and driving it with a hand-built `AIMessage` carrying a `buy_stock` tool call, rather than mocking `interrupt`.

### Research sub-agents and live data

`research_stock` (`app/research.py`) is a fixed pipeline, not an agent: `_RESEARCHERS` (role → system prompt) and `_CONTEXT_FETCHERS` (role → live-data callable) are keyed by the same three roles (fundamentals, technicals, news_sentiment). Each role runs in a `ThreadPoolExecutor` as one LLM call fed its fetched context, then a synthesis call condenses the notes into exactly three paragraphs. Adding a role means adding a matching entry to both dicts.

`app/market_data.py` fetchers never raise to the caller: `fetch_price_snapshot`/`fetch_web_context` return bracketed `[... unavailable: ...]` strings, and `fetch_current_price` returns `None`. The sub-agent prompts tell the model to say data is missing rather than fill from training memory. Yahoo's `chartPreviousClose` is the range start, not yesterday's close — previous close is `closes[-2]`. `_fetch_chart_data` caches successful responses per symbol for 60s (in-process, bounded) because `research_stock` and `buy_stock`'s interrupt re-runs request the same symbol repeatedly; `tests/test_market_data.py` clears `_chart_cache` in an autouse fixture, and any new test that mocks Yahoo responses must do the same.

The mock `BrokerClient` also uses `fetch_current_price` for live position/quote prices, falling back to `_MOCK_QUOTES` only on `None` (a real `0.0` is preserved). Tests patch `app.broker_client.fetch_current_price` (autouse fixture in `test_tools.py`) and `app.market_data.requests.get/post` to stay offline.

### 10-K search (RAG)

`search_filings` is agentic RAG: the agent chooses when to call it and writes the `query`/`fiscal_year`/`section` arguments; the system prompt tells it to answer only from the excerpts and cite them as `[n]`. `app/sec_filings.py` fetches from EDGAR (ticker map → submissions JSON → primary 10-K HTML), converts inline-XBRL HTML to text with stdlib `HTMLParser` (skips `<ix:header>`/`display:none`, table cells joined by ` | `), and `split_sections` keeps Items 1/1A/7/7A. Every heading appears in the table of contents and some filers (Microsoft) repeat a bare "Item 1A" running header per page, so consecutive same-Item headings merge into a run and the longest run wins — test any parser change against real filings from several companies, not just the unit-test fixture.

`app/rag.py`'s `FilingIndex` ingests on demand (`ensure_indexed`: two latest 10-Ks, rechecked daily, a failed filing doesn't block the other, a stale index is served if EDGAR is down) into MongoDB Atlas, chosen because hybrid search is built in and the same cluster can later hold LangGraph chat checkpoints (which also holds the chat checkpoints, see `app/checkpointer.py`). Two collections: `chunks` (filing metadata denormalized onto each chunk, the text, a float32 BSON vector) and `companies` (last EDGAR check, `_id` = `variant/symbol`). Chunking is LangChain's `RecursiveCharacterTextSplitter` counting words. `retrieve` runs the aggregation `_pipeline` builds: `$rankFusion` over `$vectorSearch` (cosine) and `$search` (`lucene.english`), each prefiltered; `mode="bm25"|"dense"` exist for the eval. Filter values are BSON values, never strings, because the symbol comes from the LLM. Don't hand-roll ranking here — use what Atlas provides. Every chunk carries a `variant` (embedding model slug + chunk size) that every query and the companies key filter on, so two models' vectors or two chunkings never mix; it's one shared collection rather than one per variant because M0 allows only 3 search indexes per cluster and this uses 2 (`chunks_text`, `chunks_vector`, created in code if missing). The vector index's dimension is fixed: switching to a model of another size means dropping `chunks_vector`. Atlas Search indexes lag writes by ~1s, so `_index_filing` waits until both indexes see a new filing's chunks. `get_database()`/`get_filing_index()` connect lazily so importing `app.tools` never loads the model or connects; with `MONGODB_URI` unset or Atlas unreachable, `search_filings` returns a bracketed message rather than raising. `MONGODB_URI` is a per-app Fly secret; Fly has no static egress IP, so the Atlas network access list is `0.0.0.0/0`. Atlas Search can't be faked offline: `tests/test_rag.py` covers chunking, the built pipelines, and the tool against a stub index (patched into `rag._index`), while ingest/ranking tests are in `tests/test_rag_atlas.py`, skipped unless `MONGODB_TEST_URI` is set (Atlas Local container), each test on its own dropped database. Both use `tests/conftest.py`'s hashing fake embedder and `fake_edgar` (patches `app.rag.list_annual_reports`/`fetch_sections`).

Embedding model choice is memory-bound. The Dockerfile bakes the model from build args (`EMBEDDING_MODEL`, `EMBEDDING_DTYPE`) into `/models/<hub id>` at that precision and points `EMBEDDING_MODEL` at it — saving at build time matters because model2vec loads float32 weights before quantizing, so runtime quantization still pays the float32 peak. Both apps build the default, `potion-retrieval-32M` float16 (server peak ~315MB of 470MB on Fly; float16 scores the same as float32, and it beats `potion-base-8M` on dense/hybrid in the README eval table). Keep `app/config.py`'s `EMBEDDING_MODEL` default equal to the Dockerfile's ARG default. Re-run `evals/filings_eval.py` after any chunking/retrieval change and update the README table if the numbers move.

### LLM provider

`app/config.get_llm()` is the single switch (`cerebras` default with `gpt-oss-120b` via its OpenAI-compatible endpoint; `groq` and `gemini` alternatives, whose packages aren't in `requirements.txt` and must be installed first) and is `@cache`d, so every caller shares one client. It raises at startup if the selected provider's API key is unset — a Fly app that only has the old `GROQ_API_KEY` secret must set `CEREBRAS_API_KEY` (or `LLM_PROVIDER=groq`) before deploying. `build_graph()` calls it only when no model is passed, so importing `app.agent` creates no client; `tests/test_api.py` passes a `GenericFakeChatModel` subclass (`ScriptedModel`) that streams text word by word and tool calls in one chunk.

## Conventions

- Don't write comments that restate what the code does, teach a language or library feature (`MemorySaver`, dict ordering), suggest alternatives ("swap for X if you want"), or give example code for a stub. Assume the reader knows Python and LangGraph. Comment only a non-obvious *why*: an external quirk (Yahoo's `chartPreviousClose`, Fly suspending the VM), a deliberate trade-off, or a constraint the code can't show. Don't repeat in a comment what a name, docstring, or this file already says.
- The system prompt in `app/agent.py` asks for GFM tables for multi-row results; the frontend's hand-rolled `renderMarkdown` (`frontend/lib/markdown.ts`) only handles paragraphs, inline bold/italic/code, lists, and tables (right-aligning figure columns, coloring signed figures) — extend it if the prompt starts requesting other markdown. It runs during server rendering and in the browser, so keep it pure (string in, escaped HTML string out) and covered by `lib/markdown.test.ts`.
- The agent must not give buy/sell/hold advice; trading-decision questions are routed to `research_stock`. Keep new prompts and tool docstrings consistent with that.
- Deployment target is a 1GB Fly VM with no volume (`fly.toml`; it was 512MB until LanceDB, which has since been replaced by Atlas, so it may fit 512MB again once measured; older docstrings still cite 512MB); the README and `market_data.py` docstrings cite this when rejecting heavier dependencies. `.dockerignore` excludes tests, evals, notebooks, `cli/`, dev requirements and docs from the image.
