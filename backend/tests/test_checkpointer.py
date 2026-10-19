"""
Tests for app/checkpointer.py, the saver the API's graph runs with.

The unset-URI fallback runs offline. The persistence test needs a MongoDB and
is skipped unless MONGODB_TEST_URI is set (any MongoDB works; the Atlas Local
container from tests/test_rag_atlas.py is fine). It uses its own database,
dropped afterwards.
"""

import asyncio
import os
import uuid
from unittest.mock import patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.graph import END, START, MessagesState, StateGraph
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

from app import checkpointer
from app.checkpointer import mongo_checkpointer

_URI = os.getenv("MONGODB_TEST_URI")


def test_falls_back_to_memory_without_a_uri():
    async def saver_type():
        async with mongo_checkpointer() as saver:
            return type(saver)

    with patch.object(checkpointer.settings, "mongodb_uri", ""):
        assert asyncio.run(saver_type()) is InMemorySaver


def test_falls_back_to_memory_when_the_database_is_unreachable():
    async def saver_type():
        async with mongo_checkpointer() as saver:
            return type(saver)

    with (
        patch.object(checkpointer.settings, "mongodb_uri", "mongodb://x"),
        patch.object(
            checkpointer, "MongoDBSaver", side_effect=ServerSelectionTimeoutError("timed out")
        ),
    ):
        assert asyncio.run(saver_type()) is InMemorySaver


def test_no_key_means_the_default_serializer():
    with patch.object(checkpointer.settings, "checkpoint_aes_key", ""):
        assert checkpointer._serde() is None


def test_a_key_of_the_wrong_length_refuses_to_boot():
    async def boot():
        async with mongo_checkpointer():
            pass

    with (
        patch.object(checkpointer.settings, "mongodb_uri", "mongodb://x"),
        patch.object(checkpointer.settings, "checkpoint_aes_key", "too-short"),
    ):
        with pytest.raises(ValueError, match="16, 24, or 32"):
            asyncio.run(boot())


def test_a_key_encrypts_what_the_serializer_writes():
    with patch.object(checkpointer.settings, "checkpoint_aes_key", "k" * 32):
        serde = checkpointer._serde()
    assert isinstance(serde, EncryptedSerializer)
    typ, blob = serde.dumps_typed({"note": "secret position: 500 NVDA"})
    assert b"NVDA" not in blob
    assert serde.loads_typed((typ, blob)) == {"note": "secret position: 500 NVDA"}


@pytest.mark.skipif(not _URI, reason="MONGODB_TEST_URI not set")
def test_encrypted_threads_expire_and_old_plain_ones_still_load():
    graph = StateGraph(MessagesState)
    graph.add_node("echo", lambda state: {"messages": [("ai", "reply")]})
    graph.add_edge(START, "echo")
    graph.add_edge("echo", END)
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    db = f"test_checkpointer_{uuid.uuid4().hex[:8]}"

    async def run(turn: bool):
        async with mongo_checkpointer() as saver:
            compiled = graph.compile(checkpointer=saver)
            if turn:
                await compiled.ainvoke({"messages": [("user", "my secret NVDA order")]}, config)
            return (await compiled.aget_state(config)).values["messages"]

    try:
        with (
            patch.object(checkpointer.settings, "mongodb_uri", _URI),
            patch.object(checkpointer.settings, "mongodb_db", db),
        ):
            with patch.object(checkpointer.settings, "checkpoint_aes_key", ""):
                asyncio.run(run(turn=True))  # written as plain text
            # strip created_at to look like a pre-TTL document
            with MongoClient(_URI, serverSelectionTimeoutMS=5000) as client:
                client[db]["checkpoints"].update_many({}, {"$unset": {"created_at": ""}})
            with patch.object(checkpointer.settings, "checkpoint_aes_key", "k" * 32):
                old = asyncio.run(run(turn=False))
                asyncio.run(run(turn=True))  # written encrypted
                new = asyncio.run(run(turn=False))
            with MongoClient(_URI, serverSelectionTimeoutMS=5000) as client:
                docs = list(client[db]["checkpoints"].find())
                ttl = [
                    i for i in client[db]["checkpoints"].list_indexes() if "expireAfterSeconds" in i
                ]
    finally:
        with MongoClient(_URI, serverSelectionTimeoutMS=5000) as client:
            client.drop_database(db)

    assert [m.content for m in old] == ["my secret NVDA order", "reply"]
    assert len(new) == 4
    assert all("created_at" in d for d in docs)  # legacy ones were stamped
    assert any("+" in d["type"] for d in docs)  # cipher name is appended to the type
    assert ttl[0]["expireAfterSeconds"] == 30 * 86400


@pytest.mark.skipif(not _URI, reason="MONGODB_TEST_URI not set")
def test_thread_survives_a_new_saver_on_the_same_database():
    """A fresh saver stands in for a restarted server."""
    graph = StateGraph(MessagesState)
    graph.add_node("echo", lambda state: {"messages": [("ai", "hello back")]})
    graph.add_edge(START, "echo")
    graph.add_edge("echo", END)
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    db = f"test_checkpointer_{uuid.uuid4().hex[:8]}"

    async def run(first_turn: bool):
        async with mongo_checkpointer() as saver:
            compiled = graph.compile(checkpointer=saver)
            if first_turn:
                await compiled.ainvoke({"messages": [("user", "hello")]}, config)
            return (await compiled.aget_state(config)).values["messages"]

    try:
        with (
            patch.object(checkpointer.settings, "mongodb_uri", _URI),
            patch.object(checkpointer.settings, "mongodb_db", db),
        ):
            asyncio.run(run(first_turn=True))
            messages = asyncio.run(run(first_turn=False))
    finally:
        with MongoClient(_URI, serverSelectionTimeoutMS=5000) as client:
            client.drop_database(db)

    assert [m.content for m in messages] == ["hello", "hello back"]
