"""
Retrieval-augmented search over a company's 10-K filings.

Why retrieval rather than pasting the filing into the prompt: one 10-K's
kept sections (see app/sec_filings.py) run 15-35k words, and the agent
compares across years, so even one company is 100k+ tokens of context per
question. Retrieval sends the model ~5 relevant excerpts instead, each
carrying a citation back to the filing and section it came from.

Pipeline, per company, the first time anyone asks about it:
  EDGAR 10-K HTML -> Items 1/1A/7/7A text -> ~250-word chunks on paragraph
  boundaries (LangChain's RecursiveCharacterTextSplitter) -> embeddings,
  stored with the text in a MongoDB Atlas collection that has both an
  Atlas Search (BM25) index and a Vector Search index.
Then per question Atlas runs a hybrid search within the company/year/
section filter: BM25 over the text and vector search over the embeddings,
merged by its $rankFusion stage (reciprocal rank fusion).

Design choices:

- MongoDB Atlas rather than a database file on the VM: Fly's disk is wiped
  on every restart and redeploy, so a local index re-downloads every
  company from EDGAR after each one. Atlas was picked over other hosted
  stores because hybrid search is built in (BM25, vector search, metadata
  prefilters and RRF all run in the database, so no ranking code lives
  here), and because the same cluster can later hold LangGraph's chat
  checkpoints too, without adding a second service.
- One shared `chunks` collection, with a `variant` field (embedding model
  + chunk size) that every query filters on, instead of a collection per
  model or chunk size: the free M0 tier allows only 3 search indexes per
  cluster, and this design needs 2. The filter still keeps vectors from two
  embedding spaces, or two chunkings used by evals/, from ever being
  compared. The vector index's dimension is fixed, so switching to a model
  of another size means dropping the `chunks_vector` index first.
- Atlas Search indexes catch up with writes asynchronously (about a
  second), so after ingesting a filing _index_filing waits until both
  indexes see its chunks; otherwise the first question about a new company
  would search an index that doesn't have it yet.
- Embeddings are model2vec static embeddings (potion-retrieval-32M, ~65MB
  at float16, numpy only), computed locally. Hosted embedding APIs were the
  alternative, but free tiers cap throughput (Gemini's: 30k tokens/minute),
  which turns indexing one 10-K on demand into minutes of waiting;
  sentence-transformer models need torch, which alone outweighs the VM.
  Static embeddings are weak at paraphrase, which is why BM25 sits beside
  them: filings are full of exact terms (product names, "Taiwan", "export
  controls") that keyword search nails. evals/ measures each mode
  separately so that trade-off is a number, not a claim.
"""
from dataclasses import dataclass
import time
from threading import Lock
from typing import Any, Literal, Protocol

from bson.binary import Binary, BinaryVectorDtype
from langchain_text_splitters import RecursiveCharacterTextSplitter
import numpy as np
from pymongo import ASCENDING, MongoClient
from pymongo.database import Database
from pymongo.errors import PyMongoError
from pymongo.operations import SearchIndexModel

from app.config import get_embedder, settings
from app.sec_filings import FilingRef, FilingUnavailable, fetch_sections, list_annual_reports

SECTION_ITEMS = {
    "business": "1",
    "risk_factors": "1A",
    "mdna": "7",
    "market_risk": "7A",
}
SectionName = Literal["business", "risk_factors", "mdna", "market_risk"]
RetrievalMode = Literal["hybrid", "bm25", "dense"]

# How often a company's filing list is re-checked for a new 10-K. Filings are
# annual, so a day is plenty fresh and keeps repeat questions off EDGAR.
_RECHECK_SECONDS = 24 * 3600
# Each ranker's candidate depth before fusion.
_CANDIDATES = 30
# How long to wait for Atlas's search indexes: to become queryable when
# first created, and to catch up after a filing is inserted.
_INDEX_READY_SECONDS = 120
_INDEX_CATCHUP_SECONDS = 30

_TEXT_INDEX = "chunks_text"
_VECTOR_INDEX = "chunks_vector"
# Fields queries filter on, so both search indexes must declare them.
_FILTER_FIELDS = ["variant", "symbol", "fiscal_year", "section_item", "accession"]

_HIT_FIELDS = ["symbol", "company", "fiscal_year", "filing_date", "url", "section_title", "text"]


class Embedder(Protocol):
    def encode(self, texts: list[str]) -> np.ndarray: ...


@dataclass(frozen=True)
class Hit:
    symbol: str
    company: str
    fiscal_year: int
    filing_date: str
    url: str
    section_title: str
    text: str
    score: float

    @property
    def citation(self) -> str:
        return (
            f"{self.symbol} 10-K FY{self.fiscal_year} · {self.section_title} · "
            f"filed {self.filing_date}"
        )


def chunk_text(text: str, target_words: int, overlap_words: int) -> list[str]:
    """Pack paragraphs (lines) into chunks of about `target_words`, splitting
    a paragraph on spaces only if it alone exceeds the target, with about
    `overlap_words` of trailing paragraphs repeated at the start of the next
    chunk so a sub-heading isn't stranded on the wrong side of a boundary."""
    splitter = RecursiveCharacterTextSplitter(
        separators=["\n", " "],
        chunk_size=target_words,
        chunk_overlap=overlap_words,
        length_function=lambda s: len(s.split()),
    )
    return splitter.split_text(text)


def _text_index_definition() -> dict:
    fields: dict[str, Any] = {"text": {"type": "string", "analyzer": "lucene.english"}}
    for name in _FILTER_FIELDS:
        fields[name] = {"type": "number"} if name == "fiscal_year" else {"type": "token"}
    return {"mappings": {"dynamic": False, "fields": fields}}


def _vector_index_definition(dim: int) -> dict:
    return {
        "fields": [
            {"type": "vector", "path": "vector", "numDimensions": dim, "similarity": "cosine"},
            *({"type": "filter", "path": name} for name in _FILTER_FIELDS),
        ]
    }


def _pipeline(
    mode: RetrievalMode,
    query: str,
    vector: list[float] | None,
    filters: dict[str, Any],
    k: int,
) -> list[dict]:
    """The aggregation pipeline for one search. Filter values are passed as
    BSON values, never spliced into a query string, since the symbol comes
    from the LLM; Atlas's `text` operator doesn't parse query syntax either,
    so quotes or NOT in the question are just words."""

    def text_stages(limit: int) -> list[dict]:
        return [
            {
                "$search": {
                    "index": _TEXT_INDEX,
                    "compound": {
                        "must": [{"text": {"query": query, "path": "text"}}],
                        "filter": [
                            {"equals": {"path": path, "value": value}}
                            for path, value in filters.items()
                        ],
                    },
                }
            },
            {"$limit": limit},
        ]

    def vector_stages(limit: int) -> list[dict]:
        return [
            {
                "$vectorSearch": {
                    "index": _VECTOR_INDEX,
                    "path": "vector",
                    "queryVector": vector,
                    "numCandidates": limit * 10,
                    "limit": limit,
                    "filter": {"$and": [{p: {"$eq": v}} for p, v in filters.items()]},
                }
            }
        ]

    if mode == "bm25":
        stages, score = text_stages(k), "searchScore"
    elif mode == "dense":
        stages, score = vector_stages(k), "vectorSearchScore"
    else:
        stages = [
            {
                "$rankFusion": {
                    "input": {
                        "pipelines": {
                            "vector": vector_stages(_CANDIDATES),
                            "text": text_stages(_CANDIDATES),
                        }
                    }
                }
            },
            {"$limit": k},
        ]
        score = "score"
    return stages + [
        {"$project": {"_id": 0, **{f: 1 for f in _HIT_FIELDS}, "score": {"$meta": score}}}
    ]


class FilingIndex:
    """The Atlas collections plus the operations on them. One shared
    instance (see get_filing_index) serves the app; tests and evals/ build
    their own against another database, a fake embedder, or different chunk
    sizes."""

    def __init__(
        self,
        db: Database,
        embedder: Embedder,
        *,
        model: str,
        chunk_words: int = 250,
        overlap_words: int = 40,
        filings_per_company: int = 2,
    ) -> None:
        self.embedder = embedder
        self.chunk_words = chunk_words
        self.overlap_words = overlap_words
        self.filings_per_company = filings_per_company
        # Chunks from another embedding model or chunk size share the
        # collection but are never searched together (see module docstring).
        self.variant = f"{model.rsplit('/', 1)[-1]}-{chunk_words}w"
        # One ingest at a time: two first-time questions about the same
        # company would otherwise both download and index it.
        self._ingest_lock = Lock()

        dim = np.asarray(embedder.encode(["dimension probe"])).shape[1]
        self._companies = db["companies"]
        if "chunks" not in db.list_collection_names():
            # Search indexes can only be created on an existing collection.
            db.create_collection("chunks")
        self._chunks = db["chunks"]
        self._chunks.create_index([("variant", ASCENDING), ("symbol", ASCENDING), ("fiscal_year", ASCENDING)])
        self._chunks.create_index([("variant", ASCENDING), ("accession", ASCENDING)])
        self._ensure_search_indexes(dim)

    def _ensure_search_indexes(self, dim: int) -> None:
        existing = {i["name"]: i for i in self._chunks.list_search_indexes()}
        if _TEXT_INDEX not in existing:
            self._chunks.create_search_index(
                SearchIndexModel(_text_index_definition(), name=_TEXT_INDEX, type="search")
            )
        if _VECTOR_INDEX not in existing:
            self._chunks.create_search_index(
                SearchIndexModel(_vector_index_definition(dim), name=_VECTOR_INDEX, type="vectorSearch")
            )
        else:
            definition = existing[_VECTOR_INDEX].get("latestDefinition", {})
            dims = [f.get("numDimensions") for f in definition.get("fields", []) if f.get("type") == "vector"]
            if dims and dims[0] != dim:
                raise ValueError(
                    f"The {_VECTOR_INDEX} Atlas index holds {dims[0]}-dim vectors but the "
                    f"embedding model produces {dim}; drop {_VECTOR_INDEX} to switch models."
                )

        deadline = time.time() + _INDEX_READY_SECONDS
        while time.time() < deadline:
            indexes = list(self._chunks.list_search_indexes())
            if all(i.get("queryable") for i in indexes if i["name"] in (_TEXT_INDEX, _VECTOR_INDEX)):
                return
            time.sleep(1)

    def ensure_indexed(self, symbol: str) -> None:
        """Index any of the company's recent 10-Ks not already in the DB.
        Raises FilingUnavailable only if nothing is indexed for the company
        afterwards — if EDGAR is down but last week's index exists, that
        index is still served, and one filing that won't parse doesn't stop
        the others being indexed."""
        company_id = f"{self.variant}/{symbol}"
        with self._ingest_lock:
            row = self._companies.find_one({"_id": company_id})
            if row and time.time() - row["checked_at"] < _RECHECK_SECONDS:
                return

            error: FilingUnavailable | None = None
            try:
                refs = list_annual_reports(symbol, self.filings_per_company)
            except FilingUnavailable as exc:
                refs, error = [], exc
            for ref in refs:
                try:
                    self._index_filing(ref)
                except FilingUnavailable as exc:
                    error = error or exc

            if refs:
                # Recorded even if a filing failed to parse: that failure is
                # deterministic, so retrying it on every question just
                # re-downloads the same HTML. A failed EDGAR lookup (no refs)
                # isn't recorded, so the next question retries it.
                self._companies.update_one(
                    {"_id": company_id}, {"$set": {"checked_at": time.time()}}, upsert=True
                )
            if error and not self.indexed_years(symbol):
                raise error

    def _index_filing(self, ref: FilingRef) -> None:
        if self._chunks.count_documents({"variant": self.variant, "accession": ref.accession}, limit=1):
            return

        rows = []
        for section in fetch_sections(ref):
            for text in chunk_text(section.text, self.chunk_words, self.overlap_words):
                rows.append((section.item, section.title, text))
        if not rows:
            raise FilingUnavailable(
                f"Couldn't find Items 1/1A/7/7A in {ref.symbol}'s FY{ref.fiscal_year} 10-K"
            )

        # The header is embedded with the chunk but not stored in it: it
        # tells the vector which company/year/section a fragment like "our
        # supply constraints worsened" belongs to, while the stored text
        # stays a verbatim excerpt for citation.
        header = f"{ref.company} ({ref.symbol}) 10-K fiscal {ref.fiscal_year}"
        vectors = self.embedder.encode([f"{header}, {t}\n{x}" for _, t, x in rows])

        self._chunks.insert_many(
            [
                {
                    "variant": self.variant,
                    "accession": ref.accession,
                    "symbol": ref.symbol,
                    "company": ref.company,
                    "fiscal_year": ref.fiscal_year,
                    "filing_date": ref.filing_date,
                    "url": ref.url,
                    "section_item": item,
                    "section_title": title,
                    "text": text,
                    # float32 BSON vector: half the storage of a list of doubles.
                    "vector": Binary.from_vector(
                        np.asarray(vector, dtype=np.float32).tolist(), BinaryVectorDtype.FLOAT32
                    ),
                }
                for (item, title, text), vector in zip(rows, vectors)
            ]
        )
        self._wait_until_searchable(ref.accession, len(rows), vectors[0])

    def _wait_until_searchable(self, accession: str, expected: int, probe: np.ndarray) -> None:
        """Block until both search indexes return all of a filing's chunks,
        or give up after _INDEX_CATCHUP_SECONDS (searches then just miss
        some chunks until Atlas catches up)."""
        filters = {"variant": self.variant, "accession": accession}
        text_count = [
            {
                "$searchMeta": {
                    "index": _TEXT_INDEX,
                    "compound": {
                        "filter": [{"equals": {"path": p, "value": v}} for p, v in filters.items()]
                    },
                    "count": {"type": "total"},
                }
            }
        ]
        vector_count = [
            {
                "$vectorSearch": {
                    "index": _VECTOR_INDEX,
                    "path": "vector",
                    "queryVector": np.asarray(probe, dtype=np.float32).tolist(),
                    "exact": True,
                    "limit": expected,
                    "filter": {"$and": [{p: {"$eq": v}} for p, v in filters.items()]},
                }
            },
            {"$count": "n"},
        ]
        deadline = time.time() + _INDEX_CATCHUP_SECONDS
        while time.time() < deadline:
            texts = list(self._chunks.aggregate(text_count))
            vecs = list(self._chunks.aggregate(vector_count))
            if (
                texts and texts[0]["count"]["total"] >= expected
                and vecs and vecs[0]["n"] >= expected
            ):
                return
            time.sleep(0.5)

    def indexed_years(self, symbol: str) -> list[int]:
        years = self._chunks.distinct("fiscal_year", {"variant": self.variant, "symbol": symbol})
        return sorted(years, reverse=True)

    def retrieve(
        self,
        symbol: str,
        query: str,
        *,
        fiscal_year: int | None = None,
        section: SectionName | None = None,
        k: int = 5,
        mode: RetrievalMode = "hybrid",
    ) -> list[Hit]:
        """Top-k chunks for `query` within one company's indexed filings,
        optionally narrowed to a fiscal year and/or section. Doesn't ingest;
        call ensure_indexed first."""
        if not query.strip():
            return []
        filters: dict[str, Any] = {"variant": self.variant, "symbol": symbol}
        if fiscal_year is not None:
            filters["fiscal_year"] = int(fiscal_year)
        if section is not None:
            filters["section_item"] = SECTION_ITEMS[section]

        vector = None
        if mode != "bm25":
            vector = np.asarray(self.embedder.encode([query])[0], dtype=np.float32).tolist()
        rows = self._chunks.aggregate(_pipeline(mode, query, vector, filters, k))
        return [Hit(**{f: r[f] for f in _HIT_FIELDS}, score=r["score"]) for r in rows]


_client: MongoClient | None = None
_index: FilingIndex | None = None
_index_lock = Lock()


def get_database() -> Database:
    """The configured Atlas database, sharing one client per process."""
    global _client
    if not settings.mongodb_uri:
        raise FilingUnavailable("10-K search isn't configured (MONGODB_URI is not set)")
    if _client is None:
        _client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
    return _client[settings.mongodb_db]


def get_filing_index() -> FilingIndex:
    """The app's shared index, created on first use so importing this module
    (and app.tools) doesn't load the embedding model or connect to Atlas."""
    global _index
    with _index_lock:
        if _index is None:
            _index = FilingIndex(get_database(), get_embedder(), model=settings.embedding_model)
        return _index


def search_filings(
    symbol: str,
    query: str,
    *,
    fiscal_year: int | None = None,
    section: SectionName | None = None,
) -> str:
    """Ingest the company on first use, retrieve, and format numbered
    excerpts for the model. Like app/market_data.py's fetchers, never raises:
    failures come back as a bracketed explanation the model can relay."""
    symbol = symbol.strip().upper()
    try:
        index = get_filing_index()
        index.ensure_indexed(symbol)

        years = index.indexed_years(symbol)
        # Without a year, both filings' excerpts came back interleaved, and the
        # model presented them all as "the latest 10-K". Defaulting to the newest
        # keeps every answer to one filing; comparisons search each year anyway.
        if fiscal_year is None:
            fiscal_year = years[0]
        elif fiscal_year not in years:
            return (
                f"[no FY{fiscal_year} 10-K indexed for {symbol}; indexed fiscal "
                f"years: {', '.join(map(str, years))}]"
            )

        hits = index.retrieve(symbol, query, fiscal_year=fiscal_year, section=section)
    except FilingUnavailable as exc:
        return f"[filings unavailable: {exc}]"
    except PyMongoError:
        return "[filings unavailable: the filings database is unreachable]"
    return format_hits(symbol, hits, years)


def format_hits(symbol: str, hits: list[Hit], years: list[int]) -> str:
    """Numbered excerpts as the model sees them. Shared with evals/ so the
    eval grades answers generated from exactly what the agent would get."""
    if not hits:
        return f"[no matching excerpts in {symbol}'s 10-K filings]"
    header = (
        f"Excerpts from {hits[0].company} ({symbol}) 10-K filings, most "
        f"relevant first (indexed fiscal years: {', '.join(map(str, years))}). "
        f"Cite them by number."
    )
    blocks = [
        f"[{n}] {hit.citation} · {hit.url}\n{hit.text}" for n, hit in enumerate(hits, start=1)
    ]
    return header + "\n\n" + "\n\n".join(blocks)
