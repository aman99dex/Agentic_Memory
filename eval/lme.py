"""LongMemEval (_s) and LoCoMo harness: the same questions through three systems, then judged.

    uv run --env-file .env python -m eval.lme --n 12 --seed 0 --name pilot
    ... --dataset locomo                   # LoCoMo instead (ADR 0017)
    ... --retriever vector | --no-history | --no-rerank   # one ablation each (ADR 0015, 0016)

Systems (ADR 0008): `full` stuffs the whole history in the prompt, `rag` is chunked vector
search, `ours` is this project's write path + recall. All three answer with the same model and
the same token budget (full excepted). Results go to eval/results/<name>.json; nothing is typed
by hand.
"""

import argparse
import asyncio
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from langchain.chat_models import init_chat_model
from pgvector.psycopg import register_vector_async
from psycopg_pool import AsyncConnectionPool

from agentic_memory.embedder import Embedder
from agentic_memory.rank import Rank
from agentic_memory.read_path import _tokens, recall
from agentic_memory.store import MemoryStore, Retriever
from agentic_memory.write_path import (
    EXTRACT_PROMPT,
    RECONCILE_PROMPT,
    Facts,
    build_graph,
    extract_prompt,
    ingest_turn,
)
from eval import locomo
from eval.judge import anscheck_prompt, is_correct, locomo_prompt
from eval.llm_cache import CachedLLM

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "longmemeval_s_cleaned.json"
CACHE = HERE / "cache"
RESULTS = HERE / "results"
SYSTEMS = ("full", "rag", "ours")

ANSWER_PROMPT = """I will give you notes from earlier chats between you and a user, then a question.
Answer the question using only those notes. If they do not contain the information, say so.
Think step by step: first pull out the relevant information, then work out the answer.

Notes:
{context}

Current date: {date}
Question: {question}
Answer:"""


def parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y/%m/%d (%a) %H:%M").replace(tzinfo=UTC)


@dataclass(frozen=True)
class Round:
    session_id: str
    at: datetime
    user: str
    assistant: str

    @property
    def text(self) -> str:
        return f"[{self.at:%Y-%m-%d}] user: {self.user}\nassistant: {self.assistant}"


def rounds_of(q: dict[str, Any]) -> list[Round]:
    """Every user message (with the assistant reply that follows) in time order."""
    out: list[Round] = []
    order = sorted(range(len(q["haystack_sessions"])), key=lambda i: q["haystack_dates"][i])
    for i in order:
        msgs, start = q["haystack_sessions"][i], parse_date(q["haystack_dates"][i])
        j = 0
        while j < len(msgs):
            if msgs[j]["role"] != "user":
                j += 1
                continue
            reply = (
                msgs[j + 1] if j + 1 < len(msgs) and msgs[j + 1]["role"] == "assistant" else None
            )
            at = start + timedelta(seconds=len(out))  # distinct times keep rounds in order
            out.append(
                Round(
                    q["haystack_session_ids"][i],
                    at,
                    msgs[j]["content"],
                    reply["content"] if reply else "",
                )
            )
            j += 2 if reply else 1
    return out


def sample(questions: list[dict[str, Any]], n: int, seed: int) -> list[dict[str, Any]]:
    """Stratified by question type, seeded, so the same (n, seed) is the same set forever."""
    if n >= len(questions):
        return questions
    rng, by_type = random.Random(seed), defaultdict(list)  # noqa: S311 (shuffling, not security)
    for q in sorted(questions, key=lambda q: q["question_id"]):
        by_type[q["question_type"]].append(q)
    for group in by_type.values():
        rng.shuffle(group)
    chosen: list[dict[str, Any]] = []
    while len(chosen) < n:
        for t in sorted(by_type):
            if by_type[t] and len(chosen) < n:
                chosen.append(by_type[t].pop())
    return chosen


@dataclass(frozen=True)
class Retrieval:
    text: str
    session_ids: list[str]
    seconds: float


class Env:
    def __init__(
        self,
        store: MemoryStore,
        embedder: Embedder,
        budget: int,
        caches: dict[str, CachedLLM],
        retriever: Retriever = "hybrid",
        keep_history: bool = True,
        rank: Rank | None = None,
    ):
        self.store, self.embedder, self.budget = store, embedder, budget
        self.llm = caches  # extract / query / answer / judge, each a CachedLLM
        self.retriever, self.keep_history, self.rank = retriever, keep_history, rank
        # Several questions can share one history (LoCoMo): ingest it once, index it once.
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._rag_vectors: dict[str, np.ndarray] = {}
        self.graph = build_graph(store, caches["extract"], caches["extract"], keep_history)
        self.ingested_file = CACHE / "ingested.json"
        # A different write path (history off) must not reuse another variant's memories.
        self.user_suffix = "" if keep_history else "-nohist"
        self.ingest_key = hashlib.sha256(
            (
                caches["extract"].name
                + EXTRACT_PROMPT
                + RECONCILE_PROMPT
                + ("" if keep_history else "no-history")
            ).encode()
        ).hexdigest()[:16]

    @property
    def ours_label(self) -> str:
        parts = (
            ([] if self.retriever == "hybrid" else [f"{self.retriever}-only"])
            + ([] if self.keep_history else ["no-history"])
            + ([] if self.rank else ["no-rerank"])
        )
        return f"ours ({', '.join(parts)})" if parts else "ours"

    def _marks(self) -> dict[str, str]:
        if self.ingested_file.exists():
            marks: dict[str, str] = json.loads(self.ingested_file.read_text())
            return marks
        return {}

    async def ensure_ingested(self, user: str, rounds: Sequence[Round]) -> float:
        """Write the whole history through the real write path, once per prompt version."""
        async with self._locks[user]:
            return await self._ingest(user, rounds)

    async def _ingest(self, user: str, rounds: Sequence[Round]) -> float:
        marks = self._marks()
        if marks.get(user) == self.ingest_key:
            return 0.0
        started = time.perf_counter()
        await self.store.forget(user)  # a partial earlier run must not leave half a history
        # Extraction ignores database state, so do all of it at once into the cache; the real
        # write path below then replays it. Reconcile and apply still run in time order.
        extractor = self.llm["extract"].with_structured_output(Facts)
        await asyncio.gather(
            *(extractor.ainvoke(extract_prompt(r.at, r.user, r.assistant)) for r in rounds)
        )
        for r in rounds:
            await ingest_turn(self.store, self.graph, user, r.session_id, r.user, r.assistant, r.at)
        marks = self._marks()  # another question may have written meanwhile
        marks[user] = self.ingest_key
        self.ingested_file.write_text(json.dumps(marks))
        return time.perf_counter() - started


def pack_text(blocks: Sequence[str], budget: int) -> str:
    used, kept = 0, []
    for b in blocks:
        if used + _tokens(b) > budget:
            break
        kept.append(b)
        used += _tokens(b)
    return "\n\n".join(kept)


async def sys_full(env: Env, q: dict[str, Any], rounds: list[Round]) -> Retrieval:
    return Retrieval("\n\n".join(r.text for r in rounds), [r.session_id for r in rounds], 0.0)


async def sys_rag(env: Env, q: dict[str, Any], rounds: list[Round]) -> Retrieval:
    # Embedding the history is the index build (a write-time cost); only the query is timed.
    history = q.get("history_id")
    vecs = env._rag_vectors.get(history) if history else None
    if vecs is None:
        vecs = np.array(await asyncio.to_thread(env.embedder.embed, [r.text for r in rounds]))
        if history:
            env._rag_vectors[history] = vecs
    started = time.perf_counter()
    qv = await asyncio.to_thread(env.embedder.embed, [q["question"]])
    ranked = np.argsort(-(vecs @ np.array(qv[0])))  # unit vectors: dot = cosine
    keep: list[int] = []
    used = 0
    for i in ranked:
        if used + _tokens(rounds[i].text) > env.budget:
            break
        keep.append(int(i))
        used += _tokens(rounds[i].text)
    return Retrieval(
        "\n\n".join(rounds[i].text for i in keep),
        [rounds[i].session_id for i in keep],
        time.perf_counter() - started,
    )


async def sys_ours(env: Env, q: dict[str, Any], rounds: list[Round]) -> Retrieval:
    user = f"{q.get('history_id', 'lme-' + q['question_id'])}{env.user_suffix}"
    await env.ensure_ingested(user, rounds)
    started = time.perf_counter()
    r = await recall(
        env.store,
        env.llm["query"],
        user,
        q["question"],
        now=parse_date(q["question_date"]),
        budget=env.budget,
        retriever=env.retriever,
        rank=env.rank,
    )
    return Retrieval(r.text, [t.session_id for t in r.turns], time.perf_counter() - started)


RUNNERS = {"full": sys_full, "rag": sys_rag, "ours": sys_ours}


async def evaluate(env: Env, q: dict[str, Any], rounds: list[Round], system: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "question_id": q["question_id"],
        "question_type": q["question_type"],
        "question": q["question"],
        "gold": str(q["answer"]),
        "system": env.ours_label if system == "ours" else system,
        "correct": None,
        "error": None,
    }
    try:
        got = await RUNNERS[system](env, q, rounds)
        answer = await env.llm["answer"].ainvoke(
            ANSWER_PROMPT.format(
                context=got.text or "(nothing)", date=q["question_date"], question=q["question"]
            )
        )
        judge_prompt = (
            locomo_prompt(q["question"], q["answer"], answer)
            if q.get("dataset") == "locomo"
            else anscheck_prompt(
                q["question_type"], q["question"], q["answer"], answer, "_abs" in q["question_id"]
            )
        )
        reply = await env.llm["judge"].ainvoke(judge_prompt)
        evidence = set(q["answer_session_ids"])
        row |= {
            "correct": is_correct(reply),
            "hypothesis": answer,
            "judge_reply": reply,
            "context_tokens": _tokens(got.text),
            "retrieval_seconds": got.seconds,
            "evidence_recall": len(evidence & set(got.session_ids)) / len(evidence)
            if evidence
            else None,
        }
    except Exception as e:  # one bad question must not sink a long run; it is counted and shown
        row["error"] = f"{type(e).__name__}: {str(e)[:300]}"
    return row


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            cwd=HERE,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["longmemeval", "locomo"], default="longmemeval")
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--name", default=datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S"))
    ap.add_argument("--systems", default=",".join(SYSTEMS))
    ap.add_argument("--budget", type=int, default=2000, help="context tokens for rag and ours")
    ap.add_argument("--retriever", choices=["hybrid", "vector", "text"], default="hybrid")
    ap.add_argument("--no-history", action="store_true", help="ablation: delete replaced facts")
    ap.add_argument("--no-rerank", action="store_true", help="ablation: plain RRF order")
    ap.add_argument("--concurrency", type=int, default=4, help="questions in flight at once")
    args = ap.parse_args()
    systems = args.systems.split(",")

    judge_model = os.environ.get("JUDGE_MODEL", "anthropic:claude-sonnet-5-5")
    models = {
        "extract": os.environ["EXTRACT_MODEL"],
        "query": os.environ["QUERY_MODEL"],
        "answer": os.environ["ANSWER_MODEL"],
        "judge": judge_model,
    }
    caches = {
        role: CachedLLM(init_chat_model(name), name, CACHE / "llm.sqlite")
        for role, name in models.items()
    }

    dataset = (
        locomo.load() if args.dataset == "locomo" else json.loads(DATA.read_text(encoding="utf-8"))
    )
    questions = sample(dataset, args.n, args.seed)
    print(f"{len(questions)} questions, systems {systems}, models {models}", file=sys.stderr)
    rows: list[dict[str, Any]] = []
    gate = asyncio.Semaphore(args.concurrency)

    async with AsyncConnectionPool(
        os.environ["DATABASE_URL"],
        configure=register_vector_async,
        open=False,
        max_size=args.concurrency * 2 + 2,
    ) as pool:
        embedder = Embedder()
        env = Env(
            MemoryStore(pool, embedder),
            embedder,
            args.budget,
            caches,
            args.retriever,
            not args.no_history,
            None if args.no_rerank else Rank(),
        )

        async def one(q: dict[str, Any]) -> None:
            async with gate:
                rounds = rounds_of(q)
                for system in systems:
                    started = time.perf_counter()
                    row = await evaluate(env, q, rounds, system)
                    row["wall_seconds"] = time.perf_counter() - started
                    rows.append(row)
                    verdict = {True: "right", False: "wrong", None: "ERROR"}[row["correct"]]
                    print(
                        f"{q['question_id']:>10} {q['question_type']:<26} {system:<5} {verdict}"
                        f"  {row['error'] or ''}",
                        file=sys.stderr,
                    )

        await asyncio.gather(*(one(q) for q in questions))

    RESULTS.mkdir(exist_ok=True)
    out = {
        "name": args.name,
        "config": {
            "dataset": "locomo10" if args.dataset == "locomo" else "longmemeval_s_cleaned",
            "n": len(questions),
            "seed": args.seed,
            "systems": systems,
            "budget_tokens": args.budget,
            "retriever": args.retriever,
            "keep_history": not args.no_history,
            "rerank": None if args.no_rerank else asdict(Rank()),
            "models": models,
            "judge_is_official": judge_model.endswith("gpt-4o-2024-08-06"),
            "ingest_key": env.ingest_key,
            "git": git_sha(),
            "llm_calls": {r: {"cached": c.hits, "fresh": c.misses} for r, c in caches.items()},
        },
        "rows": sorted(rows, key=lambda r: (r["question_id"], r["system"])),
    }
    path = RESULTS / f"{args.name}.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"saved {path}", file=sys.stderr)


if __name__ == "__main__":
    # psycopg's async mode can't use Windows' default (Proactor) event loop.
    asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)
