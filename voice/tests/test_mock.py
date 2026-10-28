from datetime import date, time

import pytest

from app.memory.types import PipelineEntry
from app.mock import (
    INTERVIEWER_NAMES,
    STEP_BACK_IN,
    STEP_IN,
    STEP_OUT,
    Cue,
    CueFilter,
    Mock,
    Target,
    interviewer_name,
    interviewer_title,
    plain_company,
    resolve_target,
    same_company,
    step_out_reason,
)

SPROUT = PipelineEntry("Sprout Social", "BDR", "recruiter screen", date(2026, 10, 16), time(11, 0), None)
TESS = PipelineEntry("Tessellate", None, "panel", date(2026, 10, 20), None, "four interviewers")


def feed_all(cues, deltas):
    return "".join(cues.feed(d) for d in deltas) + cues.flush()


def test_a_mock_cue_ends_the_reply_and_names_the_company():
    cues = CueFilter()
    assert feed_all(cues, ["Sure, let's do it. [mo", "ck: Sprout ", "Social] I'm Dana"]) == "Sure, let's do it. "
    assert cues.cue == Cue("mock", "Sprout Social")


@pytest.mark.parametrize("raw, cue", [
    ("[mock]", Cue("mock")), ("[ Mock : sprout ]", Cue("mock", "sprout")),
    ("[step out]", Cue("step_out")), ("[Step Out]", Cue("step_out")),
    ("[mock: ]", Cue("mock")), ("[ STEP   OUT ]", Cue("step_out")), ("[step out: done]", Cue("step_out")),
])
def test_cues_read_whatever_the_spacing_or_case(raw, cue):
    cues = CueFilter()
    assert feed_all(cues, [raw]) == "" and cues.cue == cue


def test_other_brackets_and_unclosed_ones_pass_through():
    for text in ("Okay [laughs] go on.", "Fine. [mo"):
        cues = CueFilter()
        assert feed_all(cues, [text]) == text and cues.cue is None


def test_everything_after_a_cue_is_dropped():
    cues = CueFilter()
    assert cues.feed("Sure. [step out] Thanks for") == "Sure. "
    assert cues.feed(" your time.") == "" and cues.flush() == "" and cues.cue == Cue("step_out")


def test_a_stray_bracket_doesnt_hide_a_real_cue():
    cues = CueFilter()
    assert feed_all(cues, ["Okay [uh ", "[mock: Acme]"]) == "Okay [uh " and cues.cue == Cue("mock", "Acme")


def test_a_bracket_that_never_closes_is_released_after_60_characters():
    cues = CueFilter()
    held = "[" + "a" * 58
    assert cues.feed("Okay " + held) == "Okay "
    assert cues.feed("a") == held + "a" and cues.cue is None


@pytest.mark.parametrize("text", [
    "Sure, let's do it. [mock: Sprout Social] I'm Dana", "Okay [laughs] go on.", "Fine. [mo", "[step out]",
    "Hi [uh [mock] bye", "A [bracket " + "x" * 80 + " that runs long] and more",
])
def test_how_the_deltas_are_split_changes_nothing(text):
    whole, by_char = CueFilter(), CueFilter()
    assert feed_all(whole, [text]) == feed_all(by_char, list(text)) and whole.cue == by_char.cue


def test_reset_clears_the_cue_and_anything_held():
    cues = CueFilter()
    cues.feed("Hi [mo")
    cues.reset()
    assert cues.flush() == ""
    cues.feed("[step out]")
    assert cues.cue == Cue("step_out")
    cues.reset()
    assert cues.cue is None and cues.feed("Hello.") == "Hello."


@pytest.mark.parametrize("stage, title", [
    ("recruiter screen", "recruiter"), ("phone screen", "recruiter"), ("hiring manager interview", "hiring manager"),
    ("hiring manager screen", "hiring manager"), ("panel", "interviewer"), (None, "interviewer"),
])
def test_the_interviewer_fits_the_stage(stage, title):
    assert interviewer_title(stage) == title


def test_targets_come_from_the_pipeline_first():
    assert resolve_target("sprout", (TESS, SPROUT), ("BDR",)) == Target("Sprout Social", "BDR", "recruiter screen", None)
    assert resolve_target(None, (SPROUT, TESS), ()).company == "Sprout Social"
    assert resolve_target("Acme", (SPROUT,), ("analyst",)) == Target("Acme", "analyst")
    assert resolve_target(None, (), ("BDR",)) == Target(None, "BDR")
    assert resolve_target("Tessellate", (TESS,), ("BDR",)).role == "BDR"  # the entry has no role


@pytest.mark.parametrize("raw, plain", [
    ("Acme (Series B)", "Acme"), (" Sprout Social. ", "Sprout Social"), ("()", None), ('"Sprout"', "Sprout"),
    ("Acme [Series B", "Acme"),  # "[mock: Acme [Series B]]" ends at the first "]", so the cue holds this
    ("Sprout Social", "Sprout Social"), ("", None), (None, None),
])
def test_a_cue_names_the_company_plainly(raw, plain):
    assert plain_company(raw) == plain


def test_with_no_company_the_soonest_entry_wins_whatever_the_order():
    undated = PipelineEntry("Acme", None, "applied", None, None, None)
    assert resolve_target(None, (TESS, SPROUT), ()).company == "Sprout Social"
    assert resolve_target(None, (undated, TESS), ()).company == "Tessellate"  # no date comes last
    assert resolve_target("  ", (TESS,), ()).company == "Tessellate"  # a blank company is no company


def test_the_introduction_says_who_and_where():
    def intro(target, title):
        return Mock(1, "Dana", title, target).introduction()
    assert intro(Target("Sprout Social"), "recruiter") == "I'm Dana, the recruiter at Sprout Social."
    assert intro(Target("Sprout Social"), "hiring manager") == "I'm Dana, the hiring manager at Sprout Social."
    assert intro(Target("Tessellate"), "interviewer") == "I'm Dana, one of the interviewers at Tessellate."
    assert intro(Target(), "interviewer") == "I'm Dana, and I'll be your interviewer today."


@pytest.mark.parametrize("title, company, who", [
    ("recruiter", "Sprout Social", "the recruiter at Sprout Social"),
    ("hiring manager", "Sprout Social", "the hiring manager at Sprout Social"),
    ("interviewer", "Tessellate", "one of the interviewers at Tessellate"),
    ("interviewer", None, "an interviewer"),
    ("recruiter", None, "a recruiter"),
])
def test_who_is_the_title_and_where(title, company, who):
    assert Mock(1, "Dana", title, Target(company)).who() == who


def test_interviewer_names_are_invented_and_stable():
    assert interviewer_name("s9", 1) in INTERVIEWER_NAMES and interviewer_name("s9", 1) == interviewer_name("s9", 1)
    assert len({interviewer_name(f"s{i}", 1) for i in range(40)}) > 3


def test_interviewer_names_dont_change_between_processes():
    # Picked with crc32, not hash(), which differs in every process. Pinned, so a change is noticed.
    assert interviewer_name("s9", 1) == "Omar" and interviewer_name("maya-s03", 1) == "Grace"


def test_the_spoken_lines_and_names_are_the_agreed_copy():
    assert STEP_IN == ("Okay, stepping into role.",)
    assert STEP_BACK_IN == ("Okay, stepping back into role.",)
    assert STEP_OUT == ("Stepping out.", "It's me again.")
    assert all(name.isalpha() and len(name) <= 6 for name in INTERVIEWER_NAMES)


@pytest.mark.parametrize("a, b, same", [
    ("Sprout Social", "sprout social", True), ("sprout", "Sprout Social", True), ("Sprout Social", "sprout", True),
    ("Sprout Social, Inc.", "sprout-social", True), ("Sprout", "Sprouts Farmers Market", False),
    ("Acme", "Sprout Social", False),
    ("", "Sprout", False), (None, "Sprout", False), ("Sprout", None, False), ("", "", False), (None, None, False),
    ("...", "Sprout", False),
])
def test_company_names_match_loosely(a, b, same):
    assert same_company(a, b) is same


@pytest.mark.parametrize("text, reason", [
    ("Wait, are you a real person?", "ai"), ("Are you an AI?", "ai"), ("Hold on, am I talking to a bot?", "ai"),
    ("Is this a real recruiter?", "ai"),
    ("I've been thinking about killing myself.", "crisis"), ("Some days I don't want to be alive.", "crisis"),
    ("Are you even real? I want to kill myself.", "crisis"),
    ("Can we stop the interview?", "stop"), ("Okay, let's stop there.", "stop"), ("End the mock.", "stop"),
    ("Step out.", "pause"), ("Can we step out for a second?", "pause"), ("Wren, can I ask you something?", "pause"),
])
def test_words_that_bring_wren_back(text, reason):
    assert step_out_reason(text) == reason


@pytest.mark.parametrize("text", [
    "I had to step out of my comfort zone to lead that project.",
    "We had to stop the launch and rethink the plan with the whole team.",
    "The deadline was killing me, but we shipped on time.",
    "I'm done with my degree in May.",
    "Is this role remote or hybrid?",
])
def test_everyday_words_in_an_answer_dont_step_out(text):
    assert step_out_reason(text) is None


@pytest.mark.parametrize("text, reason", [
    # "step out" is a request in a short message, or when it names the role, mock or interview
    ("Could you step out of the role for a moment? I want to ask about the pay range for this job.", "pause"),
    ("Can we step out of the interview? I have a question about my resume and how it reads.", "pause"),
    ("Can you step out of role for a second?", "pause"),
    # a message that opens by addressing Wren, with or without an opener first
    ("Hey Wren, can you hear me?", "pause"), ("Wren?", "pause"),
    ("Okay, Wren. Quick question about my resume.", "pause"),
    ("Wait, Wren, can I ask you something?", "pause"), ("Hold on, Wren, quick question.", "pause"),
    ("Actually, Wren, one more thing.", "pause"), ("Oh Wren, quick question.", "pause"),
    # the order is crisis, ai, stop, pause
    ("Wren, are you a bot?", "ai"), ("Wren, can we stop the interview?", "stop"),
    ("Wren, I want to kill myself.", "crisis"), ("Can we step out? Are you a person or a bot?", "ai"),
    # other ways to stop
    ("Let's end the mock interview.", "stop"), ("I want to stop.", "stop"), ("That's enough for today.", "stop"),
    ("That's enough.", "stop"), ("Can we end the interview here?", "stop"), ("Stop the interview please.", "stop"),
    ("Can we stop the mock for today?", "stop"),
    ("Please end the interview now, I'm too tired to keep going with this.", "stop"),
    ("Let's stop this.", "stop"), ("Let's stop right here.", "stop"), ("Can we call it a day?", "stop"),
    ("I need to stop for today.", "stop"), ("Let's end it here.", "stop"), ("Can we stop the practice?", "stop"),
    ("Can we end the session?", "stop"), ("Could you stop the interview?", "stop"),
    ("Can you end the mock, please?", "stop"), ("You can stop the interview.", "stop"),
    ("I think we should end the mock.", "stop"),
    # typographic apostrophes, as a text box or a transcript may give them
    ("Let\u2019s stop there.", "stop"), ("I don\u2019t want to be alive.", "crisis"),
    # other ways to ask whether it's an AI
    ("Are you a real person or a bot?", "ai"), ("Am I talking to a real person?", "ai"), ("Is this an AI?", "ai"),
    ("You're a bot, aren't you?", "ai"), ("Is this a person?", "ai"), ("Is this a human?", "ai"),
    ("Is it a person or a bot?", "ai"), ("You're just a bot, aren't you?", "ai"), ("You're actually an AI?", "ai"),
    ("You're just an AI.", "ai"),
    # other ways to say it's a crisis
    ("I don't want to live anymore.", "crisis"), ("I'm thinking about suicide.", "crisis"),
    ("I want to hurt myself.", "crisis"), ("I'm not safe right now.", "crisis"), ("I wish I were dead.", "crisis"),
    ("I don't feel safe.", "crisis"), ("I don't feel safe at home.", "crisis"), ("I feel like dying.", "crisis"),
    ("Everyone would be better off without me.", "crisis"), ("I don't feel safe right now.", "crisis"),
    ("I do not feel safe here.", "crisis"), ("They'd be better off without me", "crisis"),
])
def test_more_words_that_bring_wren_back(text, reason):
    assert step_out_reason(text) == reason


def test_distress_is_never_taken_for_a_request_to_stop():
    assert step_out_reason("I want to end it all.") == "crisis"
    assert step_out_reason("I want to stop feeling like this.") != "stop"


@pytest.mark.parametrize("text", [
    "Honestly, the hardest part was learning to step out of my comfort zone and speak up in meetings.",
    "Let's stop and think about what the customer actually needs before we build anything at all.",
    "We ended the interview process early because the budget was cut for that role.",
    "That's enough time to ship it if we start on Monday and keep the scope small.",
    "I volunteer with families who are in danger of losing their homes.",
    "I'm in danger of missing the deadline, so I plan my week around it.",
    "I don't want to live in New York, but I'd move for the right team.",
    "Is this an AI company?", "Are you hiring for this role?", "Is this a computer science role?",
    "Are you real busy right now?", "Let's stop and think about it first.",
    "I always end the session by recapping the next steps with the client.",
    "It wasn't a hill I wanted to die on, so I let the client choose the format.",
    # "step out of" something that isn't the role, mock or interview, even in a long answer
    "At a startup you sometimes have to step out of your role and help wherever the team needs it.",
    "I'm ready to step out of this role and into a team lead position next year.",
    # "stop" or "end" with an object that only starts with the interview, practice or session
    "I'd like to end the interview by asking what success looks like in the first ninety days.",
    "In my first month I want to end the practice of copying data by hand into the CRM.",
    "You can stop the session at any time, no questions asked.",
    "As a recruiter I need to end the interview process faster.",
    "My rule is simple. End the session with a clear next step for the client.",
    # "that's enough" and "I'm done" are also how people close an answer
    "That's enough about me.", "Okay, I think that's enough detail.", "I'm done.", "I'm done here.",
    # loose stop phrases with a proper ending still count only in a short message
    "When the numbers stopped adding up I told the team let's stop here and rerun everything.",
    "I told my manager that's enough, and I asked for a project with clearer goals.",
    # words about people and safety in a job question or a job story
    "Is this a human resources role?", "Is this an in-person role?",
    "I don't feel safe speaking up in meetings without data to back me",
    "the team was better off without me micromanaging every ticket",
    "I felt like dying when the demo crashed in front of the client",
])
def test_more_everyday_words_in_an_answer_dont_step_out(text):
    assert step_out_reason(text) is None
