# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
source venv/bin/activate            # project venv (see .vscode/settings.json)
pip install -r requirements.txt

python -m pytest                    # full suite, offline (~1s)
python -m pytest tests/test_tools.py::test_buy_stock_requires_two_affirmative_confirmations_before_submitting

python cli.py                       # terminal chat loop (in-process, MemorySaver)
uvicorn app.main:app --reload       # FastAPI: POST /ask {"question": ..., "thread_id": ...}
langgraph dev                       # LangGraph API on :2024 + browser chat UI at / (what Fly runs)
fly deploy                          # deploys the Dockerfile; app name in fly.toml
```

There is no linter/formatter config. `.env` holds `GROQ_API_KEY`, `TAVILY_API_KEY`, optional `LLM_PROVIDER`/`GROQ_MODEL`/`USE_MOCK_BROKER`; `langgraph.json` also loads it.

## Architecture

Single LangGraph `agent -> tools -> agent` loop over `MessagesState` (`app/agent.py`), with tools in `app/tools.py` calling a `BrokerClient` singleton (`app/broker_client.py`) that returns Pydantic models from `app/models.py`. Read the module docstrings — each file explains *why* it is shaped the way it is, and those rationales (mock-first broker contract, no MCP/Tavily SDK, Starlette-not-FastAPI frontend) are deliberate.

### Two compiled graphs, two entry paths

`app/agent.py` exports both `agent` (compiled with `MemorySaver`, used by `cli.py` and `app/main.py` via `ask()`) and `platform_graph` (compiled with **no** checkpointer, referenced by `langgraph.json`). LangGraph Platform/`langgraph dev` supplies its own checkpointer and errors if the graph already has one, so don't collapse these into one.

- **In-process path** (`ask()`): infers whether the thread is paused on an interrupt via `agent.get_state(config).interrupts` and sends `Command(resume=text)` vs a new user message accordingly. Callers just keep passing whatever the user typed.
- **LangGraph API path** (`app/frontend.py`): the browser page posts straight to `/threads/{id}/runs/stream`, tracks `pendingInterrupt` from the previous response's `__interrupt__`, and chooses `input` vs `command.resume` explicitly. It never touches `ask()` or `app/main.py`. `frontend.py` is mounted via `langgraph.json`'s `http.app` hook and is a Starlette app on purpose (FastAPI would shadow LangGraph's `/docs`).

### `buy_stock` is the only mutating tool, gated by `interrupt()`

The safety model is structural, not prompt-based: `buy_stock` calls `interrupt()` twice, so the graph pauses and the LLM is not re-invoked until an external caller resumes. There is intentionally no sell/modify/cancel tool, and `test_buy_stock_is_the_only_order_placing_tool` asserts that. Anything that changes `ALL_TOOLS`, the interrupt payload shape (`{"type": "confirm_buy_order", "step", "of", "message", ...}`), or `_is_affirmative` affects `ask()`, the frontend, and the system prompt together.

Tests exercise the interrupt flow against the real LangGraph engine by building a tiny `ToolNode`-only graph (`tests/test_tools.py::_build_tool_graph`) rather than mocking `interrupt`.

### Research sub-agents and live data

`research_stock` (`app/research.py`) fans out three narrow LLM calls (fundamentals, technicals, news) in a `ThreadPoolExecutor`, each fed live context from `app/market_data.py` (Yahoo chart endpoint for prices, Tavily `/search` for web), then synthesizes into exactly three paragraphs. Fetchers degrade to bracketed `[... unavailable: ...]` strings rather than raising, and the sub-agent prompts tell the model to say so rather than fill from memory. `chartPreviousClose` from Yahoo is *not* yesterday's close (it's the range start); previous close comes from `closes[-2]`.

The mock `BrokerClient` also uses `fetch_current_price` for live position/quote prices, falling back to `_MOCK_QUOTES`. Tests patch `app.broker_client.fetch_current_price` (autouse fixture in `test_tools.py`) to stay offline.

### LLM provider

`app/config.get_llm()` is the single switch (`groq` default with `openai/gpt-oss-120b`, `gemini` alternative). `app/agent.py` binds tools at import time, so importing it instantiates the provider client; tests avoid importing `app.agent` and go through `app.tools` directly.

## Conventions

- The system prompt in `app/agent.py` asks for GFM tables for multi-row results; the frontend has a hand-rolled markdown renderer (`renderMarkdown`) that only handles paragraphs, inline bold/italic/code, lists, and tables — extend it if the prompt starts requesting other markdown.
- The agent must not give buy/sell/hold advice; trading-decision questions are routed to `research_stock`. Keep new prompts and tool docstrings consistent with that.
- Deployment target is a 512MB Fly VM (`fly.toml`); the README and `market_data.py` docstrings cite this when rejecting heavier dependencies. `.dockerignore` excludes tests, notebooks, and README from the image.
