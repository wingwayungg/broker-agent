"""Durable chat-thread storage for `langgraph dev` (referenced by langgraph.json).

Under `langgraph dev` the platform keeps checkpoints in memory and pickles them
to `.langgraph_api/`, which Fly's ephemeral disk loses on every restart or
spin-down. langgraph.json's `checkpointer` key swaps that for the saver this
module yields, so each thread's checkpoints live in the same Atlas cluster as
the 10-K index (collections `checkpoints` / `checkpoint_writes`, separate from
RAG's `chunks` / `companies`).

Only checkpoints move: the platform still keeps its own thread *records* (id,
status, metadata) in memory, so after a restart `GET /threads/{id}` is 404
until the frontend recreates the record under the same id (`recreateThread` in
app/static/app.js); the state is then read back from here.

The built-in `{"backend": "mongo"}` checkpointer option isn't used: it talks to
a gRPC checkpointer service that `langgraph dev` doesn't run.

With `MONGODB_URI` unset, or Atlas unreachable at startup, this yields an
in-memory saver (and logs why) rather than keeping the whole app from booting,
as `search_filings` degrades rather than raises. Threads then last only until
the next restart, as they did before this module. The in-process `agent` in app/agent.py (CLI and /ask)
keeps its own MemorySaver and doesn't use this.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.mongodb import MongoDBSaver
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app.config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def mongo_checkpointer():
    if not settings.mongodb_uri:
        logger.warning("MONGODB_URI is not set; chat threads will not survive a restart")
        yield InMemorySaver()
        return

    # Same options as app/rag.py's client, for the same reason: Fly suspends
    # the VM and resumes it with pooled sockets that are dead on Atlas's side.
    client = MongoClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=5000,
        maxIdleTimeMS=60_000,
        socketTimeoutMS=30_000,
    )
    try:
        # The constructor creates indexes synchronously; keep it off the loop.
        saver = await asyncio.to_thread(MongoDBSaver, client, settings.mongodb_db)
    except PyMongoError:
        client.close()
        logger.exception("Atlas is unreachable; chat threads will not survive a restart")
        yield InMemorySaver()
        return
    try:
        yield saver
    finally:
        client.close()
