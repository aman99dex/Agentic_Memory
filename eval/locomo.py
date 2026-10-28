"""LoCoMo (Maharana et al., 2024) in the shape the LongMemEval harness already runs (ADR 0017).

Data: https://github.com/snap-research/locomo/blob/main/data/locomo10.json (10 conversations of
about 590 turns, 1986 questions). Download it to eval/data/locomo10.json.

Each conversation is one long history shared by about 200 questions, so a question carries
`history_id` and the harness ingests that history once. Category 5 (adversarial) has no gold
answer and is left out, as Mem0's evaluation does.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent / "data" / "locomo10.json"
CATEGORIES = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop"}
LME_DATE = "%Y/%m/%d (%a) %H:%M"  # the format lme.parse_date reads


def session_date(text: str) -> str:
    """'1:56 pm on 8 May, 2023' -> '2023/05/08 (Mon) 13:56'."""
    return datetime.strptime(text, "%I:%M %p on %d %B, %Y").strftime(LME_DATE)


def convert(sample: dict[str, Any]) -> list[dict[str, Any]]:
    conv = sample["conversation"]
    numbers = sorted(
        int(k.removeprefix("session_")) for k in conv if re.fullmatch(r"session_\d+", k) and conv[k]
    )
    ids = [f"D{n}" for n in numbers]
    dates = [session_date(conv[f"session_{n}_date_time"]) for n in numbers]
    # Roles alternate by position, not by speaker, so no turn is skipped when a session opens
    # with the second speaker. The speaker's name stays in the text: questions ask about people.
    sessions = [
        [
            {
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"{t['speaker']}: {t['text']}",
            }
            for i, t in enumerate(conv[f"session_{n}"])
        ]
        for n in numbers
    ]
    history = f"locomo-{sample['sample_id']}"
    return [
        {
            "question_id": f"{sample['sample_id']}-{i}",
            "question_type": CATEGORIES[qa["category"]],
            "question": qa["question"],
            "answer": str(qa["answer"]),
            "question_date": dates[-1],  # no question date in the data: ask as of the last session
            "haystack_session_ids": ids,
            "haystack_dates": dates,
            "haystack_sessions": sessions,
            "answer_session_ids": sorted(
                {e.split(":")[0] for ev in qa["evidence"] for e in re.findall(r"D\d+:\d+", ev)}
            ),
            "history_id": history,
            "dataset": "locomo",
        }
        for i, qa in enumerate(sample["qa"])
        if qa["category"] in CATEGORIES
    ]


def load(path: Path = DATA) -> list[dict[str, Any]]:
    return [q for sample in json.loads(path.read_text(encoding="utf-8")) for q in convert(sample)]
