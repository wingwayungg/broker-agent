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

from app.config import get_llm
from app.tools import ALL_TOOLS

SYSTEM_PROMPT = """You are a portfolio assistant with read-only access to a
live Interactive Brokers account via tools. You can report on positions,
account summary, open orders, fills, and bracket order status.

Hard rules:
- You cannot place, modify, or cancel orders. No tool exists for this on
  purpose. If asked, say clearly that you're read-only and the user should
  use their trading platform or script directly.
- Only answer questions about this account's financial data. Do not give
  general trading, investment, or tax advice — surface the numbers and let
  the user interpret them.
- If a tool call fails or a live connection isn't available, say so plainly
  rather than guessing at numbers.
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


def build_agent():
    graph = StateGraph(MessagesState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", ToolNode(ALL_TOOLS))

    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    # MemorySaver keeps per-thread conversation state in memory so
    # follow-up questions in the same session have context. Swap for a
    # persistent checkpointer (e.g. SqliteSaver) if you want sessions to
    # survive a restart.
    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


agent = build_agent()


def ask(question: str, thread_id: str = "default") -> str:
    """Convenience wrapper used by both the CLI and the FastAPI endpoint."""
    config = {"configurable": {"thread_id": thread_id}}
    result = agent.invoke({"messages": [("user", question)]}, config=config)
    return result["messages"][-1].content
