from evals.score import ProbeOutcome, count_tokens, is_hit, is_stale, percentile, summarize

GOLD = {"s2:t02"}
STALE = {"s1:t02"}


def test_hit_only_counts_the_top_k():
    ranked = [("x",)] * 5 + [("s2:t02",)]
    assert not is_hit(ranked, GOLD, k=5)
    assert is_hit(ranked[1:], GOLD, k=5)


def test_stale_when_old_version_outranks_current():
    assert is_stale([("s1:t02",), ("s2:t02",)], GOLD, STALE)


def test_not_stale_when_current_version_comes_first():
    assert not is_stale([("s2:t02",), ("s1:t02",)], GOLD, STALE)


def test_stale_when_old_version_appears_without_current():
    assert is_stale([("x",), ("s1:t02",)], GOLD, STALE)


def test_not_stale_when_neither_version_is_retrieved():
    assert not is_stale([("x",), ("y",)], GOLD, STALE)


def test_percentile_is_nearest_rank():
    values = [float(v) for v in range(1, 21)]
    assert percentile(values, 50) == 10.0
    assert percentile(values, 95) == 19.0


def test_summarize_leaves_stale_rate_blank_without_changed_probes():
    outcomes = [ProbeOutcome("p1", True, None, 40, 120.0), ProbeOutcome("p2", False, None, 60, 200.0)]
    result = summarize(outcomes)
    assert result["recall_at_5"] == 0.5
    assert result["tokens_per_turn"] == 50.0
    assert result["stale_fact_rate"] == ""
    assert result["latency_p95_ms"] == 200.0


def test_count_tokens_counts_something():
    assert 0 < count_tokens("The user moved the Brightline screen to Friday.") < 20
