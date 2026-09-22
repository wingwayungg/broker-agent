"""
The LangGraph agent itself.

This uses a small, explicit graph (router -> tools -> synthesis, with a
loop back to the router if the model wants to call another tool) rather
than a single black-box chain, and a checkpointer for memory, so a
follow-up like "which of those are up more than 5%?" can resolve against
the previous turn's tool output without re-asking the question from
scratch. That statefulness is the actual reason this is a LangGraph
project and not just a LangChain chain.
"""
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from app.config import get_llm
from app.tools import ALL_TOOLS

SYSTEM_PROMPT = """You are a portfolio assistant with access to a live
broker account via tools. You can report on positions, account
summary, open orders, fills, and bracket order status. You can research any
stock — not just ones the user holds — via research_stock, which fans out
to three sub-agents (fundamentals, technicals, news/sentiment) and returns
a three-paragraph informational summary. You can also buy stock via the
buy_stock tool.

Hard rules:
- buy_stock is the only order-placing capability. There is no sell,
  modify, or cancel tool. If asked to sell, modify, or cancel an order, say
  clearly that you can't do that here.
- buy_stock requires two separate explicit human confirmations before it
  submits anything, and that gate is enforced by the graph itself — not by
  you. You cannot skip it, pre-answer it, or call the tool again to try to
  push it through. When you call buy_stock you'll get back a confirmation
  question; that question is shown to the user as-is, and you simply wait
  for their real reply on the next turn — don't answer on their behalf and
  don't assume what they'll say.
- Only call buy_stock when the user gives an explicit buy instruction that
  includes both a ticker symbol and a share quantity. If either is missing
  or ambiguous, ask the user in plain text first — never guess a quantity.
- Do not give general trading, investment, or tax advice, and never frame a
  research summary or an order confirmation as a recommendation to buy,
  sell, or hold — surface the information and let the user decide.
- Treat questions phrased as a trading decision (e.g. "should I buy TSLA",
  "is it a good time to sell NVDA", "research on tsla") as a request to
  research that ticker: call research_stock and present the informational
  summary, then remind the user you can't tell them what to do — don't
  just decline the question outright, and don't call buy_stock unless they
  give an explicit buy instruction with a symbol and quantity.
- If a tool call fails or a live connection isn't available, say so plainly
  rather than guessing at numbers.
- When get_positions, get_open_orders, or get_recent_fills returns more
  than one row, present it as a GitHub-flavored markdown table instead of
  prose or a bullet list — the UI renders markdown tables. For positions,
  use columns Symbol, Quantity, Avg Cost, Current Price, P&L %, Market
  Value, right-aligning the numeric columns (e.g. `---:` in the header
  separator row). Keep any surrounding commentary brief; let the table
  carry the detail. Write the table as plain markdown — do not wrap it in
  a ``` code fence.
"""

_llm_with_tools = get_llm().bind_tools(ALL_TOOLS)


def call_model(state: MessagesState):
    messages = state["messages"]
    if not messages or messages[0].type != "system":
        from langchain_core.messages import SystemMessage

        messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages
    response = _llm_with_tools.invoke(messages)
    return {"messages": [response]}


def should_continue(state: MessagesState) -> str:
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "tools"
    return END


def _build_graph() -> StateGraph:
    graph = StateGraph(MessagesState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", ToolNode(ALL_TOOLS))

    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph


def build_agent():
    # MemorySaver keeps per-thread conversation state in memory so
    # follow-up questions in the same session have context. Swap for a
    # persistent checkpointer (e.g. SqliteSaver) if you want sessions to
    # survive a restart. This checkpointer only serves the in-process path
    # (`ask()` below, which is what everything in local_api_cli/ calls); the
    # LangGraph API path uses `platform_graph` and its own store instead.
    # That store has the same caveat in prod: `langgraph dev` pickles it to
    # .langgraph_api/, which survives a local restart but not a Render
    # restart or free-tier spin-down (ephemeral filesystem).
    checkpointer = MemorySaver()
    return _build_graph().compile(checkpointer=checkpointer)


agent = build_agent()

# Entry point for `langgraph.json` (LangGraph API / `langgraph dev` /
# LangGraph Platform). The platform manages checkpointing itself and
# raises on load if the compiled graph already has a custom checkpointer
# attached, so this variant is compiled without one.
platform_graph = _build_graph().compile()


def ask(question: str, thread_id: str = "default") -> str:
    """Convenience wrapper used by both the CLI and the FastAPI endpoint.

    Transparently doubles as the resume path for buy_stock's confirmation
    interrupts: if this thread is currently paused waiting on a human
    answer, `question` is treated as that answer (via Command(resume=...))
    instead of a new user message. Callers don't need to know the
    difference — they just keep calling ask() with whatever the user typed
    next, whether that's a new question or "yes"/"no" to a pending order.
    """
    config = {"configurable": {"thread_id": thread_id}}
    state = agent.get_state(config)
    if state.interrupts:
        result = agent.invoke(Command(resume=question), config=config)
    else:
        result = agent.invoke({"messages": [("user", question)]}, config=config)

    if "__interrupt__" in result:
        return result["__interrupt__"][0].value["message"]
    return result["messages"][-1].content
