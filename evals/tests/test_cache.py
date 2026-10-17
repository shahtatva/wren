from evals.cache import cache_key, cached_batch, cached_json


def test_cached_json_computes_once(tmp_path):
    calls = []

    def compute():
        calls.append(1)
        return {"n": 1}

    key = cache_key("model", "prompt", "input")
    assert cached_json("x", key, compute, tmp_path) == {"n": 1}
    assert cached_json("x", key, compute, tmp_path) == {"n": 1}
    assert len(calls) == 1


def test_cache_key_changes_with_any_part():
    assert cache_key("a", "b") != cache_key("a", "c")
    assert cache_key("ab", "c") != cache_key("a", "bc")


def test_cached_batch_only_computes_missing(tmp_path):
    asked = []

    def compute(indexes):
        asked.append(indexes)
        return [[float(i)] for i in indexes]

    keys = [cache_key(str(i)) for i in range(3)]
    assert cached_batch("v", keys[:2], compute, tmp_path) == [[0.0], [1.0]]
    assert cached_batch("v", keys, compute, tmp_path) == [[0.0], [1.0], [2.0]]
    assert asked == [[0, 1], [2]]
