# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
source venv/bin/activate               # project venv (see .vscode/settings.json)
pip install -r requirements.txt

python -m pytest                       # full suite, offline (~1s)
python -m pytest tests/test_tools.py::test_buy_stock_requires_two_affirmative_confirmations_before_submitting

python -m local_api_cli.cli            # terminal chat loop (in-process, MemorySaver)
uvicorn local_api_cli.api:app --reload # FastAPI: POST /ask {"question": ..., "thread_id": ...}
langgraph dev                          # LangGraph API on :2024 + browser chat UI at / (what Render runs)
```

Render deploys are triggered by pushing to the connected branch (`render.yaml` blueprint; service name in there).

There is no linter/formatter config. `.env` holds `GROQ_API_KEY`, `TAVILY_API_KEY`, optional `LLM_PROVIDER`/`GROQ_MODEL`/`USE_MOCK_BROKER`; `langgraph.json` also loads it.

## Architecture

Read the module docstrings first — each file explains *why* it is shaped the way it is (mock-first broker contract, `requests` instead of MCP/Tavily SDKs, Starlette-not-FastAPI frontend), and those rationales are deliberate.

### Layers and dependency direction

```
entry points   local_api_cli/cli.py   local_api_cli/api.py (FastAPI /ask)   app/frontend.py (browser UI, via LangGraph API)
                         \                     |                                   |
                             ask() in app/agent.py                   langgraph.json -> platform_graph
                                        \                             /
graph            app/agent.py   (agent node <-> ToolNode loop over MessagesState)
                              |
tools            app/tools.py   (7 @tool functions; ALL_TOOLS)
                     /                    \
domain    app/broker_client.py        app/research.py  (3 sub-agent LLM calls + synthesis)
                     \                    /
data           app/market_data.py  (Yahoo chart endpoint, Tavily /search)
                              |
config           app/config.py  (settings, get_llm())      app/models.py (Pydantic contract)
```

Imports only go downward. `local_api_cli/` holds the in-process runners (terminal chat and FastAPI `/ask`); they don't import each other, both just call `ask()`. It's named for its two contents rather than "local" because `tests/` and `notebooks/` are local too. Nothing in `local_api_cli/` ships to Render — it's in `.dockerignore` and its deps (`fastapi`, `uvicorn`) are dev-only. Run them as `python -m local_api_cli.cli` / `uvicorn local_api_cli.api:app`, not `python local_api_cli/cli.py`, so the repo root stays on `sys.path`. `app/tools.py` is the only place the LLM-facing surface is defined; `app/broker_client.py` is the only file meant to change when wiring a real broker (its methods raise `NotImplementedError` when `USE_MOCK_BROKER=false`). `app/models.py` is the return-type contract between the two — `Position.market_value` and `unrealized_pnl_pct` are computed properties, not stored fields, so a real broker only needs to supply `symbol/quantity/avg_cost/current_price`.

### One turn, end to end

1. Caller sends text for a `thread_id`. If the thread is paused on an interrupt, the text is a **resume value**; otherwise it is a new `("user", text)` message.
2. `call_model` prepends `SYSTEM_PROMPT` if the first message isn't a system message, invokes the tool-bound LLM, and `should_continue` routes to `ToolNode` if the reply has `tool_calls`, else `END`.
3. `ToolNode` runs the tool. Read-only tools return formatted strings that the LLM turns into prose/tables on the next loop. `buy_stock` may instead hit `interrupt()`, which surfaces as `__interrupt__` in the result — the caller shows `value["message"]` and waits for a human.
4. Loop continues until the model replies without tool calls.

Memory is per `thread_id` and never in a real database (`MemorySaver` in-process, or LangGraph API's own store under `langgraph dev`, which pickles to `.langgraph_api/`). Either way it doesn't survive deployment: Render's filesystem is ephemeral and the free tier spins down when idle, so a cold start comes back with no threads. The browser UI handles this by catching a 404 on its stored thread and transparently starting a new one.

### Two compiled graphs, two entry paths

`app/agent.py` exports both `agent` (compiled with `MemorySaver`, used by `local_api_cli/cli.py` and `local_api_cli/api.py` via `ask()`) and `platform_graph` (compiled with **no** checkpointer, referenced by `langgraph.json`). LangGraph Platform/`langgraph dev` supplies its own checkpointer and errors if the graph already has one, so don't collapse these into one.

- **In-process path** (`ask()`): infers whether the thread is paused via `agent.get_state(config).interrupts` and sends `Command(resume=text)` vs a new user message accordingly. Callers just keep passing whatever the user typed — including "yes"/"no".
- **LangGraph API path** (`app/frontend.py`): the browser page posts straight to `/threads/{id}/runs/stream` with `stream_mode: ["messages", "updates"]`, tracks `pendingInterrupt` from the previous response's `__interrupt__` update, and chooses `input` vs `command.resume` explicitly. It never touches `ask()` or `local_api_cli/api.py`. `frontend.py` is mounted via `langgraph.json`'s `http.app` hook and is a Starlette app on purpose (FastAPI would shadow LangGraph's `/docs`). Under `langgraph dev`, `local_api_cli/api.py` is not served at all.

### `buy_stock` is the only mutating tool, gated by `interrupt()`

The safety model is structural, not prompt-based: `buy_stock` validates quantity and buying power *before* any interrupt (so an unaffordable order returns immediately with no confirmation), then calls `interrupt()` twice. Each pause returns control to the caller; the LLM is not re-invoked until an external resume arrives, so it can never answer its own confirmation. Only `_is_affirmative` replies (`y`/`yes`/`confirm`/`confirmed`, case-insensitive) advance; anything else cancels. There is intentionally no sell/modify/cancel tool, and `test_buy_stock_is_the_only_order_placing_tool` asserts that.

The interrupt payload shape (`{"type": "confirm_buy_order", "step": 1|2, "of": 2, "message": ..., "symbol", "quantity", ...}`) is consumed by both `ask()` (reads `["message"]`) and the frontend (reads `value.message`, renders Yes/No buttons) — change it in all three places together.

Tests exercise this against the real LangGraph engine by building a tiny `ToolNode`-only graph (`tests/test_tools.py::_build_tool_graph`) and driving it with a hand-built `AIMessage` carrying a `buy_stock` tool call, rather than mocking `interrupt`.

### Research sub-agents and live data

`research_stock` (`app/research.py`) is a fixed pipeline, not an agent: `_RESEARCHERS` (role → system prompt) and `_CONTEXT_FETCHERS` (role → live-data callable) are keyed by the same three roles (fundamentals, technicals, news_sentiment). Each role runs in a `ThreadPoolExecutor` as one LLM call fed its fetched context, then a synthesis call condenses the notes into exactly three paragraphs. Adding a role means adding a matching entry to both dicts.

`app/market_data.py` fetchers never raise to the caller: `fetch_price_snapshot`/`fetch_web_context` return bracketed `[... unavailable: ...]` strings, and `fetch_current_price` returns `None`. The sub-agent prompts tell the model to say data is missing rather than fill from training memory. Yahoo's `chartPreviousClose` is the range start, not yesterday's close — previous close is `closes[-2]`.

The mock `BrokerClient` also uses `fetch_current_price` for live position/quote prices, falling back to `_MOCK_QUOTES` only on `None` (a real `0.0` is preserved). Tests patch `app.broker_client.fetch_current_price` (autouse fixture in `test_tools.py`) and `app.market_data.requests.get/post` to stay offline.

### LLM provider

`app/config.get_llm()` is the single switch (`groq` default with `openai/gpt-oss-120b`, `gemini` alternative) and is called fresh for every research sub-agent call. `app/agent.py` binds tools at import time (`_llm_with_tools`), so importing it instantiates the provider client; tests avoid importing `app.agent` and go through `app.tools` directly.

## Conventions

- The system prompt in `app/agent.py` asks for GFM tables for multi-row results; the frontend has a hand-rolled markdown renderer (`renderMarkdown`) that only handles paragraphs, inline bold/italic/code, lists, and tables — extend it if the prompt starts requesting other markdown.
- The agent must not give buy/sell/hold advice; trading-decision questions are routed to `research_stock`. Keep new prompts and tool docstrings consistent with that.
- Deployment target is a 512MB Render free-tier instance (`render.yaml`); the README and `market_data.py` docstrings cite this when rejecting heavier dependencies. `.dockerignore` excludes tests, notebooks, and README from the image.
