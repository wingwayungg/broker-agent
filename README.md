# Broker Portfolio Agent

[![Built with LangGraph](https://img.shields.io/badge/Built%20with-LangGraph-1C3C3C?logo=langgraph&logoColor=white)](https://www.langchain.com/langgraph)
[![LLM by Groq](https://img.shields.io/badge/LLM-Groq-F55036)](https://groq.com/)
[![Deployed on Fly](https://img.shields.io/badge/Deployed%20on-Fly-8B5CF6?logo=flydotio&logoColor=white)](https://fly.io/)

This is a simple chat website where you can ask questions about a stock portfolio in everyday English — for example "What am I holding right now?" or "Should I buy TSLA?" — and an AI assistant answers by looking up the account and today's market prices. The account behind it is a demo account holding three sample stocks (Apple, Nvidia and Microsoft), so no real money is involved, but the prices are live from [Yahoo Finance](https://finance.yahoo.com/) and the research is built from live web search via [Tavily](https://tavily.com/). This project serves as an exercise to practice building AI agents (powered by [LangGraph](https://www.langchain.com/langgraph) and [Groq](https://groq.com/)) and connecting them to live data. The features of this project include:

- viewing holdings, cash and buying power, open orders and recent trades
- researching any stock with three AI "analysts" (fundamentals, technicals, news) working in parallel on live data
- placing a buy order — but only after a human says "yes" twice
- follow-up questions ("which of those are in profit?") within the same conversation
- conversations that survive a browser reload

## Ask the assistant
### Demo conversation 1 (account) — everyday questions

> **You —** What am I holding right now?
>
> **Assistant —** *A table: AAPL 150 shares (+81.0%), NVDA 40 shares (+91.9%), MSFT 60 shares (+24.8%), with average cost, current price and market value*
>
> **You —** Which of those are in profit?
>
> **Assistant —** All three of your current holdings are showing a profit … Each position's unrealized P&L is positive, so every stock you own is currently in profit.
>
> **You —** How much cash do I have?
>
> **Assistant —** You have **$41,200.00** in cash available in your account.
>
> **You —** Do I have any open orders?
>
> **Assistant —** *A table with one order: BUY 20 TSLA, limit $245.00, Submitted*

Everything above is read-only.

### Demo conversation 2 (trading) — guardrails and human-in-the-loop

> **You —** Sell all my NVDA
>
> **Assistant —** I'm not able to place sell orders or cancel existing positions. I can only help you research a stock, view your current holdings, or place a **buy** order.
>
> **You —** Should I buy TSLA?
>
> **Assistant —** *Three short sections — fundamentals, technicals, news — followed by:* I'm not able to give personal investment advice or tell you whether to buy or not. Use the information above to make your own decision …
>
> **You —** Buy some Apple
>
> **Assistant —** Sure! How many shares of **AAPL** would you like to purchase?
>
> **You —** Buy 100000 shares of NVDA
>
> **Assistant —** I'm unable to place that order — the estimated cost of ≈ $22.7 million for 100 000 NVDA shares exceeds the buying power available in the account.
>
> **You —** Buy 10 shares of AAPL
>
> **Assistant —** Confirm order: BUY 10.0 AAPL @ ~$338.98 (estimated cost $3,389.80). Reply 'yes' to continue or 'no' to cancel.
>
> **You —** yes
>
> **Assistant —** Final confirmation — this will submit a real order: BUY 10.0 AAPL @ ~$338.98 (estimated cost $3,389.80). This cannot be undone once submitted. Reply 'yes' to submit or 'no' to cancel.
>
> **You —** no
>
> **Assistant —** The order wasn't placed because the required confirmations weren't given, so no shares were bought.

This conversation is all guardrails. The assistant has no way to sell, cancel or modify anything — those tools simply don't exist, so it cannot do them however it is asked. It will not tell you whether to buy or sell; a question like "should I buy TSLA?" is answered with research and a reminder that the decision is yours. A vague instruction ("buy some Apple") gets a question back rather than a guessed quantity, and an order the account can't afford is refused on the spot. Even a valid buy order never goes through on the assistant's say-so: it asks a real person to confirm twice, and anything other than a clear "yes" cancels it.

### Demo conversation 3 (research) — thread memory
> **You —** hood
>
> **Assistant —** *Research on Robinhood Markets (HOOD) — fundamentals, technicals and news, condensed into three paragraphs …*
>
> **You —** crcl
>
> **Assistant —** *The same three-part research for Circle Internet Group (CRCL) …*
>
> **You —** compare two stocks
>
> **Assistant —** *A table putting HOOD and CRCL side by side on price, 52-week range and recent momentum, followed by:* … which of the two suits you is your call, not mine.
>
> **You —** compare them to coin
>
> **Assistant —** *Fresh research on Coinbase (COIN), then the same table widened to all three …*

Nothing after the first message names a company again. "Compare two stocks" has no subject of its own — the assistant resolves it to HOOD and CRCL because those are the two it just researched in this thread — and "them" in the next message resolves to that same pair, which is how COIN gets added to a comparison rather than replacing it. Each browser tab is its own thread, so a second tab starts with none of this.

## Technical Features

- **An agent, not a script.** There is no fixed menu of commands. Thanks to the flexibility of LangGraph: each question goes to the language model together with descriptions of the tools; the model picks one, LangGraph runs it, the result goes back to the model, and it either picks another tool or writes the answer. For example, a question phrased as a trading decision ("should I buy TSLA?") is routed to the research tool.

- **Human-in-the-loop buying** Before a buy goes through, the app stops and asks you to confirm — twice. While it waits, the whole process is frozen: the model cannot move on, and it is never allowed to answer the question on its own behalf. Only a clear `y` / `yes` / `confirm` continues; anything else cancels the order. Quantity and buying power are checked, so an unaffordable order is refused.

- **Three research analysts in parallel.** "Research on a ticket" is a fixed pipeline rather than more agent improvisation: a fundamentals analyst, a technicals analyst and a news/sentiment analyst — each a single narrow language-model call fed its own live data — run concurrently in a thread pool, and a fourth call condenses their notes into exactly three paragraphs. This allows the response to be delivered quickly while ensuring quality and readability.

- **Live data instead of the model's memory.** A language model's knowledge of prices, earnings and news is out of date (for the LLM used in this project, the training data is from 2024). Latest data is obtained from Tavily web search and Yahoo Finance.

- **Plain `requests` rather than MCP or vendor SDKs.** Real-time market data is fetched with plain `requests` rather than through MCP servers or vendor SDKs: the result is more deterministic and what MCP is actually good at — letting a model discover and pick among tools — has nothing to add here. The REST APIs already do the job that an MCP server does, and one `requests.get` is lighter on a 512MB instance than an extra dependency chain (see the full reasoning in `app/market_data.py`).

- **Thread memory.** Refreshing the browser doesn't lose the chat history, as conversations live in the server's memory. However, the conversation is lost whenever the server restarts or the free instance spins down (e.g. redeployment or the chat is idle for certain time). Persisting chat history properly would need a real database behind the checkpointer, which is the natural next step for this project.

- **A maximum token size (upcoming)** Every turn currently resends the whole thread to the model, so a long conversation keeps growing — more cost, slower replies, and eventually the model's context window is exceeded. The next step is a token budget on the thread: once it is reached, the oldest turns are condensed into a short running summary rather than simply dropped, so the assistant still knows which stocks were discussed and what was already researched while the context stays within the limit.

## Programming Languages

The language I used is Python, with LangGraph and LangChain for the agent, Pydantic for the data models, Starlette for the web layer and plain `requests` for market data. The browser page is hand-written HTML, CSS and JavaScript with no framework. The language model is `openai/gpt-oss-120b` served by Groq (the provider is a single setting in `app/config.py`), live prices come from Yahoo Finance and web search from Tavily. The app ships as a single Docker image.

## Deployment

Please visit [https://broker-ai-agent-yung.fly.dev/](https://broker-ai-agent-yung.fly.dev/)

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
