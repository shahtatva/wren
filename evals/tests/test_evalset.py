from dataclasses import replace
from pathlib import Path

from evals.evalset import gold_turns, load_eval_set, shared_run, stale_turns, validate

FIXTURE = Path(__file__).parent / "fixtures" / "mini"


def test_fixture_is_valid():
    assert validate(load_eval_set(FIXTURE)) == []


def test_gold_turns_follow_fact_tags():
    evalset = load_eval_set(FIXTURE)
    probe = evalset.probes[1]
    assert gold_turns(evalset, probe) == {"ana-s01:t04"}


def test_stale_turns_follow_supersedes_chain():
    evalset = load_eval_set(FIXTURE)
    probe = evalset.probes[0]
    assert gold_turns(evalset, probe) == {"ana-s02:t02"}
    assert stale_turns(evalset, probe) == {"ana-s01:t02"}


def test_probe_without_gold_turn_is_an_error():
    evalset = load_eval_set(FIXTURE)
    persona = evalset.personas["ana"]
    extra = replace(persona.facts["ana-f03"], id="ana-f99", text="Never said")
    personas = {"ana": replace(persona, facts={**persona.facts, "ana-f99": extra})}
    probe = replace(evalset.probes[1], facts=("ana-f99",))
    broken = replace(evalset, personas=personas, probes=(probe,))
    assert any("no turn states" in e for e in validate(broken))


def test_probe_that_copies_transcript_wording_is_flagged():
    evalset = load_eval_set(FIXTURE)
    probe = replace(evalset.probes[0], question="Oakridge pushed the screen to Thursday the eighth?")
    errors = validate(replace(evalset, probes=(probe,)))
    assert any("repeats" in e for e in errors)


def test_unknown_fact_tag_is_an_error():
    evalset = load_eval_set(FIXTURE)
    transcript = evalset.transcripts["ana-s02"]
    turn = replace(transcript.turns[1], facts=("ana-f42",))
    bad = replace(transcript, turns=(transcript.turns[0], turn))
    errors = validate(replace(evalset, transcripts={**evalset.transcripts, "ana-s02": bad}))
    assert any("unknown fact ana-f42" in e for e in errors)


def test_planned_fact_never_stated_is_an_error():
    evalset = load_eval_set(FIXTURE)
    transcript = evalset.transcripts["ana-s01"]
    bad = replace(transcript, turns=transcript.turns[:2])
    errors = validate(replace(evalset, transcripts={**evalset.transcripts, "ana-s01": bad}))
    assert any("never stated" in e and "ana-f03" in e for e in errors)


def test_planned_fact_stated_only_by_wren_is_an_error():
    evalset = load_eval_set(FIXTURE)
    transcript = evalset.transcripts["ana-s01"]
    t01, t02, t03, t04 = transcript.turns
    bad = replace(transcript, turns=(t01, t02, replace(t03, facts=("ana-f03",)), replace(t04, facts=())))
    errors = validate(replace(evalset, transcripts={**evalset.transcripts, "ana-s01": bad}))
    assert any("no user turn" in e and "ana-f03" in e for e in errors)


def test_shared_run_ignores_short_overlaps():
    assert shared_run("tell me about yourself", "so, tell me about yourself please") is None
    assert shared_run("the food bank churn dashboard", "I built the food bank churn dashboard") == "the food bank churn dashboard"
