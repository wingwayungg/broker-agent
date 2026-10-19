"""The API end to end: real graph, real tools on the mock broker, a scripted model in
place of the LLM, and an in-memory checkpointer in place of Atlas."""

import asyncio
import json
import re
import threading
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from langgraph.checkpoint.memory import InMemorySaver

from api import main
from api.events import sse
from api.runs import Run, RunInProgress, RunManager
from app.agent import build_graph


class ScriptedModel(GenericFakeChatModel):
    """Replies with the scripted messages in order: text streamed word by word, tool
    calls in one chunk. With a `gate`, each reply waits for it to be set."""

    gate: Any = None

    def bind_tools(self, tools, **kwargs):
        return self

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        if self.gate is not None:
            self.gate.wait(10)
        message = next(self.messages)
        if isinstance(message, str):
            message = AIMessage(content=message)
        if message.tool_calls:
            call_chunks = [
                {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                for i, c in enumerate(message.tool_calls)
            ]
            chunks = [AIMessageChunk(content="", tool_call_chunks=call_chunks)]  # type: ignore[arg-type]
        else:
            words = re.split(r"(\s)", str(message.content))
            chunks = [AIMessageChunk(content=word) for word in words if word]
        for message_chunk in chunks:
            chunk = ChatGenerationChunk(message=message_chunk)
            if run_manager:
                run_manager.on_llm_new_token(str(message_chunk.content), chunk=chunk)
            yield chunk


def tool_call(name: str, **args) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{name}"}])


def parse_events(text: str) -> list[tuple[str, Any]]:
    events = []
    for block in text.strip().split("\n\n"):
        event, data = block.split("\n")
        events.append((event.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return events


def reply_text(events) -> str:
    return "".join(data["text"] for event, data in events if event == "token")


@pytest.fixture(autouse=True)
def _offline_prices():
    with patch("app.broker_client.fetch_current_price", return_value=None):
        yield


@pytest.fixture
def make_client(monkeypatch):
    clients = []

    def make(*script, gate=None) -> TestClient:
        model = ScriptedModel(messages=iter(script), gate=gate)

        @asynccontextmanager
        async def memory_checkpointer():
            yield InMemorySaver()

        monkeypatch.setattr(main, "mongo_checkpointer", memory_checkpointer)
        monkeypatch.setattr(main, "build_graph", lambda saver: build_graph(saver, model=model))
        # Entered, so the lifespan runs and every request shares one event loop:
        # runs are background tasks that outlive the request that started them.
        client = TestClient(main.app).__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def new_thread(client: TestClient) -> str:
    response = client.post("/api/threads")
    assert response.status_code == 201
    return response.json()["thread_id"]


def send(client: TestClient, thread_id: str, text: str) -> list[tuple[str, Any]]:
    response = client.post(f"/api/threads/{thread_id}/messages", json={"text": text})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_events(response.text)


def saved_messages(client: TestClient, thread_id: str) -> list:
    state = client.app.state.runs.graph.get_state({"configurable": {"thread_id": thread_id}})  # type: ignore[attr-defined]
    return state.values["messages"]


def test_a_reply_streams_as_tokens_and_is_saved_to_the_thread(make_client):
    client = make_client("You hold three stocks.")
    thread_id = new_thread(client)

    events = send(client, thread_id, "What do I hold?")
    assert reply_text(events) == "You hold three stocks."
    assert len([e for e, _ in events if e == "token"]) > 1
    assert events[-1] == ("done", {})

    view = client.get(f"/api/threads/{thread_id}").json()
    assert view["messages"] == [
        {"role": "user", "content": "What do I hold?"},
        {"role": "assistant", "content": "You hold three stocks."},
    ]
    assert view["interrupt"] is None
    assert view["active_run"] is False


def test_a_tool_call_shows_as_a_status_and_its_output_stays_out_of_the_chat(make_client):
    client = make_client(tool_call("get_positions"), "Here are your positions.")
    thread_id = new_thread(client)

    events = send(client, thread_id, "Show my positions")
    assert ("status", {"label": "Checking your positions"}) in events
    assert reply_text(events) == "Here are your positions."
    # The tool ran on the mock broker and its result is in the checkpoint, not the view.
    tool_results = [m for m in saved_messages(client, thread_id) if isinstance(m, ToolMessage)]
    assert "AAPL" in tool_results[0].content
    view = client.get(f"/api/threads/{thread_id}").json()
    assert [m["role"] for m in view["messages"]] == ["user", "assistant"]


def test_research_sub_agent_calls_are_not_streamed_as_the_reply(make_client):
    def research_with_a_sub_agent(symbol: str) -> str:
        notes = GenericFakeChatModel(messages=iter(["SUB-AGENT NOTES"])).invoke("analyze")
        return f"Summary of {symbol}: {notes.content}"

    client = make_client(tool_call("research_stock", symbol="nvda"), "NVIDIA overview.")
    thread_id = new_thread(client)
    with patch("app.tools._research_stock", research_with_a_sub_agent):
        events = send(client, thread_id, "Research NVDA")

    assert ("status", {"label": "Researching NVDA"}) in events
    assert reply_text(events) == "NVIDIA overview."
    assert "SUB-AGENT" not in json.dumps(events)


def test_an_order_needs_two_yeses_before_it_is_submitted(make_client):
    client = make_client(tool_call("buy_stock", symbol="AAPL", quantity=1), "Your order is in.")
    thread_id = new_thread(client)

    events = send(client, thread_id, "Buy 1 share of AAPL")
    interrupts = [data for event, data in events if event == "interrupt"]
    assert [i["step"] for i in interrupts] == [1]
    assert interrupts[0]["symbol"] == "AAPL"
    # A reloaded page gets the pending ticket from the thread.
    assert client.get(f"/api/threads/{thread_id}").json()["interrupt"]["step"] == 1

    events = send(client, thread_id, "yes")
    assert [d["step"] for e, d in events if e == "interrupt"] == [2]

    events = send(client, thread_id, "yes")
    assert reply_text(events) == "Your order is in."
    assert "Order submitted" in saved_messages(client, thread_id)[-2].content
    assert client.get(f"/api/threads/{thread_id}").json()["interrupt"] is None


def test_anything_but_yes_cancels_the_order(make_client):
    client = make_client(tool_call("buy_stock", symbol="AAPL", quantity=1), "Cancelled it.")
    thread_id = new_thread(client)
    send(client, thread_id, "Buy 1 share of AAPL")

    events = send(client, thread_id, "actually no")
    assert reply_text(events) == "Cancelled it."
    tool_result = saved_messages(client, thread_id)[-2].content
    assert "cancelled" in tool_result.lower()
    assert "Order submitted" not in tool_result


def test_a_running_thread_rejects_a_second_send_and_a_rejoin_gets_the_whole_run(make_client):
    # TestClient buffers a streamed response until it ends, so the run in flight is
    # placed in the registry directly; RunManager itself is tested below.
    client = make_client()
    thread_id = new_thread(client)
    run = Run()
    client.app.state.runs._runs[thread_id] = run  # type: ignore[attr-defined]

    assert client.post(f"/api/threads/{thread_id}/messages", json={"text": "hi"}).status_code == 409
    assert client.get(f"/api/threads/{thread_id}").json()["active_run"] is True

    client.portal.call(run.publish, sse("token", {"id": "m1", "text": "Already sent."}))  # type: ignore[union-attr]
    client.portal.call(run.finish)  # type: ignore[union-attr]
    rejoined = parse_events(client.get(f"/api/threads/{thread_id}/stream").text)
    assert rejoined == [("token", {"id": "m1", "text": "Already sent."})]

    del client.app.state.runs._runs[thread_id]  # type: ignore[attr-defined]
    assert client.get(f"/api/threads/{thread_id}/stream").status_code == 204


def test_run_manager_allows_one_run_per_thread_and_replays_it_to_every_subscriber():
    async def collect(events) -> list[str]:
        return [event async for event in events]

    async def scenario():
        gate = threading.Event()
        model = ScriptedModel(messages=iter(["A slow reply."]), gate=gate)
        runs = RunManager(build_graph(InMemorySaver(), model=model))

        run = await runs.start("t1", "hi")
        with pytest.raises(RunInProgress):
            await runs.start("t1", "again")
        assert runs.active("t1") is run

        live = asyncio.create_task(collect(run.follow()))
        gate.set()
        live_events = await live
        late_events = await collect(run.follow())
        return runs, live_events, late_events

    runs, live_events, late_events = asyncio.run(scenario())
    assert live_events == late_events
    assert reply_text(parse_events("".join(live_events))) == "A slow reply."
    assert runs.active("t1") is None


def test_unknown_and_malformed_thread_ids(make_client):
    client = make_client()
    assert client.get("/api/threads/00000000-0000-4000-8000-000000000000").status_code == 404
    assert client.get("/api/threads/not-a-uuid").status_code == 422
    assert client.post("/api/threads/not-a-uuid/messages", json={"text": "hi"}).status_code == 422
    empty = client.post(f"/api/threads/{new_thread(client)}/messages", json={"text": ""})
    assert empty.status_code == 422
