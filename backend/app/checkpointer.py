"""Durable chat-thread storage: the checkpointer the API's graph runs with
(api/main.py's lifespan), and LangGraph Studio's when debugging (langgraph.json).

Each thread's checkpoints live in the same Atlas cluster as the 10-K index
(collections `checkpoints` / `checkpoint_writes`, separate from RAG's `chunks` /
`companies`), so a conversation survives the Fly machine restarting or spinning
down. The checkpoints are the thread: there is no separate thread record, so any
id with a checkpoint can be read back after a restart.

The built-in `{"backend": "mongo"}` option of langgraph.json isn't used: it talks
to a gRPC checkpointer service that `langgraph dev` doesn't run.

Privacy: with `LANGGRAPH_AES_KEY` set, checkpoint state (messages, channel
values, pending writes) is AES-encrypted before it reaches Atlas, so browsing
the database, a backup or a leaked `MONGODB_URI` shows only ciphertext. The key
is a Fly secret the app itself holds, so this does NOT hide chats from whoever
operates the app; only per-user keys could, and there are no accounts. Checkpoint
`metadata` stays plain because Mongo filters on it. Unset, state is stored
as before (logged); a malformed key refuses to boot rather than silently
storing plain text. Reads accept both forms, so threads written before the key
was set still load. Losing the key makes encrypted threads unreadable.

Retention: every checkpoint carries `created_at` and a TTL index deletes it
after `CHECKPOINT_TTL_DAYS` (30). A thread keeps its latest checkpoint as long as
it is used, since each turn writes a new one; one idle past the limit is gone.
Documents written before the TTL existed have no `created_at`, so `_stamp_legacy`
gives them one (their 30 days start then).

With `MONGODB_URI` unset, or Atlas unreachable at startup, this yields an
in-memory saver (and logs why) rather than keeping the whole app from booting,
as `search_filings` degrades rather than raises. Threads then last only until
the next restart. The terminal chat (cli/session.py) keeps its own MemorySaver
and doesn't use this.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app.config import settings

logger = logging.getLogger(__name__)


def _serde():
    """Encrypting serializer when a key is configured, else None (MongoDBSaver's default)."""
    key = settings.checkpoint_aes_key.encode()
    if not key:
        logger.warning("LANGGRAPH_AES_KEY is not set; chat threads are stored unencrypted in Atlas")
        return None
    if len(key) not in (16, 24, 32):
        raise ValueError("LANGGRAPH_AES_KEY must be 16, 24, or 32 bytes long.")
    return EncryptedSerializer.from_pycryptodome_aes(key=key)


def _stamp_legacy(saver: MongoDBSaver) -> None:
    """Give pre-TTL checkpoints a `created_at` so the TTL index can expire them."""
    now = datetime.now(tz=UTC)
    for collection in (saver.checkpoint_collection, saver.writes_collection):
        collection.update_many({"created_at": {"$exists": False}}, {"$set": {"created_at": now}})


@asynccontextmanager
async def mongo_checkpointer():
    if not settings.mongodb_uri:
        logger.warning("MONGODB_URI is not set; chat threads will not survive a restart")
        yield InMemorySaver()
        return

    serde = _serde()  # before connecting: a bad key should stop the boot
    # Same options as app/rag.py's client, for the same reason: Fly suspends
    # the VM and resumes it with pooled sockets that are dead on Atlas's side.
    client = MongoClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=5000,
        maxIdleTimeMS=60_000,
        socketTimeoutMS=30_000,
    )

    def build() -> MongoDBSaver:
        saver = MongoDBSaver(
            client,
            settings.mongodb_db,
            ttl=settings.checkpoint_ttl_days * 86400,
            serde=serde,
        )
        _stamp_legacy(saver)
        return saver

    try:
        # The constructor creates indexes synchronously; keep it off the loop.
        saver = await asyncio.to_thread(build)
    except PyMongoError:
        client.close()
        logger.exception("Atlas is unreachable; chat threads will not survive a restart")
        yield InMemorySaver()
        return
    try:
        yield saver
    finally:
        client.close()
