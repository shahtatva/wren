import pytest

from app.voice.bargein import is_echo, is_interruption, words

WREN = "You lost me around the second project. Lead with the result. Want to try it again?"


def test_words_lowercases_and_keeps_apostrophes():
    assert words("Don’t STOP, mm-hmm. Uh-huh!") == ["don't", "stop", "mmhmm", "uhhuh"]


def test_echo_of_wrens_words_is_not_an_interruption():
    assert not is_interruption("lost me around the second project", WREN)
    assert not is_interruption("Lead with the result.", WREN)


@pytest.mark.parametrize("text", ["mm-hmm", "Yeah, okay.", "Uh-huh. Right.", "Mmm", "hmm yeah", "Oh, nice", "um"])
def test_backchannels_are_not_interruptions(text):
    assert not is_interruption(text, WREN)


@pytest.mark.parametrize("text", ["I disagree", "the Sprout one", "Can we skip this?"])
def test_two_real_words_interrupt(text):
    assert is_interruption(text, WREN)


@pytest.mark.parametrize("text", ["Wait.", "Sorry", "No.", "Hang on, yeah"])
def test_a_single_stop_word_interrupts(text):
    assert is_interruption(text, WREN)


def test_one_random_word_waits():
    assert not is_interruption("Sprout", WREN)
    assert not is_interruption("", WREN)


def test_without_wren_speaking_nothing_counts_as_echo():
    assert is_interruption("the second project", "")


def test_is_echo_only_when_every_word_was_wrens():
    assert is_echo("around the second project", WREN)
    assert not is_echo("Yes.", WREN)  # an answer, even a short one
    assert not is_echo("the second one", WREN)
    assert not is_echo("", WREN)
