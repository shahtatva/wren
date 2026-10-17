import logging
import time

from app.memory import retrieve as retrieve_module
from app.memory.retrieve import merge_hits, retrieve
from app.memory.types import SearchHit

QUESTIONS = ("When is the Sprout screen?", "How do they feel about Sprout?", "What story fits Sprout?")
MESSAGE = "Sprout moved my screen to Friday."


def hit(id: str, score: float) -> SearchHit:
    return SearchHit(id, "episode", f"memory {id}", (f"{id}:t01",), score)


class Pipeline:
    """Fake rewrite, embed and search stages that record what they were given."""

    def __init__(self, questions=QUESTIONS, rewrite_error=None, delays=(0.0, 0.0, 0.0)):
        self.questions, self.rewrite_error, self.delays = list(questions), rewrite_error, delays
        self.embedded, self.searched = [], []

    def rewrite(self, message):
        time.sleep(self.delays[0])
        if self.rewrite_error:
            raise self.rewrite_error
        return self.questions

    def embed(self, texts):
        time.sleep(self.delays[1])
        self.embedded.append(list(texts))
        return [[float(i), 0.5] for i in range(len(texts))]

    def search(self, questions, vectors, k):
        time.sleep(self.delays[2])
        self.searched.append((list(questions), vectors, k))
        return [[hit(f"m{i}", 0.01 * (i + 1))] for i in range(len(questions))]

    def run(self, k=8):
        return retrieve(MESSAGE, self.rewrite, self.embed, self.search, k=k)


def _log_records(caplog):
    return [r for r in caplog.records if r.name == "app.memory.retrieve"]


def test_merge_adds_up_a_memorys_scores_across_questions():
    per_question = [[hit("a", 0.030), hit("b", 0.040)], [hit("a", 0.020), hit("c", 0.010)]]
    merged = merge_hits(per_question, k=8)
    assert [(h.id, round(h.score, 3)) for h in merged] == [("a", 0.050), ("b", 0.040), ("c", 0.010)]


def test_merge_keeps_the_top_k_and_breaks_ties_by_id():
    per_question = [[hit("d", 0.02), hit("b", 0.02), hit("a", 0.03)], [hit("c", 0.02)]]
    assert [h.id for h in merge_hits(per_question, k=3)] == ["a", "b", "c"]


def test_all_questions_are_embedded_together_and_searched_in_one_call():
    pipeline = Pipeline()
    result = pipeline.run(k=8)
    assert pipeline.embedded == [list(QUESTIONS)]
    ((questions, vectors, per_question_k),) = pipeline.searched
    assert questions == list(QUESTIONS)
    assert vectors == [[0.0, 0.5], [1.0, 0.5], [2.0, 0.5]]
    assert per_question_k >= 8
    assert result.questions == list(QUESTIONS)
    assert result.fallback is None
    assert [h.id for h in result.hits] == ["m2", "m1", "m0"]


def test_a_failed_rewrite_falls_back_to_the_raw_message(caplog):
    caplog.set_level(logging.INFO, logger="app.memory.retrieve")
    pipeline = Pipeline(rewrite_error=TimeoutError("groq was slow"))
    result = pipeline.run()
    assert pipeline.embedded == [[MESSAGE]]
    assert pipeline.searched[0][0] == [MESSAGE]
    assert result.questions == [MESSAGE]
    assert result.fallback == "TimeoutError"
    (record,) = _log_records(caplog)
    assert record.levelno == logging.WARNING
    assert "fallback=TimeoutError" in record.getMessage()


def test_an_empty_rewrite_falls_back_to_the_raw_message():
    pipeline = Pipeline(questions=())
    result = pipeline.run()
    assert pipeline.searched[0][0] == [MESSAGE]
    assert result.fallback == "no questions"


def test_each_stage_is_timed_separately():
    result = Pipeline(delays=(0.03, 0.02, 0.04)).run()
    assert result.rewrite_ms >= 29
    assert result.embed_ms >= 19
    assert result.search_ms >= 39
    assert result.total_ms >= 89


def test_logs_one_line_per_call_with_stage_timings(caplog):
    caplog.set_level(logging.INFO, logger="app.memory.retrieve")
    Pipeline().run()
    (record,) = _log_records(caplog)
    message = record.getMessage()
    for field in ("total_ms=", "rewrite_ms=", "embed_ms=", "search_ms=", "budget_ms=300", "questions=3", "hits=3"):
        assert field in message
    assert record.levelno == logging.INFO


def test_warns_when_over_budget(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="app.memory.retrieve")
    monkeypatch.setattr(retrieve_module, "BUDGET_MS", 30)
    Pipeline(delays=(0.0, 0.0, 0.04)).run()
    (record,) = _log_records(caplog)
    assert record.levelno == logging.WARNING
