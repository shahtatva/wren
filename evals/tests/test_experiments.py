from dataclasses import asdict, replace
from pathlib import Path

import pytest

from app.memory.types import SearchHit
from evals.evalset import load_eval_set
from evals.experiments import BASE, EXPERIMENTS, RANKINGS, Config, Services, Timed, probe_outcome, variants
from evals.ingest import user_id_for
from evals.score import count_tokens

FIXTURE = Path(__file__).parent / "fixtures" / "mini"
EVALSET = load_eval_set(FIXTURE)
MOVED_PROBE = EVALSET.probes[0]  # gold ana-s02:t02, stale ana-s01:t02


def test_each_experiment_varies_only_its_own_factor_and_includes_the_base():
    for experiment, (factor, names) in EXPERIMENTS.items():
        configs = dict(variants(experiment))
        assert list(configs) == list(names)
        assert sum(config == BASE for config in configs.values()) == 1, experiment
        for config in configs.values():
            changed = {k for k, v in asdict(config).items() if v != asdict(BASE)[k]}
            assert changed <= {factor}, experiment


def test_unknown_variants_are_rejected():
    with pytest.raises(ValueError):
        variants("E1", only=("chunk512", "nonsense"))


class Fakes:
    """Fake API calls that record their inputs. Each reports a fixed latency."""

    def __init__(self, hits):
        self.hits, self.embedded, self.searched = hits, [], []

    def services(self) -> Services:
        return Services(
            extract=None,
            embed_documents=None,
            embed_query=lambda texts, task: self.embedded.append((list(texts), task)) or Timed([[0.1] * 3 for _ in texts], 40.0),
            rewrite=lambda question: Timed(["When is the Oakridge call?", "Did the Oakridge call move?"], 300.0),
            hyde=lambda question: Timed("The user's Oakridge screen moved to Thursday.", 250.0),
        )

    def search(self, user_id, questions, vectors, k, ranking):
        self.searched.append((user_id, list(questions), k, ranking))
        return [self.hits for _ in questions]


def test_the_current_version_ranked_first_is_a_fresh_hit():
    fakes = Fakes([SearchHit("a", "pipeline", "Oakridge Labs: phone screen, Thursday.", ("ana-s02:t02",), 0.03),
                   SearchHit("b", "pipeline", "Oakridge Labs: phone screen, Tuesday.", ("ana-s01:t02",), 0.02)])
    outcome = probe_outcome(EVALSET, MOVED_PROBE, BASE, fakes.services(), fakes.search)
    assert outcome.hit is True and outcome.stale is False
    assert outcome.tokens == count_tokens("Oakridge Labs: phone screen, Thursday.") + count_tokens("Oakridge Labs: phone screen, Tuesday.")
    assert outcome.latency_ms >= 40.0
    ((user_id, questions, k, ranking),) = fakes.searched
    assert (user_id, questions, ranking) == (user_id_for("atomic", "ana"), [MOVED_PROBE.question], RANKINGS["vector"])
    assert k >= 5


def test_the_old_version_alone_is_a_stale_miss():
    fakes = Fakes([SearchHit("b", "pipeline", "Oakridge Labs: phone screen, Tuesday.", ("ana-s01:t02",), 0.02)])
    outcome = probe_outcome(EVALSET, MOVED_PROBE, BASE, fakes.services(), fakes.search)
    assert outcome.hit is False and outcome.stale is True


def test_probes_without_a_changed_fact_have_no_stale_verdict():
    fakes = Fakes([SearchHit("c", "story", "The user built a food bank dashboard.", ("ana-s01:t04",), 0.03)])
    outcome = probe_outcome(EVALSET, EVALSET.probes[1], BASE, fakes.services(), fakes.search)
    assert outcome.hit is True and outcome.stale is None


def test_rewrite_searches_with_the_rewritten_questions_and_counts_their_latency():
    fakes = Fakes([])
    outcome = probe_outcome(EVALSET, MOVED_PROBE, replace(BASE, query="rewrite"), fakes.services(), fakes.search)
    assert fakes.embedded == [(["When is the Oakridge call?", "Did the Oakridge call move?"], "query")]
    assert fakes.searched[0][1] == ["When is the Oakridge call?", "Did the Oakridge call move?"]
    assert outcome.latency_ms >= 340.0


def test_hyde_searches_with_the_note_embedded_as_a_document():
    fakes = Fakes([])
    outcome = probe_outcome(EVALSET, MOVED_PROBE, replace(BASE, query="hyde"), fakes.services(), fakes.search)
    assert fakes.embedded == [(["The user's Oakridge screen moved to Thursday."], "document")]
    assert outcome.latency_ms >= 290.0


def test_storage_and_ranking_choose_the_user_and_the_ranking_parameters():
    fakes = Fakes([])
    probe_outcome(EVALSET, MOVED_PROBE, Config("turn", "raw", "rrf_boost"), fakes.services(), fakes.search)
    user_id, _, _, ranking = fakes.searched[0]
    assert user_id == user_id_for("turn", "ana")
    assert ranking == RANKINGS["rrf_boost"]
