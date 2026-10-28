"""The report turns results JSON into tables. Checked on made-up results with known answers."""

import pytest

from eval.report import merge, quantile, render_eval, render_load


def row(qid, system, correct, qtype="knowledge-update", tokens=100, secs=0.1):
    return {
        "question_id": qid, "question_type": qtype, "system": system, "correct": correct,
        "error": None, "context_tokens": tokens, "retrieval_seconds": secs, "evidence_recall": 1.0,
    }  # fmt: skip


def result(name, rows, dataset="longmemeval_s_cleaned"):
    return {
        "name": name,
        "config": {
            "dataset": dataset, "n": 4, "seed": 0, "git": "abc123", "budget_tokens": 2000,
            "judge_is_official": False,
            "models": {"answer": "a", "extract": "e", "query": "q", "judge": "j"},
        },
        "rows": rows,
    }  # fmt: skip


def test_quantile_matches_hand_values():
    assert quantile([1.0], 0.95) == 1.0
    assert quantile([], 0.5) == 0.0
    assert quantile(list(map(float, range(1, 101))), 0.5) == pytest.approx(50.5)


def test_merge_keeps_one_row_per_question_and_system():
    a = result("a", [row("q1", "ours", True), row("q1", "full", True)])
    b = result("b", [row("q1", "full", False), row("q1", "ours (no-rerank)", False)])

    merged = merge([a, b])

    assert merged["name"] == "a + b"
    assert sorted((r["system"], r["correct"]) for r in merged["rows"]) == [
        ("full", True),  # the first run's copy wins
        ("ours", True),
        ("ours (no-rerank)", False),
    ]


def test_report_shows_accuracy_paired_table_and_plan_targets():
    rows = []
    for i in range(10):
        rows += [
            row(f"q{i}", "full", True, tokens=10_000),
            row(f"q{i}", "rag", i < 5, tokens=1_000),
            row(f"q{i}", "ours", i < 9, tokens=1_000),  # 90% vs rag's 50%
        ]

    text = render_eval(result("r", rows))

    assert "| ours | 90.0%" in text and "| rag | 50.0%" in text
    assert "ours vs rag" in text and "+40.0 pts" in text  # headline claim row too
    assert "-90.0%" in text  # tokens: 1000 vs 10000
    assert "Knowledge-update accuracy" in text and "(9 / 10)" in text
    assert "NOT the official judge model" in text


def test_a_system_with_only_errors_does_not_crash_the_report():
    rows = [row("q1", "full", True), row("q1", "ours", None)]
    assert "| ours | n/a" in render_eval(result("r", rows))


def test_load_report_lists_each_endpoint():
    text = render_load(
        {
            "name": "load-1",
            "config": {"users": 50, "seconds": 120, "host": "http://x", "git": "abc"},
            "endpoints": [
                {"name": "search (no rewrite)", "requests": 800, "failures": 0,
                 "p50_ms": 21.0, "p95_ms": 60.0, "rps": 6.6},
            ],
        }
    )  # fmt: skip
    assert "50 concurrent users" in text
    assert "| search (no rewrite) | 800 | 0 | 21 ms | 60 ms | 6.6 |" in text
