"""Check that no key from voice/.env made it into the web build: python -m app.tools.leak_check

Run it after `npm run build` in web/. It scans the whole build (web/.next) except its cache:
the static files the browser downloads, the prerendered pages and RSC payloads the server
sends, and the server bundles. web/ holds no secrets at all, so a key anywhere in its build
is a leak. It prints pass or fail per key name and never prints a value.
"""

import sys
from pathlib import Path

from dotenv import dotenv_values

from app.config import ENV_FILE

REPO = Path(__file__).resolve().parents[3]
BUILD = REPO / "web" / ".next"
SKIP = ("cache",)  # build caches never reach a browser, and they're large
SECRETS = ("DEEPGRAM_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "SUPABASE_SECRET_KEY", "DATABASE_URL")
MIN_LENGTH = 8  # shorter values would match by accident


def scan(root: Path, secrets: dict[str, str], skip: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """Files under root that contain each secret, by key name. skip names top-level folders to leave out."""
    wanted = {name: value.strip() for name, value in secrets.items() if value and len(value.strip()) >= MIN_LENGTH}
    leaks: dict[str, list[str]] = {}
    tops = [top for top in root.iterdir() if top.name not in skip]
    files = [top for top in tops if top.is_file()] + [p for top in tops if top.is_dir() for p in top.rglob("*") if p.is_file()]
    for path in sorted(files):
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for name, value in wanted.items():
            if value in text:
                leaks.setdefault(name, []).append(path.relative_to(root).as_posix())
    return leaks


def main() -> int:
    if not (BUILD / "static").exists():
        print(f"No build at {BUILD}. Run `npm run build` in web/ first.")
        return 2
    values = dotenv_values(ENV_FILE)
    secrets = {name: values.get(name) or "" for name in SECRETS}
    leaks = scan(BUILD, secrets, skip=SKIP)
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
