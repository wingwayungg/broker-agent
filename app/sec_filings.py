"""
SEC EDGAR client for app/rag.py: find a company's recent 10-K filings and
turn each one into plain-text sections ready for chunking.

Same reasoning as app/market_data.py for staying on plain `requests`: EDGAR
is three public JSON/HTML endpoints with no auth, and the third-party EDGAR
SDKs (edgartools, sec-api) either pull in pandas/lxml-sized dependency trees
or are paid APIs — neither fits the 512MB Fly VM for what is a ticker->CIK
lookup, a filing index read, and one HTML download per filing.

EDGAR's fair-access policy requires a User-Agent naming who is calling with
a contact address, and rejects generic ones with a 403. That's SEC_USER_AGENT
in .env (e.g. "Your Name you@example.com"); without it every fetch here
raises FilingUnavailable rather than getting the app's IP flagged.

Only Items 1, 1A, 7, and 7A are kept. That's where the prose a reader asks
questions about lives (business description, risk factors, management's
discussion, market risk); Item 8's financial statements are mostly XBRL
tables that chunk badly and would roughly double the embedding work per
filing for content that structured data APIs serve better anyway.
"""
from dataclasses import dataclass
from html.parser import HTMLParser
import re
from threading import Lock

import requests

from app.config import settings

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

# Item id -> human label used in citations. Order is the order they appear
# in a 10-K, which is also the order sections are returned in.
SECTIONS = {
    "1": "Item 1. Business",
    "1A": "Item 1A. Risk Factors",
    "7": "Item 7. Management's Discussion and Analysis",
    "7A": "Item 7A. Market Risk",
}


class FilingUnavailable(Exception):
    """Raised for anything that stops a filing being fetched: missing
    SEC_USER_AGENT, unknown ticker, no 10-K on file, or a network error.
    The message is shown to the model as-is, so keep it readable."""


@dataclass(frozen=True)
class FilingRef:
    symbol: str
    company: str
    cik: int
    accession: str
    form: str
    filing_date: str
    report_date: str
    url: str

    @property
    def fiscal_year(self) -> int:
        # The period-of-report year matches how companies label their own
        # fiscal year: NVIDIA's year ending 2026-01-25 is "fiscal 2026",
        # Apple's ending 2025-09-27 is "fiscal 2025".
        return int(self.report_date[:4])


@dataclass(frozen=True)
class Section:
    item: str
    title: str
    text: str


def _get(url: str) -> requests.Response:
    if not settings.sec_user_agent:
        raise FilingUnavailable("SEC filings unavailable: no SEC_USER_AGENT configured")
    try:
        resp = requests.get(url, headers={"User-Agent": settings.sec_user_agent}, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise FilingUnavailable(f"SEC EDGAR request failed: {exc}") from exc
    return resp


# Ticker -> (cik, company title). The full map is ~10k entries (~1MB JSON)
# and changes rarely, so it's fetched once per process and only on success.
_ticker_map: dict[str, tuple[int, str]] = {}
_ticker_map_lock = Lock()


def _lookup_cik(symbol: str) -> tuple[int, str]:
    with _ticker_map_lock:
        if not _ticker_map:
            for row in _get(_TICKERS_URL).json().values():
                _ticker_map[row["ticker"].upper()] = (int(row["cik_str"]), row["title"])
        entry = _ticker_map.get(symbol)
    if entry is None:
        raise FilingUnavailable(
            f"{symbol} isn't in SEC EDGAR's ticker list — only US-listed "
            f"companies that file with the SEC have 10-Ks"
        )
    return entry


def list_annual_reports(symbol: str, limit: int) -> list[FilingRef]:
    """The `limit` most recent 10-K filings for a ticker, newest first."""
    symbol = symbol.strip().upper()
    cik, company = _lookup_cik(symbol)
    filings = _get(_SUBMISSIONS_URL.format(cik=cik)).json()["filings"]

    def pages():
        # "recent" holds the latest ~1000 filings, or a year of them, whichever
        # is more; older ones are in paged files, newest first. Big banks file
        # thousands of 424B2 prospectuses a year, so their previous 10-K is
        # only in those pages.
        yield filings["recent"]
        for page in filings.get("files", []):
            yield _get(_SUBMISSIONS_PAGE_URL.format(name=page["name"])).json()

    refs = []
    for page in pages():
        for i, form in enumerate(page["form"]):
            # 10-K/A amendments are usually a cover page plus Part III and would
            # shadow the real filing for that year, so only originals count.
            if form != "10-K":
                continue
            accession = page["accessionNumber"][i]
            refs.append(
                FilingRef(
                    symbol=symbol,
                    company=company,
                    cik=cik,
                    accession=accession,
                    form=form,
                    filing_date=page["filingDate"][i],
                    report_date=page["reportDate"][i],
                    url=_ARCHIVE_URL.format(
                        cik=cik,
                        accession=accession.replace("-", ""),
                        document=page["primaryDocument"][i],
                    ),
                )
            )
            if len(refs) == limit:
                break
        if len(refs) == limit:
            break
    if not refs:
        raise FilingUnavailable(f"No 10-K filings found on EDGAR for {symbol}")
    return refs


def fetch_sections(ref: FilingRef) -> list[Section]:
    """Download one filing and return its kept sections (see SECTIONS)."""
    return split_sections(html_to_text(_get(ref.url).text))


_BLOCK_TAGS = {
    "p", "div", "br", "tr", "li", "table", "section",
    "h1", "h2", "h3", "h4", "h5", "h6",
}
_SKIP_TAGS = {"script", "style", "head", "title", "ix:header"}


class _TextExtractor(HTMLParser):
    """Inline-XBRL 10-K HTML -> lines of text. Each block element becomes a
    line break and table cells are joined with " | " so a table row stays
    one readable line. Skips <ix:header> (the hidden XBRL fact block, which
    is thousands of machine-readable values) and anything styled
    display:none, which is how filers hide the same kind of data."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_tag: str | None = None
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if self._skip_tag:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        style = (dict(attrs).get("style") or "").replace(" ", "").lower()
        if tag in _SKIP_TAGS or "display:none" in style:
            self._skip_tag, self._skip_depth = tag, 1
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self._skip_tag:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth == 0:
                    self._skip_tag = None
            return
        if tag in ("td", "th"):
            self.parts.append(" | ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_tag:
            self.parts.append(data)


# Bare page numbers, "Table of Contents" back-links, and running footers
# like "Apple Inc. | 2025 Form 10-K | 21".
_PAGE_FURNITURE = re.compile(
    r"^(\d{1,3}|table of contents|.{0,40}form 10-k \| \d{1,3})$", re.IGNORECASE
)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    lines = []
    for raw in "".join(parser.parts).split("\n"):
        line = re.sub(r"\s+", " ", raw.replace("\xa0", " ")).strip()
        # Rows of a table with empty cells come out as "| | $ | 1,234 |".
        line = re.sub(r"(\|\s*)+\|", "|", line).strip(" |")
        # Filers put "$" and "%" in their own cells: "$ | 178,353 | 7 | %".
        line = line.replace("$ | ", "$").replace(" | %", "%")
        if line and not _PAGE_FURNITURE.match(line):
            lines.append(line)
    return "\n".join(lines)


# "Item 1A." / "ITEM 7 -" / "Item 7A:" at the start of a line. Requiring the
# line start keeps in-text cross-references ("see Item 7 of this report")
# from being read as headings.
_ITEM_HEADING = re.compile(r"^item\s+(\d{1,2}[a-c]?)\b\.?", re.IGNORECASE | re.MULTILINE)
# A line that is nothing but "Item 1A" / "Item 2, 3, 4" / "PART II": the
# running page header some filers (Microsoft) repeat on every page.
_RUNNING_HEADER = re.compile(r"^(item\s+[\dA-C, ]+\.?|part\s+[IV]+)$", re.IGNORECASE)


def split_sections(text: str) -> list[Section]:
    """Find each kept Item's body in a 10-K's text.

    Every Item heading appears at least twice — once in the table of
    contents, once at the real section — and the ToC copy is followed almost
    immediately by the next Item's heading. Some filers also repeat the
    current Item as a running header on every page. So consecutive headings
    for the same Item are merged into one run, and among an Item's runs the
    real section is the one with the most text before a different Item's
    heading starts."""
    runs: list[tuple[int, str]] = []
    for m in _ITEM_HEADING.finditer(text):
        item = m.group(1).upper()
        if not runs or runs[-1][1] != item:
            runs.append((m.start(), item))

    best: dict[str, tuple[int, int]] = {}
    for (start, item), (next_start, _) in zip(runs, runs[1:] + [(len(text), "")]):
        if item not in SECTIONS:
            continue
        prev = best.get(item)
        if prev is None or next_start - start > prev[1] - prev[0]:
            best[item] = (start, next_start)

    sections = []
    for item, title in SECTIONS.items():
        if item not in best:
            continue
        start, end = best[item]
        # Drop the heading line itself (the title travels as metadata) and
        # any running headers inside the body.
        lines = text[start:end].split("\n")[1:]
        body = "\n".join(line for line in lines if not _RUNNING_HEADER.match(line)).strip()
        if body:
            sections.append(Section(item=item, title=title, text=body))
    return sections
