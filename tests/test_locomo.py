"""LoCoMo -> LongMemEval-shaped questions, on a tiny made-up conversation (no download needed)."""

from eval.lme import rounds_of
from eval.locomo import convert, session_date

SAMPLE = {
    "sample_id": "conv-1",
    "conversation": {
        "speaker_a": "Caroline",
        "speaker_b": "Melanie",
        "session_1_date_time": "1:56 pm on 8 May, 2023",
        "session_1": [
            {"speaker": "Caroline", "dia_id": "D1:1", "text": "I went to a support group."},
            {"speaker": "Melanie", "dia_id": "D1:2", "text": "That sounds great!"},
            {"speaker": "Melanie", "dia_id": "D1:3", "text": "I painted a sunrise."},
        ],
        "session_2_date_time": "9:00 am on 20 June, 2023",
        "session_2": [{"speaker": "Melanie", "dia_id": "D2:1", "text": "Camping next week."}],
    },
    "qa": [
        {"question": "When?", "answer": "7 May 2023", "evidence": ["D1:1"], "category": 2},
        {"question": "What?", "answer": 2022, "evidence": ["D1:3", "D2:1"], "category": 4},
        {"question": "Trick?", "evidence": ["D1:2"], "category": 5, "adversarial_answer": "x"},
    ],
}


def test_dates_become_the_format_the_harness_parses():
    assert session_date("1:56 pm on 8 May, 2023") == "2023/05/08 (Mon) 13:56"


def test_adversarial_questions_are_dropped_and_fields_are_mapped():
    qs = convert(SAMPLE)

    assert [q["question_id"] for q in qs] == ["conv-1-0", "conv-1-1"]
    assert [q["question_type"] for q in qs] == ["temporal", "single-hop"]
    assert qs[1]["answer"] == "2022"  # numbers become text for the judge prompt
    assert qs[1]["answer_session_ids"] == ["D1", "D2"]
    assert qs[0]["question_date"] == "2023/06/20 (Tue) 09:00"  # the last session
    assert {q["history_id"] for q in qs} == {"locomo-conv-1"}  # one shared history


def test_no_turn_is_lost_even_when_a_session_ends_on_the_second_speaker():
    (q, _) = convert(SAMPLE)

    rounds = rounds_of(q)

    texts = [t for r in rounds for t in (r.user, r.assistant) if t]
    assert texts == [
        "Caroline: I went to a support group.",
        "Melanie: That sounds great!",
        "Melanie: I painted a sunrise.",
        "Melanie: Camping next week.",
    ]
    assert [r.session_id for r in rounds] == ["D1", "D1", "D2"]
