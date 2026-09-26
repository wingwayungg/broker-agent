"""
The in-process way to drive one conversation turn, shared by cli.py and
api.py. Lives here rather than in app/agent.py because nothing that ships
uses it: the browser UI posts straight to the LangGraph API and makes the
same resume-vs-new-message decision itself in app/static/app.js.
"""
from langgraph.types import Command

from app.agent import agent


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
