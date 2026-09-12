# IBKR Portfolio Agent

A LangGraph agent that answers natural-language questions about live Interactive
Brokers positions, account summary, and open orders — by orchestrating tool
calls against your existing IBKR API wrapper.

**Read-only by design.** This agent can query your account. It cannot place,
modify, or cancel orders. That boundary is enforced in code (see
`app/tools.py`), not just in the prompt.

## Why LangGraph instead of a plain LangChain chain

Portfolio questions are naturally multi-turn: "what's my tech exposure?"
followed by "which of those are red today?" requires remembering the prior
tool result. LangGraph models this as a stateful graph with memory, rather
than a one-shot chain, which is what makes follow-up questions work.

## Architecture

```
User question (CLI or POST /ask)
        |
        v
LangGraph agent (app/agent.py)
   - router node: decides which tool(s) to call
   - tool node: executes IBKR tool calls
   - synthesis node: LLM turns tool output into a natural-language answer
   - loops back on follow-up questions within the same session
        |
        v
Tools (app/tools.py) -> IBKR client (app/ibkr_client.py)
        |
        v
Your existing IBKR API script (plug in here)
```

## Project layout

```
ibkr-portfolio-agent/
├── app/
│   ├── config.py        # env vars, LLM provider config
│   ├── models.py         # Pydantic models for all IBKR data
│   ├── ibkr_client.py     # TODO: wire up to your existing IBKR script
│   ├── tools.py           # LangGraph-callable tools (read-only)
│   ├── agent.py           # the LangGraph graph definition
│   └── main.py            # FastAPI app exposing POST /ask
├── tests/
│   └── test_tools.py      # tests against the mock IBKR client
├── cli.py                 # quick terminal chat loop for demos
├── requirements.txt
└── .env.example
```

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in your GROQ_API_KEY (or swap provider in app/config.py)
```

## Plugging in your real IBKR connection

Everything in `app/ibkr_client.py` currently returns mock data so the agent
is runnable and demoable without a live TWS/Gateway connection. Replace the
method bodies with calls into your existing `ibapi`-based script — the
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
I can't place or modify orders — I'm read-only. You'd need to do that
directly in TWS or via your trading script.
```

## Extending

- Add a `get_market_data(symbol)` tool for live quotes beyond cost basis
- Add per-tool-call logging in `app/agent.py` for an observability talking
  point in interviews
- Swap `GROQ` for `GEMINI` in `app/config.py` — the LLM client is abstracted
  behind one function so switching providers is a one-line change
