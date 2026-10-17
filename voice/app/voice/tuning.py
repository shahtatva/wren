"""Timing knobs for the voice loop. Starting values come from the design; tune them with the bench."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Tuning:
    vad_silence_ms: int = 300  # quiet after speech before the session sends Finalize
    endpointing_ms: int = 300  # Nova-3's own pause detection; measured too slow to rely on (see vad.py)
    utterance_end_ms: int = 1000  # Nova-3 backstop gap; 1000 is Deepgram's minimum
    hold_checkin_s: float = 0.0  # extra wait after speech_final before a turn commits
    hold_mock_s: float = 0.7  # mocks are patient: people pause to think mid-answer
    confirm_window_s: float = 1.2  # how long a paused barge-in waits for real words
    idle_s: float = 120.0  # close speech-to-text after this long without speech
    session_cap_s: float = 2700.0  # 45 minutes
    hello_timeout_s: float = 5.0
    keepalive_s: float = 4.0  # Deepgram closes after 10 s with neither audio nor KeepAlive
    history_turns: int = 6
    memories_k: int = 8
    pipeline_days: int = 7
    max_reply_tokens: int = 400
    playback_grace_s: float = 3.0  # finish a reply this long after its audio should have ended, played or not
    echo_tail_s: float = 2.0  # after Wren stops, drop transcripts made only of Wren's words (speaker tail)

    def hold_s(self, mode: str) -> float:
        return self.hold_mock_s if mode == "mock" else self.hold_checkin_s
