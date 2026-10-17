"""Wren eval harness.

The harness imports the app's memory code from voice/app, so evals exercise the
same pipeline the app uses (CLAUDE.md: text mode and voice mode share one pipeline).
"""

import sys
from pathlib import Path

_VOICE_DIR = Path(__file__).resolve().parents[1] / "voice"
if str(_VOICE_DIR) not in sys.path:
    sys.path.insert(0, str(_VOICE_DIR))
