"""Write missing session transcripts from persona outlines: python -m evals.generate

Each transcript is generated once and committed, so the eval set stays frozen.
Delete a file in evals/data/sessions/ to regenerate that session.
"""

import argparse
import json
import re
import sys
from dataclasses import replace
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from evals.evalset import DATA_DIR, Persona, SessionPlan, load_eval_set
from evals.retry import with_retries

PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "generate_session.md"
PERSONA_CARD = (
    Path(__file__).resolve().parents[1] / ".claude" / "skills" / "wren-voice" / "references" / "persona-card.md"
)


class GeneratedTurn(BaseModel):
    speaker: Literal["user", "wren"]
    text: str
    facts: list[str]


class GeneratedSession(BaseModel):
    turns: list[GeneratedTurn]


def persona_card() -> str:
    """The card inside the template's code fence. The text around it is notes for developers."""
    text = PERSONA_CARD.read_text(encoding="utf-8")
    fenced = re.search(r"```[^\n]*\n(.*?)\n```", text, re.S)
    return fenced.group(1).strip() if fenced else text


def build_prompt(persona: Persona, plan: SessionPlan, earlier: list[SessionPlan]) -> str:
    facts = "\n".join(f"- {fact_id}: {persona.facts[fact_id].text}" for fact_id in plan.facts)
    history = "\n".join(f"- {p.date}: {p.outline}" for p in earlier) or "- (this is the first session)"
    return PROMPT_FILE.read_text(encoding="utf-8").format(
        persona_card=persona_card(),
        name=persona.name,
        profile=persona.profile,
        history=history,
        date=plan.date,
        mode=plan.mode,
        outline=plan.outline,
        facts=facts,
    )


def to_transcript_json(persona_id: str, plan: SessionPlan, generated: GeneratedSession, generator: str) -> dict:
    turns = [
        {"id": f"{plan.id}:t{i:02d}", "speaker": t.speaker, "text": t.text.strip(), "facts": sorted(set(t.facts))}
        for i, t in enumerate(generated.turns, start=1)
    ]
    return {
        "id": plan.id, "persona": persona_id, "date": plan.date, "mode": plan.mode,
        "generator": generator,  # provenance: which model wrote this transcript
        "turns": turns,
    }


def main(argv: list[str] | None = None) -> int:
    from app.config import load_settings
    from app.memory.llm import generate_json

    parser = argparse.ArgumentParser(description="Write missing session transcripts.")
    parser.add_argument("--model", help="Gemini model for this run (default: GEMINI_FLASH_MODEL)")
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.model:
        settings = replace(settings, gemini_flash_model=args.model)
    evalset = load_eval_set()
    out_dir = DATA_DIR / "sessions"
    out_dir.mkdir(exist_ok=True)
    for persona in evalset.personas.values():
        for i, plan in enumerate(persona.sessions):
            path = out_dir / f"{plan.id}.json"
            if path.exists():
                continue
            prompt = build_prompt(persona, plan, list(persona.sessions[:i]))
            generated = with_retries(lambda: generate_json(prompt, GeneratedSession, settings))
            data = to_transcript_json(persona.id, plan, generated, settings.gemini_flash_model)
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"wrote {path.name} ({len(data['turns'])} turns)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
