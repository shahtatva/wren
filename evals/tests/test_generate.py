from pathlib import Path

from evals.evalset import load_eval_set
from evals.generate import GeneratedSession, GeneratedTurn, build_prompt, to_transcript_json

FIXTURE = Path(__file__).parent / "fixtures" / "mini"


def test_transcript_json_numbers_turns_and_cleans_facts():
    persona = load_eval_set(FIXTURE).personas["ana"]
    plan = persona.sessions[0]
    generated = GeneratedSession(
        turns=[
            GeneratedTurn(speaker="wren", text=" Hi Ana. ", facts=[]),
            GeneratedTurn(speaker="user", text="Screen is Tuesday.", facts=["ana-f01", "ana-f01"]),
        ]
    )
    data = to_transcript_json("ana", plan, generated, generator="flash-model")
    assert [t["id"] for t in data["turns"]] == ["ana-s01:t01", "ana-s01:t02"]
    assert data["turns"][0]["text"] == "Hi Ana."
    assert data["turns"][1]["facts"] == ["ana-f01"]
    assert (data["persona"], data["date"], data["mode"]) == ("ana", "2026-10-01", "checkin")
    assert data["generator"] == "flash-model"


def test_prompt_includes_facts_history_and_persona_card():
    persona = load_eval_set(FIXTURE).personas["ana"]
    prompt = build_prompt(persona, persona.sessions[1], list(persona.sessions[:1]))
    assert "ana-f02: Oakridge Labs phone screen moved to Thursday October 8" in prompt
    assert "2026-10-01: Ana mentions the Oakridge screen" in prompt
    assert "You are Wren" in prompt
    assert "Persona card template" not in prompt  # only the card itself, not the template's notes
