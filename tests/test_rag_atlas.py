"""
Tests for app/rag.py's FilingIndex against a real Atlas Search deployment:
ingest, hybrid/BM25/dense retrieval, and filters. Atlas Search and Vector
Search can't be faked offline, so these are skipped unless MONGODB_TEST_URI
points at one — locally, the Atlas Local container:

    docker run -d -p 27017:27017 mongodb/mongodb-atlas-local
    MONGODB_TEST_URI='mongodb://localhost:27017/?directConnection=true' \
        python -m pytest tests/test_rag_atlas.py

Each test gets its own database, dropped afterwards. EDGAR is still faked
(tests/conftest.py).
"""
import os
from unittest.mock import patch
import uuid

import pytest
from pymongo import MongoClient

from app.rag import FilingIndex
from app.sec_filings import FilingUnavailable
from tests.conftest import FAKE_SECTIONS, HashEmbedder

_URI = os.getenv("MONGODB_TEST_URI")
pytestmark = pytest.mark.skipif(not _URI, reason="MONGODB_TEST_URI not set")


@pytest.fixture(scope="module")
def client():
    with MongoClient(_URI, serverSelectionTimeoutMS=5000) as client:
        yield client


@pytest.fixture
def index(client):
    name = f"test_rag_{uuid.uuid4().hex[:8]}"
    yield FilingIndex(client[name], HashEmbedder(), model="hash")
    client.drop_database(name)


def test_ensure_indexed_ingests_once_then_serves_from_the_db(index, fake_edgar):
    mock_list, mock_fetch = fake_edgar
    index.ensure_indexed("NVDA")
    index.ensure_indexed("NVDA")

    assert index.indexed_years("NVDA") == [2026, 2025]
    assert mock_list.call_count == 1
    assert mock_fetch.call_count == 2


def test_ensure_indexed_keeps_serving_old_index_when_edgar_is_down(index, fake_edgar):
    index.ensure_indexed("NVDA")
    with patch("app.rag.time.time", return_value=1e12), patch(
        "app.rag.list_annual_reports", side_effect=FilingUnavailable("EDGAR down")
    ):
        index.ensure_indexed("NVDA")  # must not raise
    assert index.indexed_years("NVDA") == [2026, 2025]


def test_ensure_indexed_raises_when_nothing_could_be_indexed(index):
    with patch("app.rag.list_annual_reports", side_effect=FilingUnavailable("EDGAR down")):
        with pytest.raises(FilingUnavailable, match="EDGAR down"):
            index.ensure_indexed("NVDA")


def test_one_unparseable_filing_does_not_block_the_other(index, fake_edgar):
    with patch(
        "app.rag.fetch_sections",
        side_effect=lambda ref: [] if ref.accession == "acc-2026" else FAKE_SECTIONS[ref.accession],
    ):
        index.ensure_indexed("NVDA")
    assert index.indexed_years("NVDA") == [2025]


def test_another_chunk_size_is_indexed_separately(client, index, fake_edgar):
    index.ensure_indexed("NVDA")
    other = FilingIndex(index._chunks.database, HashEmbedder(), model="hash", chunk_words=100)
    assert other.indexed_years("NVDA") == []


@pytest.mark.parametrize("mode", ["hybrid", "bm25", "dense"])
def test_retrieve_ranks_the_matching_chunk_first(index, fake_edgar, mode):
    index.ensure_indexed("NVDA")
    hits = index.retrieve("NVDA", "TSMC Taiwan wafers", mode=mode)
    assert "TSMC" in hits[0].text
    assert hits[0].fiscal_year == 2025
    assert hits[0].citation == "NVDA 10-K FY2025 · Item 1A. Risk Factors · filed 2025-02-25"


def test_retrieve_applies_year_and_section_filters(index, fake_edgar):
    index.ensure_indexed("NVDA")
    hits = index.retrieve("NVDA", "GPUs", fiscal_year=2026, section="business")
    assert [(h.fiscal_year, h.section_title) for h in hits] == [(2026, "Item 1. Business")]


def test_retrieve_tolerates_fts_syntax_in_the_query(index, fake_edgar):
    index.ensure_indexed("NVDA")
    # Quotes, NOT, colons and parentheses are query syntax in most full-text engines.
    hits = index.retrieve("NVDA", 'export "controls" NOT (China): H20*', mode="bm25")
    assert "Export controls" in hits[0].text
