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


def _log(res):
    return res.text.split('<template id="thread">')[1].split("</template>")[0]


def _clears_url(res):
    return res.status_code == 200 and 'history.replaceState(null, "", "/")' in res.text


def test_thread_page_streams_shell_then_visible_messages(get):
    res = get(FakeClient())
    assert res.headers["cache-control"] == "no-store"
    assert '<div id="log" class="loading">' in res.text
    assert res.text.index("</footer>") < res.text.index('<template id="thread">')
    assert frontend._SHOW_THREAD in res.text
    log = _log(res)
    assert '<div class="msg user">Show my positions</div>' in log
    assert (
        '<div class="msg assistant"><p>You hold <strong>AAPL</strong>. &lt;/script&gt;</p>' in log
    )
    assert "AAPL 10 @ 150" not in log
    assert _bootstrap(res) == {
        "thread_id": THREAD_ID,
        "active_run_id": None,
        "messages": [
            {"type": "human", "content": "Show my positions"},
            {"type": "ai", "content": ""},
            {"type": "ai", "content": "You hold **AAPL**. </script>"},
        ],
        "interrupt": None,
    }
    assert '</script>"' not in res.text


def test_lost_thread_record_is_recreated(get):
    fake = FakeClient(runs_error=_error(NotFoundError, 404))
    assert _log(get(fake)).count('class="msg ') == 2
    assert fake.created == [
        {"thread_id": THREAD_ID, "if_exists": "do_nothing", "metadata": {"graph_id": "agent"}}
    ]


def test_paused_order_renders_its_ticket(get):
    value = {
        "step": 2,
        "of": 2,
        "symbol": "AAPL",
        "quantity": 3.0,
        "estimated_price": 1234.5,
        "estimated_cost": 3703.5,
        "message": "Place it?",
    }
    runs = [{"run_id": "run-1", "status": "running"}]
    res = get(FakeClient(runs=runs, interrupts=[{"value": value}]))
    assert _bootstrap(res)["interrupt"] == value
    assert _bootstrap(res)["active_run_id"] == "run-1"
    ticket = _log(res).split('<div class="msg ticket">')[1]
    for part in [
        "Final confirmation",
        "Step 2 of 2",
        "3 shares",
        "$1,234.50",
        "$3,703.50",
        "can't be undone",
        'data-reply="yes">Place order',
        'data-reply="no">Cancel',
    ]:
        assert part in ticket


def test_empty_thread_shows_only_while_its_first_run_is_live(get):
    res = get(FakeClient(messages=[]))
    assert _clears_url(res) and 'id="bootstrap"' not in res.text
    runs = [{"run_id": "run-1", "status": "pending"}]
    res = get(FakeClient(messages=[], runs=runs))
    assert '<template id="thread">' not in res.text
    assert _bootstrap(res)["active_run_id"] == "run-1"


def test_versioned_static_files_are_cached_as_immutable():
    res = TestClient(frontend.app).get(f"/static/style.css?v={frontend._STYLE_CSS_VERSION}")
    assert res.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_malformed_thread_id_redirects_home_without_reading(get):
    assert _redirects_home(get(FakeClient(state_error=AssertionError()), "garbage"))


def test_unreadable_thread_clears_the_url(get):
    assert _clears_url(get(FakeClient(state_error=_error(APIStatusError, 500))))
