# Broker Portfolio Agent

[![Built with LangGraph](https://img.shields.io/badge/Built%20with-LangGraph-1C3C3C?logo=langgraph&logoColor=white)](https://www.langchain.com/langgraph)
[![LLM by Groq](https://img.shields.io/badge/LLM-Groq-F55036)](https://groq.com/)
[![Deployed on Render](https://img.shields.io/badge/Deployed%20on-Render-46E3B7?logo=render&logoColor=white)](https://render.com/)

This is a simple chat website where you can ask questions about a stock portfolio in everyday English — for example "What am I holding right now?" or "Should I buy TSLA?" — and an AI assistant answers by looking up the account and today's market prices. The account behind it is a demo account holding three sample stocks (Apple, Nvidia and Microsoft), so no real money is involved, but the prices are live from [Yahoo Finance](https://finance.yahoo.com/) and the research is built from live web search via [Tavily](https://tavily.com/). This project has two main purposes. First, most AI chatbots can only *talk*; this one can *act* — it reads an account and can place an order — which raises the question of how to keep an AI that acts safely under human control, and that question shaped most of the design. Second, this project serves as an exercise to practice building AI agents with LangGraph and connecting them to live data. The features of this project include:

- AI portfolio assistant (powered by [LangGraph](https://www.langchain.com/langgraph) and [Groq](https://groq.com/))
- viewing holdings, cash and buying power, open orders and recent trades
- researching any stock with three AI "analysts" (fundamentals, technicals, news) working in parallel on live data
- placing a buy order — but only after a human says "yes" twice
- follow-up questions ("which of those are in profit?") within the same conversation
- a browser chat page with Yes/No buttons, proper tables, and a conversation that survives a page reload

## Ask the assistant

A chat box sits at the bottom of the page. It sends your question to the assistant, which decides which of its seven tools to use (positions, account summary, open orders, recent fills, bracket order status, research, buy), reads the result, and writes a short answer back. Below are real exchanges against the demo account. Answers are quoted as given, shortened where they ran long; where the answer was a table, it is described in *italics*:

| Prompt | Answer |
|--------|--------|
| What am I holding right now? | *A table: AAPL 150 shares (+81.0%), NVDA 40 shares (+91.9%), MSFT 60 shares (+24.8%), with average cost, current price and market value* |
| Which of those are in profit? | All three of your current holdings are showing a profit … Each position's unrealized P&L is positive, so every stock you own is currently in profit. |
| How much cash do I have? | You have **$41,200.00** in cash available in your account. |
| Do I have any open orders? | *A table with one order: BUY 20 TSLA, limit $245.00, Submitted* |
| Sell all my NVDA | I'm not able to place sell orders or cancel existing positions. I can only help you research a stock, view your current holdings, or place a **buy** order. |
| Should I buy TSLA? | *Three short sections — fundamentals, technicals, news — followed by:* I'm not able to give personal investment advice or tell you whether to buy or not. Use the information above to make your own decision … |
| Buy some Apple | Sure! How many shares of **AAPL** would you like to purchase? |
| Buy 100000 shares of NVDA | I'm unable to place that order — the estimated cost of ≈ $22.7 million for 100 000 NVDA shares exceeds the buying power available in the account. |
| Buy 10 shares of AAPL | Confirm order: BUY 10.0 AAPL @ ~$338.98 (estimated cost $3,389.80). Reply 'yes' to continue or 'no' to cancel. |
| yes | Final confirmation — this will submit a real order: BUY 10.0 AAPL @ ~$338.98 (estimated cost $3,389.80). This cannot be undone once submitted. Reply 'yes' to submit or 'no' to cancel. |
| no | The order wasn't placed because the required confirmations weren't given, so no shares were bought. |

The last seven rows show the guardrails. The assistant has no way to sell, cancel or modify anything — those tools simply don't exist, so it cannot do them however it is asked. It will not tell you whether to buy or sell; a question like "should I buy TSLA?" is answered with research and a reminder that the decision is yours. A vague instruction ("buy some Apple") gets a question back rather than a guessed quantity, and an order the account can't afford is refused on the spot. Even a valid buy order never goes through on the assistant's say-so: it asks a real person to confirm twice, and anything other than a clear "yes" cancels it.

> **Note:** the live demo runs on Render's free tier, which puts the server to sleep after 15 minutes without visitors. The first page load after a quiet spell can take up to a minute while it wakes up; after that, most answers arrive in a second or two. Research questions take around 20 seconds because they run four AI calls and several web searches.

## Technical Features

- **An agent, not a script.** There is no fixed menu of commands. Each question goes to the language model together with descriptions of the seven tools; the model picks one, LangGraph runs it, the result goes back to the model, and it either picks another tool or writes the answer. The conversation is kept per browser tab as a LangGraph *thread*, which is what lets "which of those are in profit?" resolve against the previous answer. A question phrased as a trading decision ("should I buy TSLA?") is routed to the research tool by rule, and the model is instructed never to frame research or an order confirmation as a recommendation.

- **Human-in-the-loop buying, enforced by the engine rather than the prompt.** `buy_stock` is the only tool that changes anything; there is deliberately no sell, cancel or modify tool, so those actions are impossible rather than merely discouraged. Inside `buy_stock`, LangGraph's `interrupt()` is called twice. Each call freezes the whole graph mid-tool and hands the confirmation text to whoever is driving it — the browser page or the terminal — and nothing after that line runs until a reply arrives from *outside* the model. The model that decided to buy is never asked to confirm its own order, and only a plain `y` / `yes` / `confirm` continues; any other reply cancels. Quantity and buying power are checked *before* the first pause, so an unaffordable order is refused without a confirmation round-trip.

- **Three research analysts in parallel.** "Research TSLA" is a fixed pipeline rather than more agent improvisation: a fundamentals analyst, a technicals analyst and a news/sentiment analyst — each a single narrow language-model call fed its own live data — run concurrently in a thread pool, and a fourth call condenses their notes into exactly three paragraphs. Fundamentals and news come from Tavily web search, anchored on the company name because a bare ticker like `F` returns unrelated results; technicals come from Yahoo Finance's chart endpoint (price, day and 52-week range, volume, one-month trend).

- **Live data instead of the model's memory.** A language model's knowledge of prices, earnings and news is months out of date, so nothing numerical is left to it: prices are fetched at the moment of the question, and each analyst is told to say data is unavailable rather than fill the gap from memory. Even the demo account works this way — its holdings are fixed but priced live, which is why the profit figures in the examples above change from day to day. When a source is down, the fetchers return a bracketed `[... unavailable]` note instead of raising, so the assistant says so plainly rather than guessing.

- **Mock-first broker.** Everything runs against a built-in demo account, so it can be tried without a brokerage login. `app/broker_client.py` is the one file to change to connect a real broker; as long as its methods keep returning the Pydantic models in `app/models.py`, the tools, the agent and the UI do not change at all.

- **Deliberately light.** The whole app is one Docker image running LangGraph's own API server on a 512 MB free instance, with a small Starlette page mounted on top for the chat UI. Market data is fetched with plain `requests` rather than through MCP servers or vendor SDKs: those calls are made deterministically from fixed points in the research pipeline, never chosen by the model at runtime, so the extra layers would add dependencies without adding capability (the full reasoning is in the `app/market_data.py` docstring).

- **A small, hand-written browser page.** The page streams replies straight from the LangGraph API, turns the confirmation prompt into Yes/No buttons, renders the model's markdown tables as real tables, and rebuilds the chat log from the server's thread state on reload — so refreshing mid-answer doesn't lose anything. Conversations live in the server's memory, not a database; after a redeploy the page notices its old thread is gone and quietly starts a new one.

## Quality Control

The project has 29 automated tests that run offline (network calls are stubbed) in under a second, so they run before every change:

```
$ python -m pytest -q
.............................                                            [100%]
29 passed in 0.91s
```

The buy-order tests are the important ones. Instead of faking the confirmation step, they build a tiny real LangGraph graph around the tool and drive it with a hand-made "buy" instruction, checking that the order is only submitted after two explicit "yes" replies, that a "no" at either step submits nothing, and that an unaffordable order is refused before any confirmation. One test also asserts that `buy_stock` is the only tool capable of placing an order, so quietly adding a sell tool later would fail the suite.

## Programming Languages

The language I used is Python, with LangGraph and LangChain for the agent, Pydantic for the data models, Starlette for the web layer and plain `requests` for market data. The browser page is hand-written HTML, CSS and JavaScript with no framework. The language model is `openai/gpt-oss-120b` served by Groq (the provider is a single setting in `app/config.py`), live prices come from Yahoo Finance and web search from Tavily. The app ships as a single Docker image.

## Deployment

Please visit [https://broker-portfolio-agent.onrender.com/](https://broker-portfolio-agent.onrender.com/)

The image runs `langgraph dev`, which serves the LangGraph API and the browser UI together, so the deployed app and the local one are the same server. That is a development server, and I chose it knowingly: its API is unauthenticated, so anyone with the URL can run the graph on my API keys. With the broker mocked there is nothing to lose, but it is the first thing I would change before connecting a real account — either an `auth` block in `langgraph.json`, or serving the graph from a small uvicorn app of my own.

## Running it locally

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt    # or requirements.txt if you don't need the tests or cli script
# create a .env with GROQ_API_KEY and TAVILY_API_KEY (USE_MOCK_BROKER defaults to true)

langgraph dev                 # browser chat at http://localhost:2024/
python -m local_api_cli.cli   # or chat in the terminal
python -m pytest              # run the tests
```
