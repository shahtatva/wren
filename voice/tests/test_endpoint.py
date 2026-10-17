from app.voice.endpoint import Commit, EndOfTurn
from app.voice.events import Transcript, UtteranceEnd


def final(text: str, end: float, speech_final: bool = False) -> Transcript:
    return Transcript(text=text, is_final=True, speech_final=speech_final, start=max(0.0, end - 1.0), end=end)


def interim(text: str, end: float) -> Transcript:
    return Transcript(text=text, is_final=False, speech_final=False, start=max(0.0, end - 1.0), end=end)


def test_commits_on_speech_final_with_no_hold():
    policy = EndOfTurn(hold_s=0.0)
    assert policy.on_transcript(interim("Sprout moved", 1.1), now=1.2) is None
    commit = policy.on_transcript(final("Sprout moved my screen.", 1.9, speech_final=True), now=2.3)
    assert commit == Commit(text="Sprout moved my screen.", speech_end=1.9)
    assert not policy.has_words()


def test_finals_before_speech_final_are_joined():
    policy = EndOfTurn(hold_s=0.0)
    assert policy.on_transcript(final("So the screen", 1.0), now=1.1) is None
    commit = policy.on_transcript(final("moved to Friday.", 2.0, speech_final=True), now=2.3)
    assert commit == Commit(text="So the screen moved to Friday.", speech_end=2.0)


def test_hold_waits_and_new_words_cancel_it():
    policy = EndOfTurn(hold_s=0.7)
    assert policy.on_transcript(final("I led the project,", 1.0, speech_final=True), now=1.3) is None
    assert policy.poll(now=1.9) is None
    assert policy.on_transcript(interim("and then", 1.8), now=1.95) is None  # they kept going
    assert policy.poll(now=2.1) is None  # the old deadline (2.0) no longer applies
    assert policy.on_transcript(final("and then we cut churn.", 3.0, speech_final=True), now=3.3) is None
    assert policy.poll(now=3.9) is None
    assert policy.poll(now=4.0) == Commit(text="I led the project, and then we cut churn.", speech_end=3.0)


def test_empty_speech_final_after_words_commits():
    policy = EndOfTurn(hold_s=0.0)
    assert policy.on_transcript(final("That's all.", 2.0), now=2.1) is None
    assert policy.on_transcript(Transcript("", True, True, 2.0, 2.4), now=2.5) == Commit("That's all.", 2.0)


def test_utterance_end_commits_when_speech_final_never_came():
    policy = EndOfTurn(hold_s=0.0)
    assert policy.on_transcript(final("I think so", 2.4), now=2.6) is None
    assert policy.on_utterance_end(UtteranceEnd(last_word_end=2.4), now=3.5) == Commit("I think so", 2.4)


def test_utterance_end_with_minus_one_is_ignored():
    policy = EndOfTurn(hold_s=0.0)
    policy.on_transcript(final("I think so", 2.4), now=2.6)
    assert policy.on_utterance_end(UtteranceEnd(last_word_end=-1), now=3.5) is None
    assert policy.caption() == "I think so"


def test_interim_results_never_commit():
    policy = EndOfTurn(hold_s=0.0)
    assert policy.on_transcript(interim("tell me about", 0.8), now=0.9) is None
    assert policy.poll(now=10.0) is None
    assert policy.on_utterance_end(UtteranceEnd(last_word_end=0.8), now=10.0) is None
    assert policy.caption() == "tell me about"


def test_long_answer_with_pauses_commits_everything_once():
    policy = EndOfTurn(hold_s=0.7)
    pieces = [f"part {i} of the answer," for i in range(30)]
    now = 0.0
    for i, piece in enumerate(pieces):
        now += 3.0
        # Every few sentences the speaker pauses long enough for speech_final, then carries on.
        pause = i % 4 == 3
        assert policy.on_transcript(final(piece, end=now, speech_final=pause), now=now + 0.3) is None
        if pause:
            assert policy.poll(now=now + 0.6) is None
            assert policy.on_transcript(interim("and", now + 0.5), now=now + 0.65) is None
    assert policy.on_transcript(final("That's it.", now + 2, speech_final=True), now=now + 2.3) is None
    commit = policy.poll(now=now + 3.1)
    assert commit == Commit(" ".join(pieces + ["That's it."]), now + 2)
    assert policy.poll(now=now + 9) is None


def test_a_finalize_result_is_an_end_of_turn_candidate():
    policy = EndOfTurn(hold_s=0.0)
    assert policy.on_transcript(final("My screen moved", 1.2), now=1.3) is None
    flushed = Transcript("to Monday.", is_final=True, speech_final=False, start=1.3, end=2.1, from_finalize=True)
    assert policy.on_transcript(flushed, now=2.5) == Commit("My screen moved to Monday.", 2.1)


def test_speech_resumed_cancels_the_hold():
    policy = EndOfTurn(hold_s=0.7)
    policy.on_transcript(final("I led the project,", 1.0, speech_final=True), now=1.3)
    policy.speech_resumed()  # the silence detector heard them again, before any new words
    assert policy.poll(now=5.0) is None
    assert policy.caption() == "I led the project,"


def test_clear_drops_everything():
    policy = EndOfTurn(hold_s=0.7)
    policy.on_transcript(final("half a thought", 1.0, speech_final=True), now=1.1)
    policy.clear()
    assert policy.poll(now=5.0) is None and policy.caption() == "" and not policy.has_words()
