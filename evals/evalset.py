"""The frozen eval set: personas, their facts, session transcripts and probes.

Files live in evals/data/. Facts are the unit of ground truth: transcripts tag
the turns that state a fact, and probes name the facts they ask about. Recall@5
checks whether a retrieved item cites one of those gold turns.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
OVERLAP_WORDS = 5  # a probe sharing this many consecutive words with a gold turn leaks its answer
SPEAKERS = ("user", "wren")


@dataclass(frozen=True)
class Fact:
    id: str
    text: str
    kind: str
    supersedes: str | None = None


@dataclass(frozen=True)
class SessionPlan:
    id: str
    date: str
    mode: str
    outline: str
    facts: tuple[str, ...]


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    profile: str
    facts: dict[str, Fact]
    sessions: tuple[SessionPlan, ...]


@dataclass(frozen=True)
class EvalTurn:
    id: str
    speaker: str
    text: str
    facts: tuple[str, ...]


@dataclass(frozen=True)
class Transcript:
    id: str
    persona: str
    date: str
    mode: str
    turns: tuple[EvalTurn, ...]


@dataclass(frozen=True)
class Probe:
    id: str
    persona: str
    question: str
    answer: str
    facts: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class EvalSet:
    personas: dict[str, Persona]
    transcripts: dict[str, Transcript]
    probes: tuple[Probe, ...]


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_eval_set(root: Path = DATA_DIR) -> EvalSet:
    personas = {}
    for path in sorted((root / "personas").glob("*.json")):
        raw = _read(path)
        facts = {f["id"]: Fact(f["id"], f["text"], f["kind"], f.get("supersedes")) for f in raw["facts"]}
        sessions = tuple(
            SessionPlan(s["id"], s["date"], s["mode"], s["outline"], tuple(s["facts"])) for s in raw["sessions"]
        )
        personas[raw["id"]] = Persona(raw["id"], raw["name"], raw["profile"], facts, sessions)

    transcripts = {}
    for path in sorted((root / "sessions").glob("*.json")):
        raw = _read(path)
        turns = tuple(EvalTurn(t["id"], t["speaker"], t["text"], tuple(t.get("facts", []))) for t in raw["turns"])
        transcripts[raw["id"]] = Transcript(raw["id"], raw["persona"], raw["date"], raw["mode"], turns)

    probes_path = root / "probes.json"
    probes = ()
    if probes_path.exists():
        probes = tuple(
            Probe(p["id"], p["persona"], p["question"], p["answer"], tuple(p["facts"]), tuple(p.get("tags", [])))
            for p in _read(probes_path)
        )
    return EvalSet(personas, transcripts, probes)


def _turns_stating(evalset: EvalSet, persona_id: str, fact_ids: set[str]) -> set[str]:
    return {
        turn.id
        for transcript in evalset.transcripts.values()
        if transcript.persona == persona_id
        for turn in transcript.turns
        if fact_ids & set(turn.facts)
    }


def gold_turns(evalset: EvalSet, probe: Probe) -> set[str]:
    return _turns_stating(evalset, probe.persona, set(probe.facts))


def _superseded_by_chain(persona: Persona, fact_ids: tuple[str, ...]) -> set[str]:
    stale: set[str] = set()
    frontier = list(fact_ids)
    while frontier:
        fact = persona.facts.get(frontier.pop())
        if fact and fact.supersedes and fact.supersedes not in stale:
            stale.add(fact.supersedes)
            frontier.append(fact.supersedes)
    return stale


def stale_turns(evalset: EvalSet, probe: Probe) -> set[str]:
    persona = evalset.personas[probe.persona]
    old = _superseded_by_chain(persona, probe.facts)
    return _turns_stating(evalset, probe.persona, old) - gold_turns(evalset, probe)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def shared_run(a: str, b: str, n: int = OVERLAP_WORDS) -> str | None:
    """The first run of n consecutive words that appears in both texts, if any."""
    words_a = _words(a)
    grams = {tuple(words_a[i : i + n]) for i in range(len(words_a) - n + 1)}
    words_b = _words(b)
    for i in range(len(words_b) - n + 1):
        gram = tuple(words_b[i : i + n])
        if gram in grams:
            return " ".join(gram)
    return None


def validate(evalset: EvalSet) -> list[str]:
    errors: list[str] = []
    plans = {}
    for persona in evalset.personas.values():
        for fact in persona.facts.values():
            if fact.supersedes and fact.supersedes not in persona.facts:
                errors.append(f"{fact.id}: supersedes unknown fact {fact.supersedes}")
        for plan in persona.sessions:
            plans[plan.id] = (persona, plan)
            for fact_id in plan.facts:
                if fact_id not in persona.facts:
                    errors.append(f"{plan.id}: plans unknown fact {fact_id}")
            if plan.id not in evalset.transcripts:
                errors.append(f"{plan.id}: no transcript yet (run python -m evals.generate)")

    turn_text = {}
    for transcript in evalset.transcripts.values():
        if transcript.id not in plans:
            errors.append(f"{transcript.id}: transcript has no session plan")
            continue
        persona, plan = plans[transcript.id]
        if transcript.persona != persona.id:
            errors.append(f"{transcript.id}: persona {transcript.persona} doesn't match plan {persona.id}")
        stated: set[str] = set()
        user_stated: set[str] = set()
        for turn in transcript.turns:
            if not turn.id.startswith(f"{transcript.id}:t"):
                errors.append(f"{turn.id}: turn ID must start with {transcript.id}:t")
            if turn.id in turn_text:
                errors.append(f"{turn.id}: duplicate turn ID")
            turn_text[turn.id] = turn.text
            if turn.speaker not in SPEAKERS:
                errors.append(f"{turn.id}: unknown speaker {turn.speaker}")
            for fact_id in turn.facts:
                if fact_id not in persona.facts:
                    errors.append(f"{turn.id}: unknown fact {fact_id}")
            stated |= set(turn.facts)
            if turn.speaker == "user":
                user_stated |= set(turn.facts)
        missing = sorted(set(plan.facts) - stated)
        if missing:
            errors.append(f"{transcript.id}: planned facts never stated: {', '.join(missing)}")
        # Extraction records only what the user said or confirmed, so a fact only Wren
        # states would be scored against turns that extraction never cites.
        wren_only = sorted((set(plan.facts) & stated) - user_stated)
        if wren_only:
            errors.append(f"{transcript.id}: no user turn states planned facts: {', '.join(wren_only)}")

    seen_probes: set[str] = set()
    for probe in evalset.probes:
        if probe.id in seen_probes:
            errors.append(f"{probe.id}: duplicate probe ID")
        seen_probes.add(probe.id)
        persona = evalset.personas.get(probe.persona)
        if persona is None:
            errors.append(f"{probe.id}: unknown persona {probe.persona}")
            continue
        for fact_id in probe.facts:
            if fact_id not in persona.facts:
                errors.append(f"{probe.id}: unknown fact {fact_id}")
        gold = gold_turns(evalset, probe)
        if not gold:
            errors.append(f"{probe.id}: no turn states its facts")
        for turn_id in sorted(gold):
            leak = shared_run(probe.question, turn_text.get(turn_id, ""))
            if leak:
                errors.append(f"{probe.id}: question repeats '{leak}' from {turn_id}")
    return errors


def summary(evalset: EvalSet) -> str:
    planned = sum(len(p.sessions) for p in evalset.personas.values())
    changed = sum(
        1 for probe in evalset.probes if probe.persona in evalset.personas and stale_turns(evalset, probe)
    )
    return (
        f"{len(evalset.personas)} personas, {len(evalset.transcripts)}/{planned} transcripts, "
        f"{len(evalset.probes)} probes ({changed} changed-fact)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the eval set, or print probes for hand-checking.")
    parser.add_argument("--show-probes", metavar="PERSONA", help="print each probe with its gold and stale turns")
    args = parser.parse_args(argv)
    evalset = load_eval_set()
    if args.show_probes:
        turns = {t.id: t for tr in evalset.transcripts.values() for t in tr.turns}
        for probe in evalset.probes:
            if probe.persona != args.show_probes:
                continue
            print(f"\n{probe.id}: {probe.question}\n  answer: {probe.answer}")
            for turn_id in sorted(gold_turns(evalset, probe)):
                print(f"  gold  {turn_id}: {turns[turn_id].text}")
            for turn_id in sorted(stale_turns(evalset, probe)):
                print(f"  stale {turn_id}: {turns[turn_id].text}")
    errors = validate(evalset)
    print(summary(evalset))
    for error in errors:
        print(f"ERROR {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
