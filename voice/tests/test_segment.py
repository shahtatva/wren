import asyncio

from app.voice.segment import Segmenter, clean, segments


def test_splits_sentences_as_they_complete():
    seg = Segmenter()
    assert seg.feed("Sprout moved") == []
    assert seg.feed(" to Friday. That") == ["Sprout moved to Friday."]
    assert seg.feed(" gives you a night.") == []  # a sentence end needs the space after it, or the end of the reply
    assert seg.flush() == ["That gives you a night."]


def test_a_first_sentence_with_a_comma_stays_whole():
    seg = Segmenter()
    pieces = seg.feed("As an AI, I generate text based on patterns, and sometimes it stops. Next. ") + seg.flush()
    assert pieces == ["As an AI, I generate text based on patterns, and sometimes it stops.", "Next."]


def test_does_not_split_abbreviations_or_decimals():
    seg = Segmenter()
    out = seg.feed("Dr. Lee said it's 2.5 times better, e.g. the alumni story. Nice")
    assert out == ["Dr. Lee said it's 2.5 times better, e.g. the alumni story."]
    assert seg.feed(" one. ") == ["Nice one."]


def test_question_and_exclamation_end_sentences_and_keep_closing_quotes():
    seg = Segmenter()
    assert seg.feed('You said "tell me about yourself." Ready? Go! Then') == [
        'You said "tell me about yourself."',
        "Ready?",
        "Go!",
    ]


def test_long_run_on_splits_at_max_words():
    seg = Segmenter(max_words=8)
    assert seg.feed("one two three four five six seven eight nine ten ") == ["one two three four five six seven eight"]
    assert seg.flush() == ["nine ten"]


def test_flush_returns_the_tail():
    seg = Segmenter()
    assert seg.feed("One more thing") == []
    assert seg.flush() == ["One more thing"]
    assert seg.flush() == []


def test_segments_that_clean_to_nothing_are_dropped():
    seg = Segmenter()
    assert seg.feed("🙂 ") == []
    assert seg.flush() == []


def test_clean_strips_markdown_emoji_and_links():
    assert clean("**One fix:** try this 🙂 see https://example.com/x") == "One fix: try this see"
    assert clean("1️⃣ Start with a headline") == "Start with a headline"
    assert clean("- Lead with the number\n- `End` on the result") == "Lead with the number End on the result"
    assert clean("## Debrief") == "Debrief"


def test_clean_maps_unicode_hyphens_and_dashes():
    assert clean("a three‑sentence pitch—your whole story – then stop") == (
        "a three-sentence pitch, your whole story, then stop"
    )
    assert clean("from 9–5 most days") == "from 9-5 most days"


def test_segments_streams_from_async_deltas():
    async def deltas():
        for d in ("Hey Maya. ", "Sprout is ", "Friday. How", " are you?"):
            yield d

    async def collect():
        return [s async for s in segments(deltas())]

    assert asyncio.run(collect()) == ["Hey Maya.", "Sprout is Friday.", "How are you?"]


def test_unfeed_takes_back_text_not_yet_spoken():
    seg = Segmenter()
    assert seg.feed("You're ready.Whenev") == []
    seg.unfeed(6)
    assert seg.flush() == ["You're ready."]


def test_bracketed_asides_are_never_spoken():
    assert clean("[laughs] Let's go.") == "Let's go."
    assert clean("Sure. [cut off here. They didn't hear the rest]") == "Sure."


def test_a_bracket_split_across_segments_is_dropped_from_both():
    # The streaming segmenter splits a bracket that spans sentences before clean() sees it.
    seg = Segmenter()
    assert seg.feed("Sure. [cut off here. They didn't hear the rest] Next. ") + seg.flush() == ["Sure.", "Next."]
