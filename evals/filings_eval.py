"""
Evaluate app/rag.py's 10-K search against filings_golden.jsonl.

    python -m evals.filings_eval                    # retrieval only, all modes
    python -m evals.filings_eval --judge            # + LLM-judged answers
    python -m evals.filings_eval --chunk-words 150  # try another chunk size

Two layers, scored separately because they fail separately:

1. Retrieval (no LLM, seconds): for each question, is a chunk containing
   one of its `evidence` strings in the top k? Reported per mode — bm25,
   dense, hybrid — as hit@1, hit@k, and MRR, so the claim "hybrid beats
   either alone" is checked rather than assumed.
2. Generation (--judge, uses the configured LLM): answer each question from
   the top-k excerpts with the same rules the agent's system prompt gives,
   then a second LLM call grades the answer for faithfulness (every claim
   supported by the excerpts), correctness (agrees with the reference
   answer), and — for the questions the filings can't answer — whether it
   declined instead of guessing.

This hits SEC EDGAR (needs SEC_USER_AGENT) on the first run to index the
companies; later runs reuse the index in the MongoDB Atlas database
(MONGODB_URI). Chunks are fixed at ingest time, so each chunk size is
indexed separately, under its own `variant` in the shared collection (see
app/rag.py). Not part of pytest — it needs the network and, with --judge,
LLM quota.
"""
import argparse
import json
from pathlib import Path
import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.config import get_embedder, get_llm, settings
from app.rag import FilingIndex, Hit, format_hits, get_database

_GOLDEN = Path(__file__).with_name("filings_golden.jsonl")

_ANSWER_PROMPT = (
    "Answer the question using only the numbered 10-K excerpts provided. "
    "Cite each claim with the excerpt number in brackets, e.g. [2]. If the "
    "excerpts don't contain the answer, say that the filings don't cover it "
    "instead of answering from memory. Be brief."
)

_JUDGE_PROMPT = (
    "You grade answers produced by a retrieval-augmented system. You get "
    "the excerpts the system saw, the question, the system's answer, and a "
    "reference answer (or null when the excerpts are not expected to "
    "contain one). Reply with only a JSON object:\n"
    '{"faithful": bool, "correct": bool, "abstained": bool, "reason": str}\n'
    "faithful: every factual claim in the answer is supported by the "
    "excerpts (an answer that only says the information isn't available is "
    "faithful). correct: the answer agrees with the reference answer's key "
    "facts; when the reference is null, correct means the answer declined. "
    "abstained: the answer says the excerpts/filings don't contain the "
    "answer instead of giving one."
)


def _relevant(hit: Hit, evidence: list[str]) -> bool:
    text = hit.text.lower()
    return any(e.lower() in text for e in evidence)


def _retrieval_scores(index: FilingIndex, cases: list[dict], mode: str, k: int) -> dict:
    answerable = [c for c in cases if c["evidence"]]
    hit1 = hitk = rr = 0.0
    misses = []
    for case in answerable:
        hits = index.retrieve(
            case["symbol"], case["question"], fiscal_year=case["fiscal_year"], k=k, mode=mode
        )
        ranks = [i for i, h in enumerate(hits, start=1) if _relevant(h, case["evidence"])]
        if ranks:
            hit1 += ranks[0] == 1
            hitk += 1
            rr += 1 / ranks[0]
        else:
            misses.append(case["id"])
    n = len(answerable)
    return {"hit@1": hit1 / n, f"hit@{k}": hitk / n, "mrr": rr / n, "misses": misses}


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(match.group(0)) if match else {}


def _judge(index: FilingIndex, cases: list[dict], mode: str, k: int) -> list[dict]:
    llm = get_llm()
    results = []
    for case in cases:
        symbol = case["symbol"]
        hits = index.retrieve(
            symbol, case["question"], fiscal_year=case["fiscal_year"], k=k, mode=mode
        )
        context = format_hits(symbol, hits, index.indexed_years(symbol))
        answer = llm.invoke(
            [
                SystemMessage(content=_ANSWER_PROMPT),
                HumanMessage(content=f"{context}\n\nQuestion: {case['question']}"),
            ]
        ).content
        verdict = _parse_json(
            llm.invoke(
                [
                    SystemMessage(content=_JUDGE_PROMPT),
                    HumanMessage(
                        content=(
                            f"Excerpts:\n{context}\n\nQuestion: {case['question']}\n\n"
                            f"Answer: {answer}\n\n"
                            f"Reference answer: {json.dumps(case['answer'])}"
                        )
                    ),
                ]
            ).content
        )
        results.append({"id": case["id"], "answer": answer, **verdict})
        print(
            f"  {case['id']:<26} faithful={verdict.get('faithful')!s:<5} "
            f"correct={verdict.get('correct')!s:<5} {verdict.get('reason', '')[:70]}"
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--modes", nargs="+", default=["bm25", "dense", "hybrid"])
    parser.add_argument("--chunk-words", type=int, default=250)
    parser.add_argument("--overlap-words", type=int, default=40)
    parser.add_argument("--judge", action="store_true", help="also grade generated answers with the LLM")
    parser.add_argument("--judge-mode", default="hybrid", help="retrieval mode used for --judge")
    args = parser.parse_args()

    cases = [json.loads(line) for line in _GOLDEN.read_text().splitlines() if line.strip()]
    index = FilingIndex(
        get_database(),
        get_embedder(),
        model=settings.embedding_model,
        chunk_words=args.chunk_words,
        overlap_words=args.overlap_words,
    )
    for symbol in sorted({c["symbol"] for c in cases}):
        index.ensure_indexed(symbol)

    n = sum(1 for c in cases if c["evidence"])
    print(f"Retrieval — {n} answerable questions, k={args.k}, "
          f"{args.chunk_words}-word chunks, {settings.embedding_model}")
    print(f"  {'mode':<8} {'hit@1':>6} {f'hit@{args.k}':>6} {'mrr':>6}  misses")
    for mode in args.modes:
        s = _retrieval_scores(index, cases, mode, args.k)
        print(f"  {mode:<8} {s['hit@1']:>6.2f} {s[f'hit@{args.k}']:>6.2f} {s['mrr']:>6.2f}  "
              f"{', '.join(s['misses']) or '-'}")

    if args.judge:
        print(f"\nGeneration — {len(cases)} questions, {args.judge_mode} retrieval, "
              f"LLM_PROVIDER={settings.llm_provider}")
        results = _judge(index, cases, args.judge_mode, args.k)
        unanswerable = {c["id"] for c in cases if not c["evidence"]}
        answered = [r for r in results if r["id"] not in unanswerable]
        declined = [r for r in results if r["id"] in unanswerable]
        print(f"  faithfulness       {sum(bool(r.get('faithful')) for r in results)}/{len(results)}")
        print(f"  correctness        {sum(bool(r.get('correct')) for r in answered)}/{len(answered)}")
        print(f"  abstained when due {sum(bool(r.get('abstained')) for r in declined)}/{len(declined)}")


if __name__ == "__main__":
    main()
