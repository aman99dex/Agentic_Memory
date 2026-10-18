from agentic_memory.fusion import rrf


def test_row_found_by_both_searches_wins():
    vector = [7732, 7731, 7730]
    keyword = [7731]
    assert rrf([vector, keyword]) == [7731, 7732, 7730]


def test_one_list_keeps_its_order():
    assert rrf([[3, 1, 2]]) == [3, 1, 2]


def test_nothing_in_nothing_out():
    assert rrf([]) == []
    assert rrf([[], []]) == []


def test_two_middling_ranks_beat_one_top_rank():
    # 5 is 1st in one list only; 9 is 2nd in both. 2/62 > 1/61.
    assert rrf([[5, 9], [8, 9]])[0] == 9
