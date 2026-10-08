"""
Fixtures shared by tests/test_rag.py (offline) and tests/test_rag_atlas.py
(against a real Atlas Search deployment): a fake embedder and a fake EDGAR.
"""
import zlib
from unittest.mock import patch

import numpy as np
import pytest

from app.sec_filings import FilingRef, Section


class HashEmbedder:
    """Each word bumps one of 64 dimensions, so texts sharing words point
    the same way. Enough to make dense retrieval meaningful in a test."""

    def encode(self, texts):
        out = np.zeros((len(texts), 64), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in text.lower().split():
                out[row, zlib.crc32(word.strip(".,").encode()) % 64] += 1
        return out


def filing_ref(year: int) -> FilingRef:
    return FilingRef(
        symbol="NVDA",
        company="NVIDIA CORP",
        cik=1045810,
        accession=f"acc-{year}",
        form="10-K",
        filing_date=f"{year}-02-25",
        report_date=f"{year}-01-25",
        url=f"https://www.sec.gov/fake/{year}.htm",
    )


FAKE_SECTIONS = {
    "acc-2026": [
        Section("1", "Item 1. Business", "We sell data center GPUs and networking."),
        Section("1A", "Item 1A. Risk Factors", "Export controls on China restrict H20 sales."),
    ],
    "acc-2025": [
        Section("1", "Item 1. Business", "We sell gaming GPUs."),
        Section("1A", "Item 1A. Risk Factors", "We depend on TSMC in Taiwan for wafers."),
    ],
}


@pytest.fixture
def fake_edgar():
    with patch(
        "app.rag.list_annual_reports", return_value=[filing_ref(2026), filing_ref(2025)]
    ) as mock_list, patch(
        "app.rag.fetch_sections", side_effect=lambda ref: FAKE_SECTIONS[ref.accession]
    ) as mock_fetch:
        yield mock_list, mock_fetch
