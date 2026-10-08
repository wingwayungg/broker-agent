"""
Tests for app/sec_filings.py — EDGAR lookups and 10-K HTML -> sections.
requests.get is mocked so these run offline; the HTML fixtures are small
hand-written imitations of the inline-XBRL quirks real filings have.
"""
from unittest.mock import MagicMock, patch

import pytest

from app import sec_filings
from app.sec_filings import FilingUnavailable, html_to_text, list_annual_reports, split_sections


@pytest.fixture(autouse=True)
def _fresh_ticker_map():
    sec_filings._ticker_map.clear()
    with patch("app.sec_filings.settings.sec_user_agent", "Test Suite test@example.com"):
        yield
    sec_filings._ticker_map.clear()


def _json_response(payload) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


_TICKERS = {"0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"}}
_SUBMISSIONS = {
    "filings": {
        "recent": {
            "form": ["10-Q", "10-K/A", "10-K", "10-Q", "10-K", "10-K"],
            "accessionNumber": ["q1", "amend", "0001-26-000021", "q2", "0001-25-000023", "0001-24-000029"],
            "filingDate": ["2026-05-20", "2026-04-01", "2026-02-25", "2025-11-19", "2025-02-26", "2024-02-21"],
            "reportDate": ["2026-04-26", "2026-01-25", "2026-01-25", "2025-10-26", "2025-01-26", "2024-01-28"],
            "primaryDocument": ["q1.htm", "a.htm", "nvda-20260125.htm", "q2.htm", "nvda-20250126.htm", "nvda-20240128.htm"],
        }
    }
}


def test_list_annual_reports_returns_newest_original_10ks():
    with patch(
        "app.sec_filings.requests.get",
        side_effect=[_json_response(_TICKERS), _json_response(_SUBMISSIONS)],
    ) as mock_get:
        refs = list_annual_reports("nvda", limit=2)

    assert [r.accession for r in refs] == ["0001-26-000021", "0001-25-000023"]
    assert [r.fiscal_year for r in refs] == [2026, 2025]
    assert refs[0].company == "NVIDIA CORP"
    assert refs[0].url == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000126000021/nvda-20260125.htm"
    )
    # EDGAR rejects requests without an identifying User-Agent.
    assert mock_get.call_args.kwargs["headers"] == {"User-Agent": "Test Suite test@example.com"}


def test_list_annual_reports_pages_back_past_recent_filings():
    # Big banks' "recent" block is all prospectuses back to the latest 10-K;
    # the one before it is only in an older page.
    columns = ("form", "accessionNumber", "filingDate", "reportDate", "primaryDocument")
    recent = dict(zip(columns, (["424B2", "10-K"], ["p1", "k26"], ["2026-03-01", "2026-02-13"],
                                ["", "2025-12-31"], ["p1.htm", "k26.htm"])))
    older = dict(zip(columns, (["424B2", "10-K", "10-K"], ["p2", "k25", "k24"],
                               ["2025-03-01", "2025-02-14", "2024-02-16"],
                               ["", "2024-12-31", "2023-12-31"], ["p2.htm", "k25.htm", "k24.htm"])))
    submissions = {"filings": {"recent": recent, "files": [{"name": "CIK0001045810-submissions-001.json"}]}}
    with patch(
        "app.sec_filings.requests.get",
        side_effect=[_json_response(_TICKERS), _json_response(submissions), _json_response(older)],
    ) as mock_get:
        refs = list_annual_reports("NVDA", limit=2)

    assert [r.accession for r in refs] == ["k26", "k25"]
    assert mock_get.call_args.args[0] == "https://data.sec.gov/submissions/CIK0001045810-submissions-001.json"


def test_list_annual_reports_rejects_unknown_ticker():
    with patch("app.sec_filings.requests.get", return_value=_json_response(_TICKERS)):
        with pytest.raises(FilingUnavailable, match="ZZZZ"):
            list_annual_reports("ZZZZ", limit=2)


def test_requests_refused_without_user_agent():
    with patch("app.sec_filings.settings.sec_user_agent", ""), patch(
        "app.sec_filings.requests.get"
    ) as mock_get:
        with pytest.raises(FilingUnavailable, match="SEC_USER_AGENT"):
            list_annual_reports("NVDA", limit=2)
    mock_get.assert_not_called()


def test_html_to_text_skips_hidden_xbrl_and_flattens_tables():
    html = """
    <html><head><title>nvda-20260125</title></head><body>
    <div style="display: none"><ix:header><ix:hidden>dei:Secret 123</ix:hidden></ix:header></div>
    <div><span>Our Company</span></div>
    <p>NVIDIA&#160;pioneered accelerated computing.</p>
    <table>
      <tr><td></td><td>2025</td><td></td><td>Change</td></tr>
      <tr><td>Data Center</td><td>$</td><td>115,186</td><td>142</td><td>%</td></tr>
    </table>
    <div>42</div>
    <div>Table of Contents</div>
    <div>Apple Inc. | 2025 Form 10-K | 21</div>
    </body></html>
    """
    assert html_to_text(html) == (
        "Our Company\n"
        "NVIDIA pioneered accelerated computing.\n"
        "2025 | Change\n"
        "Data Center | $115,186 | 142%"
    )


def test_split_sections_skips_table_of_contents_and_running_headers():
    text = "\n".join(
        [
            # Table of contents: every heading, each followed immediately by the next.
            "Item 1. Business", "Item 1A. Risk Factors", "Item 7. MD&A", "Item 8. Financials",
            "Item 1. Business",
            "We make GPUs.",
            "Item 1",  # running page header, same item: not a new section
            "We also make networking.",
            "Item 1A. Risk Factors",
            "Supply could be constrained.",
            "See Item 7 for discussion.",  # cross-reference mid-line, not a heading
            "PART II",
            "Item 7. Management's Discussion",
            "Revenue grew.",
            "Item 8. Financial Statements",
            "Balance sheet.",
        ]
    )
    sections = {s.item: s for s in split_sections(text)}

    assert set(sections) == {"1", "1A", "7"}
    assert sections["1"].text == "We make GPUs.\nWe also make networking."
    assert sections["1A"].text == "Supply could be constrained.\nSee Item 7 for discussion."
    assert sections["7"].text == "Revenue grew."
    assert sections["1A"].title == "Item 1A. Risk Factors"
