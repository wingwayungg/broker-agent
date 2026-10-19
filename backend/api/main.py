"""The agent's HTTP API, served by uvicorn behind the Next.js app (frontend/).

Threads exist only as checkpoints in Atlas (app/checkpointer.py): there is no
separate thread record to lose when the Fly machine restarts, so any thread id
with a checkpoint can be read back. Creating a thread just mints an id; its first
checkpoint is written by its first run.

Unauthenticated, like the rest of this demo: the broker is mocked, so a stranger
with the URL can spend API credits but not money. Accounts would go here, as a
dependency on these routes, before connecting a real broker.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from api.runs import RunInProgress, RunManager, thread_config
from api.schemas import MessageView, NewThread, SendMessage, ThreadView
from app.agent import build_graph
from app.checkpointer import mongo_checkpointer


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with mongo_checkpointer() as saver:
        app.state.runs = RunManager(build_graph(saver))
        yield
        await app.state.runs.shutdown()


app = FastAPI(
    title="Broker Portfolio Agent API",
    lifespan=lifespan,
    # Under /api so they're reachable through the Next.js proxy too.
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)


def _runs(request: Request) -> RunManager:
    return request.app.state.runs


def _event_stream(events: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/threads", status_code=201)
async def create_thread() -> NewThread:
    return NewThread(thread_id=uuid4())


@app.get("/api/threads/{thread_id}", responses={404: {"description": "No such thread"}})
async def read_thread(thread_id: UUID, request: Request) -> ThreadView:
    runs = _runs(request)
    # Run status before state: a run finishing between the two reads then shows up
    # in the state, rather than leaving the page with neither the reply nor a run
    # to re-join.
    active = runs.active(str(thread_id)) is not None
    state = await runs.graph.aget_state(thread_config(str(thread_id)))
    if not state.values and not active:
        raise HTTPException(404, "No such thread")

    messages = []
    for m in state.values.get("messages", []):
        if not isinstance(m.content, str):
            continue
        if isinstance(m, HumanMessage):
            messages.append(MessageView(role="user", content=m.content))
        elif isinstance(m, AIMessage) and m.content:
            messages.append(MessageView(role="assistant", content=m.content))
    interrupt = state.interrupts[0].value if state.interrupts else None
    return ThreadView(
        thread_id=thread_id, messages=messages, interrupt=interrupt, active_run=active
    )


@app.post(
    "/api/threads/{thread_id}/messages",
    response_class=StreamingResponse,
    responses={409: {"description": "A run is already in progress on this thread"}},
)
async def send_message(thread_id: UUID, body: SendMessage, request: Request):
    try:
        run = await _runs(request).start(str(thread_id), body.text)
    except RunInProgress:
        raise HTTPException(409, "A run is already in progress on this thread") from None
    return _event_stream(run.follow())


@app.get(
    "/api/threads/{thread_id}/stream",
    response_class=StreamingResponse,
    responses={204: {"description": "No run in progress"}},
)
async def join_run(thread_id: UUID, request: Request):
    run = _runs(request).active(str(thread_id))
    if run is None:
        return Response(status_code=204)
    return _event_stream(run.follow())
