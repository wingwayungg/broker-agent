"""
Offline tests for app/rag.py — chunking, the Atlas aggregation pipelines it
builds, and the search_filings tool. Ingest and ranking need a real Atlas
Search deployment and live in tests/test_rag_atlas.py; here the tool runs
against a stub index, so nothing touches the network or loads weights.
"""
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import pytest
from pymongo.errors import ServerSelectionTimeoutError

from app import rag
from app.rag import Hit, _pipeline, chunk_text
from app.sec_filings import FilingUnavailable
from app.tools import search_filings


def test_chunk_text_packs_paragraphs_with_overlap():
    paragraphs = [" ".join([f"p{i}"] * 40) for i in range(5)]  # five 40-word paragraphs
    chunks = chunk_text("\n".join(paragraphs), target_words=100, overlap_words=40)

    assert chunks[0].split("\n") == paragraphs[0:2]
    # The last paragraph of each chunk opens the next one.
    assert chunks[1].split("\n") == paragraphs[1:3]
    assert chunks[-1].split("\n")[-1] == paragraphs[-1]
    assert all(len(c.split()) <= 100 for c in chunks)


def test_chunk_text_splits_a_paragraph_longer_than_the_target():
    chunks = chunk_text(" ".join(["w"] * 250), target_words=100, overlap_words=0)
    assert [len(c.split()) for c in chunks] == [100, 100, 50]


_FILTERS = {"variant": "hash-250w", "symbol": "NVDA'; drop", "fiscal_year": 2026, "section_item": "1A"}


def _stage(pipeline, name):
    return next(s[name] for s in pipeline if name in s)


def test_pipeline_bm25_filters_by_value_inside_search():
    search = _stage(_pipeline("bm25", 'say "NOT"', None, _FILTERS, 5), "$search")
    assert search["compound"]["must"] == [{"text": {"query": 'say "NOT"', "path": "text"}}]
    assert search["compound"]["filter"] == [
        {"equals": {"path": p, "value": v}} for p, v in _FILTERS.items()
    ]


def test_pipeline_dense_prefilters_vector_search():
    pipeline = _pipeline("dense", "q", [0.1, 0.2], _FILTERS, 5)
    vector_search = _stage(pipeline, "$vectorSearch")
    assert vector_search["limit"] == 5
    assert vector_search["filter"] == {"$and": [{p: {"$eq": v}} for p, v in _FILTERS.items()]}
    assert pipeline[-1]["$project"]["score"] == {"$meta": "vectorSearchScore"}


def test_pipeline_hybrid_fuses_both_searches_with_the_same_filters():
    pipeline = _pipeline("hybrid", "q", [0.1, 0.2], _FILTERS, 5)
    fusion = _stage(pipeline, "$rankFusion")["input"]["pipelines"]
    assert _stage(fusion["vector"], "$vectorSearch")["filter"] == _stage(
        _pipeline("dense", "q", [0.1, 0.2], _FILTERS, 5), "$vectorSearch"
    )["filter"]
    assert _stage(fusion["text"], "$search") == _stage(
        _pipeline("bm25", "q", None, _FILTERS, 5), "$search"
    )
    assert {"$limit": 5} in pipeline
    assert pipeline[-1]["$project"]["score"] == {"$meta": "score"}


def _hit(year: int, section: str, text: str) -> Hit:
    return Hit(
        symbol="NVDA",
        company="NVIDIA CORP",
        fiscal_year=year,
        filing_date=f"{year}-02-25",
        url=f"https://www.sec.gov/fake/{year}.htm",
        section_title=section,
        text=text,
        score=1.0,
    )


class _StubIndex:
    """Stands in for FilingIndex: two indexed years, one hit per year."""

    def __init__(self):
        self.retrieved = []

    def ensure_indexed(self, symbol):
        pass

    def indexed_years(self, symbol):
        return [2026, 2025]

    def retrieve(self, symbol, query, *, fiscal_year=None, section=None):
        self.retrieved.append((symbol, query, fiscal_year, section))
        if fiscal_year == 2026:
            return [_hit(2026, "Item 1A. Risk Factors", "Export controls on China restrict H20 sales.")]
        return [_hit(2025, "Item 1A. Risk Factors", "We depend on TSMC in Taiwan for wafers.")]


@pytest.fixture
def stub_index():
    index = _StubIndex()
    with patch.object(rag, "_index", index):
        yield index


def _search(args: dict, messages: list | None = None, call_id: str = "c1") -> str:
    """Invoke the tool the way ToolNode does: as a tool call, with the
    thread's messages injected."""
    call = {"type": "tool_call", "name": "search_filings", "id": call_id, "args": args}
    if messages is None:
        messages = [HumanMessage("q"), AIMessage("", tool_calls=[call])]
    return search_filings.invoke({**call, "args": {**args, "messages": messages}}).content


def test_search_filings_numbers_continue_after_earlier_searches(stub_index):
    first = _search({"symbol": "NVDA", "query": "risks"})
    assert "\n[1] NVDA 10-K FY2026" in first

    second_call = {"name": "search_filings", "id": "c2", "args": {"symbol": "NVDA", "query": "risks", "fiscal_year": 2025}}
    thread = [
        HumanMessage("q"),
        AIMessage("", tool_calls=[{"name": "search_filings", "id": "c1", "args": {}}]),
        ToolMessage(first, tool_call_id="c1", name="search_filings"),
        AIMessage("", tool_calls=[second_call]),
    ]
    second = _search(second_call["args"], thread, call_id="c2")
    # One hit came back before, so this search starts at [2], not [1] again.
    assert "\n[2] NVDA 10-K FY2025" in second
    assert "\n[1] " not in second


def test_parallel_search_filings_calls_get_separate_number_blocks(stub_index):
    calls = [
        {"name": "search_filings", "id": "a", "args": {"symbol": "NVDA", "query": "risks"}},
        {"name": "search_filings", "id": "b", "args": {"symbol": "NVDA", "query": "risks", "fiscal_year": 2025}},
    ]
    thread = [HumanMessage("q"), AIMessage("", tool_calls=calls)]

    assert "\n[1] NVDA 10-K FY2026" in _search(calls[0]["args"], thread, call_id="a")
    assert f"\n[{1 + rag.EXCERPTS_PER_SEARCH}] NVDA 10-K FY2025" in _search(calls[1]["args"], thread, call_id="b")


def test_search_filings_tool_returns_numbered_cited_excerpts(stub_index):
    result = _search({"symbol": "nvda", "query": "export controls China"})

    assert result.startswith("Excerpts from NVIDIA CORP (NVDA) 10-K filings")
    assert "indexed fiscal years: 2026, 2025" in result
    assert "[1] NVDA 10-K FY2026 · Item 1A. Risk Factors · filed 2026-02-25 · https://www.sec.gov/fake/2026.htm\nExport controls" in result


def test_search_filings_tool_defaults_to_the_latest_fiscal_year(stub_index):
    _search({"symbol": "NVDA", "query": "TSMC Taiwan wafers"})
    assert stub_index.retrieved == [("NVDA", "TSMC Taiwan wafers", 2026, None)]


def test_search_filings_tool_reports_unindexed_year(stub_index):
    result = _search({"symbol": "NVDA", "query": "risks", "fiscal_year": 2019})
    assert result == "[no FY2019 10-K indexed for NVDA; indexed fiscal years: 2026, 2025]"


def test_search_filings_tool_reports_edgar_failure(stub_index):
    with patch.object(stub_index, "ensure_indexed", side_effect=FilingUnavailable("ZZZZ isn't in SEC EDGAR")):
        result = _search({"symbol": "ZZZZ", "query": "risks"})
    assert result == "[filings unavailable: ZZZZ isn't in SEC EDGAR]"


def test_search_filings_tool_reports_unreachable_database(stub_index):
    with patch.object(stub_index, "ensure_indexed", side_effect=ServerSelectionTimeoutError("timed out")):
        result = _search({"symbol": "NVDA", "query": "risks"})
    assert result == "[filings unavailable: the filings database is unreachable]"


def test_search_filings_tool_reports_missing_configuration():
    with patch.object(rag, "_index", None), patch.object(rag.settings, "mongodb_uri", ""):
        result = _search({"symbol": "NVDA", "query": "risks"})
    assert result == "[filings unavailable: 10-K search isn't configured (MONGODB_URI is not set)]"
