import json
import re

import httpx
import pytest
from langgraph_sdk.errors import APIStatusError, NotFoundError
from starlette.testclient import TestClient

from app import frontend

THREAD_ID = "11111111-2222-3333-4444-555555555555"
BOOTSTRAP_RE = re.compile(r'id="bootstrap" type="application/json">(.*?)</script>')
HISTORY = [
    {"type": "human", "content": "Show my positions"},
    {"type": "ai", "content": "", "tool_calls": [{"name": "get_positions"}]},
    {"type": "tool", "content": "AAPL 10 @ 150"},
    {"type": "ai", "content": "You hold **AAPL**. </script>"},
]


def _error(cls, status):
    response = httpx.Response(status, request=httpx.Request("GET", "http://api"))
    return cls("error", response=response, body=None)


class FakeClient:
    """The SDK client's `runs.list`/`threads.create`/`threads.get_state`, in one object."""

    def __init__(self, messages=HISTORY, runs=(), interrupts=(), runs_error=None, state_error=None):
        self.runs = self.threads = self
        self.messages, self.run_list, self.interrupts = messages, list(runs), list(interrupts)
        self.runs_error, self.state_error = runs_error, state_error
        self.created = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def list(self, thread_id, limit):
        if self.runs_error:
            raise self.runs_error
        return self.run_list

    async def create(self, **kwargs):
        self.created.append(kwargs)

    async def get_state(self, thread_id):
        if self.state_error:
            raise self.state_error
        return {"values": {"messages": self.messages}, "interrupts": self.interrupts}


@pytest.fixture
def get(monkeypatch):
    def _get(fake, thread_id=THREAD_ID):
        monkeypatch.setattr(frontend, "get_client", lambda: fake)
        params = {} if thread_id is None else {"thread_id": thread_id}
        return TestClient(frontend.app, follow_redirects=False).get("/", params=params)

    return _get


def _bootstrap(res):
    return json.loads(BOOTSTRAP_RE.search(res.text)[1])


def _redirects_home(res):
    return (res.status_code, res.headers.get("location")) == (307, "/")


def test_home_page_never_reads_a_thread(get):
    res = get(FakeClient(state_error=AssertionError()), thread_id=None)
    assert '<div id="log" class="empty">' in res.text
    assert 'id="bootstrap"' not in res.text


def test_thread_page_inlines_rendered_messages_only(get):
    res = get(FakeClient())
    assert res.headers["cache-control"] == "no-store"
    assert '<div id="log" class="loading">' in res.text
    # The model's text can't close the bootstrap <script> early.
    assert res.text.count("</script>") == 2
    assert _bootstrap(res) == {
        "thread_id": THREAD_ID,
        "state": {"values": {"messages": [HISTORY[0], HISTORY[3]]}, "interrupts": []},
        "active_run_id": None,
    }


def test_lost_thread_record_is_recreated(get):
    fake = FakeClient(runs_error=_error(NotFoundError, 404))
    assert len(_bootstrap(get(fake))["state"]["values"]["messages"]) == 2
    assert fake.created == [
        {"thread_id": THREAD_ID, "if_exists": "do_nothing", "metadata": {"graph_id": "agent"}}
    ]


def test_paused_order_and_live_run_are_passed_to_the_page(get):
    interrupt = {"value": {"type": "confirm_buy_order", "step": 1, "of": 2}}
    runs = [{"run_id": "run-1", "status": "running"}]
    data = _bootstrap(get(FakeClient(runs=runs, interrupts=[interrupt])))
    assert (data["active_run_id"], data["state"]["interrupts"]) == ("run-1", [interrupt])


def test_empty_thread_shows_only_while_its_first_run_is_live(get):
    assert _redirects_home(get(FakeClient(messages=[])))
    runs = [{"run_id": "run-1", "status": "pending"}]
    assert _bootstrap(get(FakeClient(messages=[], runs=runs)))["active_run_id"] == "run-1"


@pytest.mark.parametrize(
    "thread_id, fake",
    [
        ("garbage", FakeClient(state_error=AssertionError())),
        (THREAD_ID, FakeClient(state_error=_error(APIStatusError, 500))),
    ],
)
def test_malformed_or_unreadable_thread_redirects_home(get, thread_id, fake):
    assert _redirects_home(get(fake, thread_id))
