"""Background agent runs whose event streams can be re-joined.

A run outlives the request that started it: closing or reloading the tab doesn't
cancel it, and the reloaded page re-joins it. Each run keeps its whole event log,
and a subscriber replays it from the start before following live events, so a
re-joined page shows the complete reply rather than only what was produced after
it connected.

Runs are tracked in this process, which is enough for the single Fly machine this
deploys to. Several instances would need a shared registry (e.g. Redis pub/sub)
and sticky routing for the streams.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from api.events import run_events, sse

logger = logging.getLogger(__name__)


class RunInProgress(Exception):
    pass


class Run:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.done = False
        self.task: asyncio.Task | None = None
        self._changed = asyncio.Condition()

    async def publish(self, event: str) -> None:
        async with self._changed:
            self.events.append(event)
            self._changed.notify_all()

    async def finish(self) -> None:
        async with self._changed:
            self.done = True
            self._changed.notify_all()

    async def follow(self) -> AsyncIterator[str]:
        sent = 0
        while True:
            async with self._changed:
                await self._changed.wait_for(lambda n=sent: n < len(self.events) or self.done)
                batch, done = self.events[sent:], self.done
            sent += len(batch)
            for event in batch:
                yield event
            if done and sent == len(self.events):
                return


def thread_config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


class RunManager:
    def __init__(self, graph: CompiledStateGraph) -> None:
        self.graph = graph
        self._runs: dict[str, Run] = {}

    def active(self, thread_id: str) -> Run | None:
        return self._runs.get(thread_id)

    async def start(self, thread_id: str, text: str) -> Run:
        """Start a run with the user's text, raising RunInProgress if one is running.

        A thread paused on buy_stock's interrupt takes the text as the answer to it
        (Command(resume=...)), the same rule as cli/session.py's ask(); anything but
        an affirmative reply cancels the order there.
        """
        if thread_id in self._runs:
            raise RunInProgress(thread_id)
        # Claimed before the first await, so two concurrent sends can't both start.
        run = self._runs[thread_id] = Run()
        config = thread_config(thread_id)
        try:
            state = await self.graph.aget_state(config)
        except BaseException:
            del self._runs[thread_id]
            raise
        graph_input = (
            Command(resume=text) if state.interrupts else {"messages": [HumanMessage(text)]}
        )
        run.task = asyncio.create_task(self._drive(thread_id, run, graph_input, config))
        return run

    async def _drive(
        self, thread_id: str, run: Run, graph_input: Any, config: RunnableConfig
    ) -> None:
        try:
            async for event, data in run_events(self.graph, graph_input, config):
                await run.publish(sse(event, data))
            await run.publish(sse("done", {}))
        except Exception:
            logger.exception("Run on thread %s failed", thread_id)
            await run.publish(sse("error", {"message": "The agent hit an error. Try again."}))
        finally:
            # The final checkpoint is saved by now, so a page reading the thread
            # after this sees the finished state rather than an active run.
            del self._runs[thread_id]
            await run.finish()

    async def shutdown(self) -> None:
        tasks = [run.task for run in self._runs.values() if run.task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
