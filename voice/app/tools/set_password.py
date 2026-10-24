"""Give an account a password, with no email: the owner's tool for sign-in.

    python -m app.tools.set_password you@example.com [--visible]

New Free Supabase projects can't edit their email templates, so Wren signs people in with a
password the owner sets (sign-in plan, revised 2026-10-24). This finds the account through
the Auth admin API and sets its password. It reads the password twice without showing it,
and prints only whether it worked. It uses SUPABASE_URL and SUPABASE_SECRET_KEY from
voice/.env; the secret key goes only in the apikey header.

Hidden typing needs a real console. Where there isn't one (some IDE consoles), or with
--visible, the password shows as it's typed instead.
"""

import argparse
import asyncio
import getpass
import sys
from uuid import UUID

import httpx

from app.auth import project_url
from app.config import load_settings

PAGE = 100


async def _find(http: httpx.AsyncClient, base: str, key: str, email: str) -> UUID | None:
    page = 1
    while True:
        response = await http.get(
            f"{base}/auth/v1/admin/users", params={"page": page, "per_page": PAGE}, headers={"apikey": key},
        )
        response.raise_for_status()
        users = response.json().get("users", [])
        for user in users:
            if (user.get("email") or "").lower() == email:
                return UUID(user["id"])
        if len(users) < PAGE:
            return None
        page += 1


async def set_password(url: str, key: str, email: str, password: str, client: httpx.AsyncClient | None = None) -> bool:
    """False when no account has that email; nothing changes then."""
    base, http = project_url(url), client or httpx.AsyncClient(timeout=15)
    try:
        user = await _find(http, base, key, email.strip().lower())
        if user is None:
            return False
        response = await http.put(f"{base}/auth/v1/admin/users/{user}", headers={"apikey": key}, json={"password": password})
        response.raise_for_status()
        return True
    finally:
        if client is None:
            await http.aclose()


def _console() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def main(argv: list[str] | None = None, *, ask=None, settings=None, client=None, terminal: bool | None = None) -> int:
    parser = argparse.ArgumentParser(description="Give a Wren account a password, with no email.")
    parser.add_argument("email")
    parser.add_argument("--visible", action="store_true", help="show the password as you type it")
    args = parser.parse_args(argv)
    # getpass hides typing, but only in a real console. Elsewhere it waits for keys that never arrive.
    hidden = not args.visible and (_console() if terminal is None else terminal)
    print("Nothing shows as you type. Type the password, then press Enter." if hidden else "The password will show as you type it.")
    ask = ask or (getpass.getpass if hidden else input)
    password = ask("New password: ")
    if not password:
        print("No password given. Nothing changed.")
        return 1
    if ask("Type it again: ") != password:
        print("The two passwords don't match. Nothing changed.")
        return 1
    settings = settings or load_settings()
    try:
        done = asyncio.run(set_password(settings.supabase_url, settings.supabase_secret_key, args.email, password, client))
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 422:
            print("Supabase refused that password, likely as too short or too weak. Nothing changed.")
        else:
            print(f"Supabase answered {e.response.status_code}. Nothing changed.")
        return 1
    except httpx.HTTPError as e:
        print(f"Couldn't reach Supabase ({type(e).__name__}). Nothing changed.")
        return 1
    print("Done. That account can sign in with the new password." if done else "No account has that email. Nothing changed.")
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
