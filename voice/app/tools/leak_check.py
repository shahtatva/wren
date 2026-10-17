"""Check that no key from voice/.env made it into the browser bundle: python -m app.tools.leak_check

Run it after `npm run build` in web/. It scans everything the browser can download
(web/.next/static) for each secret value in voice/.env and prints pass or fail per key
name. It never prints a value.
"""

import sys
from pathlib import Path

from dotenv import dotenv_values

from app.config import ENV_FILE

REPO = Path(__file__).resolve().parents[3]
STATIC = REPO / "web" / ".next" / "static"
SECRETS = ("DEEPGRAM_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "SUPABASE_SECRET_KEY", "DATABASE_URL")
MIN_LENGTH = 8  # shorter values would match by accident


def scan(root: Path, secrets: dict[str, str]) -> dict[str, list[str]]:
    """Files under root that contain each secret, by key name."""
    wanted = {name: value.strip() for name, value in secrets.items() if value and len(value.strip()) >= MIN_LENGTH}
    leaks: dict[str, list[str]] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for name, value in wanted.items():
            if value in text:
                leaks.setdefault(name, []).append(path.relative_to(root).as_posix())
    return leaks


def main() -> int:
    if not STATIC.exists():
        print(f"No build at {STATIC}. Run `npm run build` in web/ first.")
        return 2
    values = dotenv_values(ENV_FILE)
    secrets = {name: values.get(name) or "" for name in SECRETS}
    leaks = scan(STATIC, secrets)
    for name in SECRETS:
        if not secrets[name]:
            print(f"skip  {name} (not set)")
        elif name in leaks:
            print(f"FAIL  {name} found in {', '.join(leaks[name])}")
        else:
            print(f"pass  {name}")
    return 1 if leaks else 0


if __name__ == "__main__":
    sys.exit(main())
