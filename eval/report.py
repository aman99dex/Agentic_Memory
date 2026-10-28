"""Turn results JSON into the markdown tables in the README. No number is typed by hand.

uv run python -m eval.report pilot-12                       # print one run
uv run python -m eval.report pilot-12 ablate-vector load-1  # merge runs; load tests get their own table
uv run python -m eval.report pilot-12 --readme              # replace the block between the markers

Names are files in eval/results/ without .json. Runs on the same questions merge into one table, so
an ablation sits next to the full system. eval/results/judge-agreement.json is added if present.
"""

import argparse
import json
import statistics
import sys
from itertools import combinations
from pathlib import Path
from typing import Any

from eval.stats import holm, paired, wilson

RESULTS = Path(__file__).resolve().parent / "results"
README = Path(__file__).resolve().parents[1] / "README.md"
START, END = "<!-- results:start -->", "<!-- results:end -->"
BASE_SYSTEMS = ("full", "rag", "ours")
DATASETS = {"longmemeval_s_cleaned": "LongMemEval_s", "locomo10": "LoCoMo"}
# The plan's resume claims (plan.md, "Definition of done"). Measured values come from the data.
TARGETS = {
    "accuracy": "+15 pts vs naive RAG",
    "tokens": "-90% vs full context",
    "p95": "< 150 ms",
    "update": "> 80%",
}


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def quantile(xs: list[float], q: float) -> float:
    if len(xs) < 2:
        return xs[0] if xs else 0.0
    return statistics.quantiles(xs, n=100, method="inclusive")[round(q * 100) - 1]


def merge(results: list[dict[str, Any]]) -> dict[str, Any]:
    """One result from several runs of the same questions: first config, rows de-duplicated."""
    seen: set[tuple[str, str]] = set()
    rows = []
    for result in results:
        for r in result["rows"]:
            if (key := (r["question_id"], r["system"])) not in seen:
                seen.add(key)
                rows.append(r)
    return {**results[0], "name": " + ".join(r["name"] for r in results), "rows": rows}


def _score(rows: list[dict[str, Any]]) -> tuple[int, int]:
    ok = [r for r in rows if r["correct"] is not None]
    return sum(r["correct"] for r in ok), len(ok)


def render_eval(result: dict[str, Any]) -> str:
    cfg, rows = result["config"], result["rows"]
    seen = list(dict.fromkeys(r["system"] for r in rows))
    systems = [s for s in BASE_SYSTEMS if s in seen] + [s for s in seen if s not in BASE_SYSTEMS]
    by_sys = {s: [r for r in rows if r["system"] == s] for s in systems}
    judge = "official" if cfg["judge_is_official"] else "NOT the official judge model"
    out = [
        f"**{result['name']}**: {DATASETS.get(cfg['dataset'], cfg['dataset'])}, {cfg['n']} questions "
        f"(stratified sample, seed {cfg['seed']}), commit `{cfg['git']}`. Answer model "
        f"`{cfg['models']['answer']}`, extract model `{cfg['models']['extract']}`, judge "
        f"`{cfg['models']['judge']}` ({judge}; judge prompts "
        f"{'are the benchmark prompts' if cfg['dataset'] != 'locomo10' else 'are our own wording'}). "
        f"Context budget {cfg['budget_tokens']} tokens for `rag` and `ours`; `full` is unbudgeted.",
        "",
        "| System | Accuracy (95% CI) | Evidence recall | Context tokens | Retrieval p50 / p95 | Errors |",
        "|---|---|---|---|---|---|",
    ]
    for s, rs in by_sys.items():
        ok = [r for r in rs if r["correct"] is not None]
        if not ok:
            out.append(f"| {s} | n/a | n/a | n/a | n/a | {len(rs)} |")
            continue
        k, n = _score(rs)
        lo, hi = wilson(k, n)
        rec = [r["evidence_recall"] for r in ok if r.get("evidence_recall") is not None]
        sec = [r["retrieval_seconds"] for r in ok]
        out.append(
            f"| {s} | {pct(k / n)} ({pct(lo)} to {pct(hi)}) | "
            f"{pct(statistics.mean(rec)) if rec else 'n/a'} | "
            f"{statistics.mean(r['context_tokens'] for r in ok):.0f} | "
            f"{quantile(sec, 0.5):.2f}s / {quantile(sec, 0.95):.2f}s | {len(rs) - n} |"
        )
    out += _by_type(rows, systems, by_sys) + _paired(systems, by_sys) + _headline(by_sys)
    return "\n".join(out)


def _by_type(
    rows: list[dict[str, Any]], systems: list[str], by_sys: dict[str, list[dict[str, Any]]]
) -> list[str]:
    out = [
        "",
        "By question type (right / answered):",
        "",
        "| Type | " + " | ".join(systems) + " |",
        "|---|" + "---|" * len(systems),
    ]
    for t in sorted({r["question_type"] for r in rows}):
        cells = []
        for s in systems:
            k, n = _score([r for r in by_sys[s] if r["question_type"] == t])
            cells.append(f"{k} / {n}")
        out.append(f"| {t} | " + " | ".join(cells) + " |")
    return out


def _paired(systems: list[str], by_sys: dict[str, list[dict[str, Any]]]) -> list[str]:
    # With `ours` present, compare it against every other row (baselines and ablations).
    pairs = (
        [(s, "ours") for s in systems if s != "ours"]
        if "ours" in systems
        else list(combinations(systems, 2))
    )
    if not pairs:
        return []
    results = []
    for a, b in pairs:
        ra = {r["question_id"]: r["correct"] for r in by_sys[a] if r["correct"] is not None}
        rb = {r["question_id"]: r["correct"] for r in by_sys[b] if r["correct"] is not None}
        if ra.keys() & rb.keys():
            results.append((a, b, paired(ra, rb)))
    if not results:
        return []
    adjusted = holm([p.p_mcnemar for _, _, p in results])
    out = [
        "",
        "Paired comparisons (same questions; 10k bootstrap resamples; exact McNemar; Holm-adjusted):",
        "",
        "| B vs A | n | A right, B wrong | B right, A wrong | Δ accuracy (95% CI) | p (Holm) |",
        "|---|---|---|---|---|---|",
    ]
    for (a, b, p), padj in zip(results, adjusted, strict=True):
        out.append(
            f"| {b} vs {a} | {p.n} | {p.only_a} | {p.only_b} | {100 * p.delta:+.1f} pts "
            f"({100 * p.ci_low:+.1f} to {100 * p.ci_high:+.1f}) | {padj:.3f} |"
        )
    out += [
        "",
        "A difference whose interval includes 0 is not evidence of a real difference; "
        "small samples cannot show small effects (ADR 0008).",
    ]
    return out


def _headline(by_sys: dict[str, list[dict[str, Any]]]) -> list[str]:
    """The four resume claims from the plan, measured. Rows appear only when the data exists."""
    rows: list[tuple[str, str, str]] = []
    ours, rag, full = (by_sys.get(s, []) for s in BASE_SYSTEMS[::-1])  # ours, rag, full
    answered = lambda rs: [r for r in rs if r["correct"] is not None]  # noqa: E731
    if answered(ours) and answered(rag):
        (ko, no), (kr, nr) = _score(ours), _score(rag)
        rows.append(
            ("Accuracy vs naive RAG", TARGETS["accuracy"], f"{100 * (ko / no - kr / nr):+.1f} pts")
        )
    if answered(ours) and answered(full):
        tokens = lambda rs: statistics.mean(r["context_tokens"] for r in answered(rs))  # noqa: E731
        rows.append(
            (
                "Tokens per answer vs full context",
                TARGETS["tokens"],
                f"{100 * (tokens(ours) / tokens(full) - 1):+.1f}%",
            )
        )
    if answered(ours):
        p95 = quantile([r["retrieval_seconds"] for r in answered(ours)], 0.95)
        rows.append(
            (
                "p95 recall latency (includes the LLM query rewrite)",
                TARGETS["p95"],
                f"{1000 * p95:.0f} ms",
            )
        )
        k, n = _score([r for r in ours if r["question_type"] == "knowledge-update"])
        if n:
            rows.append(
                ("Knowledge-update accuracy", TARGETS["update"], f"{pct(k / n)} ({k} / {n})")
            )
    if not rows:
        return []
    return [
        "",
        "Against the plan's targets:",
        "",
        "| Claim | Target | Measured |",
        "|---|---|---|",
        *(f"| {c} | {t} | {m} |" for c, t, m in rows),
    ]


def render_load(result: dict[str, Any]) -> str:
    cfg = result["config"]
    out = [
        f"**{result['name']}**: load test, {cfg['users']} concurrent users for {cfg['seconds']} s "
        f"against `{cfg['host']}`, commit `{cfg['git']}`.",
        "",
        "| Endpoint | Requests | Failures | p50 | p95 | Requests/s |",
        "|---|---|---|---|---|---|",
    ]
    for e in result["endpoints"]:
        out.append(
            f"| {e['name']} | {e['requests']} | {e['failures']} | {e['p50_ms']:.0f} ms | "
            f"{e['p95_ms']:.0f} ms | {e['rps']:.1f} |"
        )
    return "\n".join(out)


def render_agreement(a: dict[str, Any]) -> str:
    return (
        f"Judge vs human on {a['n']} hand-labelled answers: {pct(a['agreement'])} agreement, "
        f"Cohen's κ = {a['kappa']:.2f} (human yes / judge no: {a['human_yes_judge_no']}, "
        f"human no / judge yes: {a['human_no_judge_yes']})."
    )


def render(names: list[str]) -> str:
    loaded = [json.loads((RESULTS / f"{n}.json").read_text(encoding="utf-8")) for n in names]
    evals = [r for r in loaded if "rows" in r]
    parts = [render_eval(merge(evals))] if evals else []
    parts += [render_load(r) for r in loaded if "endpoints" in r]
    if evals and (agreement := RESULTS / "judge-agreement.json").exists():
        parts.append(render_agreement(json.loads(agreement.read_text(encoding="utf-8"))))
    return "\n\n".join(parts)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+", help="results files to merge, without .json")
    ap.add_argument("--readme", action="store_true")
    args = ap.parse_args()
    text = render(args.names)
    if not args.readme:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # Windows is cp1252
        print(text)
    else:
        s = README.read_text(encoding="utf-8")
        i, j = s.index(START) + len(START), s.index(END)
        README.write_text(s[:i] + "\n" + text + "\n" + s[j:], encoding="utf-8", newline="")
