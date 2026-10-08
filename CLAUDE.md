# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
source venv/bin/activate               # project venv (see .vscode/settings.json)
pip install -r requirements.txt

python -m pytest                       # full suite, offline (~1s); Atlas tests skip without MONGODB_TEST_URI
docker run -d -p 27017:27017 mongodb/mongodb-atlas-local   # then:
MONGODB_TEST_URI='mongodb://localhost:27017/?directConnection=true' python -m pytest tests/test_rag_atlas.py
python -m pytest tests/test_tools.py::test_buy_stock_requires_two_affirmative_confirmations_before_submitting

python -m local_api_cli.cli            # terminal chat loop (in-process, MemorySaver)
uvicorn local_api_cli.api:app --reload # FastAPI: POST /ask {"question": ..., "thread_id": ...}
langgraph dev                          # LangGraph API on :2024 + browser chat UI at / (what Fly runs)
fly deploy                             # deploys the Dockerfile; app name in fly.toml
python -m evals.filings_eval [--judge] # 10-K retrieval eval (network: EDGAR + Atlas via MONGODB_URI; --judge also uses the LLM)
```

There is no linter/formatter config. `.env` holds `CEREBRAS_API_KEY`, `TAVILY_API_KEY`, `SEC_USER_AGENT` and `MONGODB_URI` (10-K search), optional `MONGODB_DB`/`LLM_PROVIDER`/`CEREBRAS_MODEL`/`GROQ_API_KEY`/`GROQ_MODEL`/`USE_MOCK_BROKER`; `langgraph.json` also loads it.

## Architecture

Read the module docstrings first — each file explains *why* it is shaped the way it is (mock-first broker contract, `requests` instead of MCP/Tavily SDKs, Starlette-not-FastAPI frontend), and those rationales are deliberate.

### Layers and dependency direction

```
entry points   local_api_cli/cli.py   local_api_cli/api.py (FastAPI /ask)   app/frontend.py (browser UI, via LangGraph API)
                         \                     |                                   |
                   ask() in local_api_cli/session.py           langgraph.json -> platform_graph
                                        \                             /
graph            app/agent.py   (agent node <-> ToolNode loop over MessagesState)
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

Imports only go downward. `local_api_cli/` holds the in-process runners (terminal chat and FastAPI `/ask`); they don't import each other, both just call `ask()` from `local_api_cli/session.py`. It's named for its two contents rather than "local" because `tests/` and `notebooks/` are local too. Nothing in `local_api_cli/` ships to Fly — it's in `.dockerignore` and its deps (`fastapi`, `uvicorn`) are dev-only. Run them as `python -m local_api_cli.cli` / `uvicorn local_api_cli.api:app`, not `python local_api_cli/cli.py`, so the repo root stays on `sys.path`. `app/tools.py` is the only place the LLM-facing surface is defined; `app/broker_client.py` is the only file meant to change when wiring a real broker (its methods raise `NotImplementedError` when `USE_MOCK_BROKER=false`). `app/models.py` is the return-type contract between the two — `Position.market_value` and `unrealized_pnl_pct` are computed properties, not stored fields, so a real broker only needs to supply `symbol/quantity/avg_cost/current_price`.

### One turn, end to end

1. Caller sends text for a `thread_id`. If the thread is paused on an interrupt, the text is a **resume value**; otherwise it is a new `("user", text)` message.
2. `call_model` prepends `SYSTEM_PROMPT` if the first message isn't a system message, stubs out `search_filings`/`research_stock` results from earlier turns (`compact_earlier_tool_results` in `app/tools.py` — Groq's free tier caps gpt-oss-120b at 8k tokens/minute, and old filing excerpts pushed later requests over it; the checkpointed thread keeps them whole), invokes the tool-bound LLM, and `should_continue` routes to `ToolNode` if the reply has `tool_calls`, else `END`.
3. `ToolNode` runs the tool. Read-only tools return formatted strings that the LLM turns into prose/tables on the next loop. `buy_stock` may instead hit `interrupt()`, which surfaces as `__interrupt__` in the result — the caller shows `value["message"]` and waits for a human.
4. Loop continues until the model replies without tool calls.

Memory is per `thread_id` and never in a real database (`MemorySaver` in-process, or LangGraph API's own store under `langgraph dev`, which pickles to `.langgraph_api/`). Either way it doesn't survive deployment: Fly's filesystem is ephemeral and the free tier spins down when idle, so a cold start comes back with no threads. The browser UI handles this by catching a 404 on its stored thread and transparently starting a new one.

### Two compiled graphs, two entry paths

`app/agent.py` exports both `agent` (compiled with `MemorySaver`, driven by `ask()` in `local_api_cli/session.py`) and `platform_graph` (compiled with **no** checkpointer, referenced by `langgraph.json`). LangGraph Platform/`langgraph dev` supplies its own checkpointer and errors if the graph already has one, so don't collapse these into one.

- **In-process path** (`local_api_cli/session.py::ask()`): infers whether the thread is paused via `agent.get_state(config).interrupts` and sends `Command(resume=text)` vs a new user message accordingly. Callers just keep passing whatever the user typed — including "yes"/"no".
- **LangGraph API path** (`app/frontend.py`): the browser page posts straight to `/threads/{id}/runs/stream` with `stream_mode: ["messages", "updates"]`, tracks `pendingInterrupt` from the previous response's `__interrupt__` update, and chooses `input` vs `command.resume` explicitly. It renders only AI messages a `messages/metadata` event attributes to `langgraph_node == "agent"` — `messages` stream mode also carries `research_stock`'s sub-agent and synthesis LLM calls from inside the tools node, and rendering those put the synthesis's three paragraphs in the reply bubble until the agent's own reply overwrote them. It never touches `ask()` or `local_api_cli/api.py`. `frontend.py` is mounted via `langgraph.json`'s `http.app` hook and is a Starlette app on purpose (FastAPI would shadow LangGraph's `/docs`). Under `langgraph dev`, `local_api_cli/api.py` is not served at all.

### `buy_stock` is the only mutating tool, gated by `interrupt()`

The safety model is structural, not prompt-based: `buy_stock` validates quantity and buying power *before* any interrupt (so an unaffordable order returns immediately with no confirmation), then calls `interrupt()` twice. Each pause returns control to the caller; the LLM is not re-invoked until an external resume arrives, so it can never answer its own confirmation. Only `_is_affirmative` replies (`y`/`yes`/`confirm`/`confirmed`, case-insensitive) advance; anything else cancels. There is intentionally no sell/modify/cancel tool, and `test_buy_stock_is_the_only_order_placing_tool` asserts that.

The interrupt payload shape (`{"type": "confirm_buy_order", "step": 1|2, "of": 2, "message": ..., "symbol", "quantity", ...}`) is consumed by both `ask()` (reads `["message"]`) and the frontend (reads `value.message`, renders Yes/No buttons) — change it in all three places together.

Tests exercise this against the real LangGraph engine by building a tiny `ToolNode`-only graph (`tests/test_tools.py::_build_tool_graph`) and driving it with a hand-built `AIMessage` carrying a `buy_stock` tool call, rather than mocking `interrupt`.

### Research sub-agents and live data

`research_stock` (`app/research.py`) is a fixed pipeline, not an agent: `_RESEARCHERS` (role → system prompt) and `_CONTEXT_FETCHERS` (role → live-data callable) are keyed by the same three roles (fundamentals, technicals, news_sentiment). Each role runs in a `ThreadPoolExecutor` as one LLM call fed its fetched context, then a synthesis call condenses the notes into exactly three paragraphs. Adding a role means adding a matching entry to both dicts.

`app/market_data.py` fetchers never raise to the caller: `fetch_price_snapshot`/`fetch_web_context` return bracketed `[... unavailable: ...]` strings, and `fetch_current_price` returns `None`. The sub-agent prompts tell the model to say data is missing rather than fill from training memory. Yahoo's `chartPreviousClose` is the range start, not yesterday's close — previous close is `closes[-2]`. `_fetch_chart_data` caches successful responses per symbol for 60s (in-process, bounded) because `research_stock` and `buy_stock`'s interrupt re-runs request the same symbol repeatedly; `tests/test_market_data.py` clears `_chart_cache` in an autouse fixture, and any new test that mocks Yahoo responses must do the same.

The mock `BrokerClient` also uses `fetch_current_price` for live position/quote prices, falling back to `_MOCK_QUOTES` only on `None` (a real `0.0` is preserved). Tests patch `app.broker_client.fetch_current_price` (autouse fixture in `test_tools.py`) and `app.market_data.requests.get/post` to stay offline.

### 10-K search (RAG)

`search_filings` is agentic RAG: the agent chooses when to call it and writes the `query`/`fiscal_year`/`section` arguments; the system prompt tells it to answer only from the excerpts and cite them as `[n]`. `app/sec_filings.py` fetches from EDGAR (ticker map → submissions JSON → primary 10-K HTML), converts inline-XBRL HTML to text with stdlib `HTMLParser` (skips `<ix:header>`/`display:none`, table cells joined by ` | `), and `split_sections` keeps Items 1/1A/7/7A. Every heading appears in the table of contents and some filers (Microsoft) repeat a bare "Item 1A" running header per page, so consecutive same-Item headings merge into a run and the longest run wins — test any parser change against real filings from several companies, not just the unit-test fixture.

`app/rag.py`'s `FilingIndex` ingests on demand (`ensure_indexed`: two latest 10-Ks, rechecked daily, a failed filing doesn't block the other, a stale index is served if EDGAR is down) into MongoDB Atlas, chosen because hybrid search is built in and the same cluster can later hold LangGraph chat checkpoints (chats are still in `langgraph dev`'s in-memory store; persisting them needs a custom checkpointer in `langgraph.json` *and* the frontend re-creating the thread under the same id after a 404, since `langgraph dev` keeps thread records in its own pickles). Two collections: `chunks` (filing metadata denormalized onto each chunk, the text, a float32 BSON vector) and `companies` (last EDGAR check, `_id` = `variant/symbol`). Chunking is LangChain's `RecursiveCharacterTextSplitter` counting words. `retrieve` runs the aggregation `_pipeline` builds: `$rankFusion` over `$vectorSearch` (cosine) and `$search` (`lucene.english`), each prefiltered; `mode="bm25"|"dense"` exist for the eval. Filter values are BSON values, never strings, because the symbol comes from the LLM. Don't hand-roll ranking here — use what Atlas provides. Every chunk carries a `variant` (embedding model slug + chunk size) that every query and the companies key filter on, so two models' vectors or two chunkings never mix; it's one shared collection rather than one per variant because M0 allows only 3 search indexes per cluster and this uses 2 (`chunks_text`, `chunks_vector`, created in code if missing). The vector index's dimension is fixed: switching to a model of another size means dropping `chunks_vector`. Atlas Search indexes lag writes by ~1s, so `_index_filing` waits until both indexes see a new filing's chunks. `get_database()`/`get_filing_index()` connect lazily so importing `app.tools` never loads the model or connects; with `MONGODB_URI` unset or Atlas unreachable, `search_filings` returns a bracketed message rather than raising. `MONGODB_URI` is a per-app Fly secret; Fly has no static egress IP, so the Atlas network access list is `0.0.0.0/0`. Atlas Search can't be faked offline: `tests/test_rag.py` covers chunking, the built pipelines, and the tool against a stub index (patched into `rag._index`), while ingest/ranking tests are in `tests/test_rag_atlas.py`, skipped unless `MONGODB_TEST_URI` is set (Atlas Local container), each test on its own dropped database. Both use `tests/conftest.py`'s hashing fake embedder and `fake_edgar` (patches `app.rag.list_annual_reports`/`fetch_sections`).

Embedding model choice is memory-bound. The Dockerfile bakes the model from build args (`EMBEDDING_MODEL`, `EMBEDDING_DTYPE`) into `/models/<hub id>` at that precision and points `EMBEDDING_MODEL` at it — saving at build time matters because model2vec loads float32 weights before quantizing, so runtime quantization still pays the float32 peak. Both apps build the default, `potion-retrieval-32M` float16 (server peak ~315MB of 470MB on Fly; float16 scores the same as float32, and it beats `potion-base-8M` on dense/hybrid in the README eval table). Keep `app/config.py`'s `EMBEDDING_MODEL` default equal to the Dockerfile's ARG default. Re-run `evals/filings_eval.py` after any chunking/retrieval change and update the README table if the numbers move.

### LLM provider

`app/config.get_llm()` is the single switch (`cerebras` default with `gpt-oss-120b` via its OpenAI-compatible endpoint; `groq` and `gemini` alternatives, whose packages aren't in `requirements.txt` and must be installed first) and is `@cache`d, so every caller shares one client. It raises at startup if the selected provider's API key is unset — a Fly app that only has the old `GROQ_API_KEY` secret must set `CEREBRAS_API_KEY` (or `LLM_PROVIDER=groq`) before deploying. `app/agent.py` binds tools at import time (`_llm_with_tools`), so importing it instantiates the provider client; tests avoid importing `app.agent` and go through `app.tools` directly.

## Conventions

- The system prompt in `app/agent.py` asks for GFM tables for multi-row results; the frontend has a hand-rolled markdown renderer (`renderMarkdown`) that only handles paragraphs, inline bold/italic/code, lists, and tables — extend it if the prompt starts requesting other markdown.
- The agent must not give buy/sell/hold advice; trading-decision questions are routed to `research_stock`. Keep new prompts and tool docstrings consistent with that.
- Deployment target is a 1GB Fly VM with no volume (`fly.toml`; it was 512MB until LanceDB, which has since been replaced by Atlas, so it may fit 512MB again once measured; older docstrings still cite 512MB); the README and `market_data.py` docstrings cite this when rejecting heavier dependencies. `.dockerignore` excludes tests, notebooks, and README from the image.
