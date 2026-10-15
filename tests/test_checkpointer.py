"""
Tests for app/checkpointer.py, the saver langgraph.json gives `langgraph dev`.

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

    with patch.object(checkpointer.settings, "mongodb_uri", "mongodb://x"), \
            patch.object(checkpointer, "MongoDBSaver", side_effect=ServerSelectionTimeoutError("timed out")):
        assert asyncio.run(saver_type()) is InMemorySaver


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
        with patch.object(checkpointer.settings, "mongodb_uri", _URI), \
                patch.object(checkpointer.settings, "mongodb_db", db):
            asyncio.run(run(first_turn=True))
            messages = asyncio.run(run(first_turn=False))
    finally:
        with MongoClient(_URI, serverSelectionTimeoutMS=5000) as client:
            client.drop_database(db)

    assert [m.content for m in messages] == ["hello", "hello back"]
