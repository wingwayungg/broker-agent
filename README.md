# Broker Portfolio Agent

A LangGraph agent that answers natural-language questions about live brokerage
positions, account summary, and open orders — by orchestrating tool calls
against your existing broker API client.

**Mostly read-only, by design.** This agent can query your account and place
buy orders — nothing else. There is no sell, modify, or cancel tool at all.
Buy orders require two separate explicit human confirmations before
anything is submitted, and that gate is enforced by the LangGraph engine
itself (see `app/tools.py`'s `buy_stock`, which pauses on `interrupt()`),
not just by prompt instructions — the model that decides to call the tool
is never invoked again until a real human reply comes back through `ask()`.

## Why LangGraph instead of a plain LangChain chain

Portfolio questions are naturally multi-turn: "what's my tech exposure?"
followed by "which of those are red today?" requires remembering the prior
tool result. LangGraph models this as a stateful graph with memory, rather
than a one-shot chain, which is what makes follow-up questions work.

## Why `requests` instead of MCP for market data

`app/market_data.py` calls Yahoo Finance and Tavily directly with `requests`
rather than through MCP servers. Both calls are invoked deterministically
from fixed positions in `app/research.py` — never chosen by the model at
runtime — so MCP's core value (runtime tool discovery, a model picking
among capabilities) doesn't apply. Every Yahoo Finance MCP server is also
an unofficial wrapper around the same undocumented endpoint this code
already hits, so going through one would add a dependency chain without
reducing the "Yahoo may break this" risk. A plain sync `requests.get` is
also cheaper than an async session lifecycle bridged into this otherwise
sync codebase, on the 512MB Fly VM this runs on.

## Architecture

```
User question (CLI or POST /ask)
        |
        v
LangGraph agent (app/agent.py)
   - router node: decides which tool(s) to call
   - tool node: executes broker tool calls
   - synthesis node: LLM turns tool output into a natural-language answer
   - loops back on follow-up questions within the same session
   - buy_stock pauses the graph twice (interrupt()) for human confirmation
     before it ever reaches the point of placing an order
        |
        v
Tools (app/tools.py) -> broker client (app/broker_client.py)
        |
        v
Your existing broker API client (plug in here)
```

## Project layout

```
broker-portfolio-agent/
├── app/
│   ├── config.py          # env vars, LLM provider config
│   ├── models.py          # Pydantic models for all broker data
│   ├── broker_client.py   # TODO: wire up to your existing broker API client
│   ├── tools.py           # LangGraph-callable tools (buy_stock is the only mutating one)
│   ├── agent.py           # the LangGraph graph definition
│   └── main.py            # FastAPI app exposing POST /ask
├── tests/
│   └── test_tools.py      # tests against the mock broker client
├── cli.py                 # quick terminal chat loop for demos
├── requirements.txt
└── .env
```

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
# copy .env and fill in your own API keys (GROQ_API_KEY, TAVILY_API_KEY, ...)
```

## Plugging in a real broker connection

Everything in `app/broker_client.py` currently returns mock data so the
agent is runnable and demoable without any live brokerage account. Replace
the method bodies with calls into whichever broker API/SDK you use — the
function signatures and return types (Pydantic models in `app/models.py`)
are the contract the rest of the app depends on, so as long as you match
those, nothing else needs to change.

## Running it

CLI demo (no server needed):
```bash
python cli.py
```

FastAPI server:
```bash
uvicorn app.main:app --reload
# POST http://localhost:8000/ask  {"question": "what are my current positions?"}
```

## Example session

```
> what are my current positions?
You're holding 150 shares of AAPL (avg cost $187.32, currently $194.10,
+3.6%) and 40 shares of NVDA (avg cost $118.50, currently $131.20, +10.7%).

> which of those are up more than 5% today?
Of your two positions, NVDA is up 10.7% and would qualify. AAPL is up 3.6%
and does not.

> place a sell order for the NVDA position
I can't sell, modify, or cancel orders — the only order-placing tool I have
is buy_stock. You'd need to sell directly through your broker.

> buy 10 shares of AAPL
Confirm order: BUY 10 AAPL @ ~$194.10 (estimated cost $1,941.00). Reply
'yes' to continue or 'no' to cancel.

> yes
Final confirmation — this will submit a real order: BUY 10 AAPL @ ~$194.10
(estimated cost $1,941.00). This cannot be undone once submitted. Reply
'yes' to submit or 'no' to cancel.

> yes
Order submitted: BUY 10 AAPL @ ~$194.10 (order id 2001, status Submitted).
```

## Extending

- Add a `get_market_data(symbol)` tool for live quotes beyond cost basis
- Add per-tool-call logging in `app/agent.py` for an observability talking
  point in interviews
- Swap `GROQ` for `GEMINI` in `app/config.py` — the LLM client is abstracted
  behind one function so switching providers is a one-line change
