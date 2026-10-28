"""Does the LLM judge agree with a human? (ADR 0008: 50 hand-labelled answers, Cohen's kappa.)

    uv run python -m eval.agreement sample pilot-12 --n 50   # writes eval/results/judge-labels.csv
    (open the CSV, fill the `human` column with yes or no for every row, save it)
    uv run python -m eval.agreement score                     # writes judge-agreement.json

The sheet hides the judge's verdict so it cannot sway the human; `score` joins it back in.
"""

import argparse
import csv
import json
import random
from pathlib import Path

from eval.judge import is_correct
from eval.stats import kappa

RESULTS = Path(__file__).resolve().parent / "results"
LABELS = RESULTS / "judge-labels.csv"
FIELDS = [
    "result",
    "question_id",
    "system",
    "question_type",
    "question",
    "gold",
    "hypothesis",
    "human",
]


def sample(names: list[str], n: int, seed: int) -> None:
    rows = [
        {**r, "result": name}
        for name in names
        for r in json.loads((RESULTS / f"{name}.json").read_text(encoding="utf-8"))["rows"]
        if r["correct"] is not None and "question" in r
    ]
    if len(rows) < n:
        raise SystemExit(f"only {len(rows)} answered rows with a question text; need {n}")
    picked = random.Random(seed).sample(rows, n)  # noqa: S311 (a fair draw, not security)
    with LABELS.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows({**r, "human": ""} for r in picked)
    print(f"wrote {LABELS}: label the `human` column yes/no, then run `score`")


def score() -> None:
    judged: dict[tuple[str, str, str], bool] = {}
    humans: list[bool] = []
    keys: list[tuple[str, str, str]] = []
    with LABELS.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            label = row["human"].strip().lower()
            if label not in ("yes", "no"):
                raise SystemExit(f"{row['result']}/{row['question_id']}: `human` must be yes or no")
            keys.append((row["result"], row["question_id"], row["system"]))
            humans.append(label == "yes")
    for name in {k[0] for k in keys}:
        for r in json.loads((RESULTS / f"{name}.json").read_text(encoding="utf-8"))["rows"]:
            judged[(name, r["question_id"], r["system"])] = is_correct(r["judge_reply"])
    judge = [judged[k] for k in keys]
    agree = sum(h == j for h, j in zip(humans, judge, strict=True))
    out = {
        "n": len(keys),
        "agreement": agree / len(keys),
        "kappa": kappa(humans, judge),
        "human_yes_judge_no": sum(h and not j for h, j in zip(humans, judge, strict=True)),
        "human_no_judge_yes": sum(j and not h for h, j in zip(humans, judge, strict=True)),
        "results": sorted({k[0] for k in keys}),
    }
    (RESULTS / "judge-agreement.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sample")
    p.add_argument("names", nargs="+", help="results files (names without .json) to draw from")
    p.add_argument("--n", type=int, default=50)
    p.add_argument("--seed", type=int, default=0)
    sub.add_parser("score")
    a = ap.parse_args()
    sample(a.names, a.n, a.seed) if a.cmd == "sample" else score()
