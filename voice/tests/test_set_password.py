import asyncio
import json
from types import SimpleNamespace
from uuid import UUID

from app.tools.set_password import main, set_password
from tests.supabase_fakes import EMAIL, KEY, URL, USER, FakeSupabase, client_for, user_body

OTHER = UUID("0b8d1f4e-9c2a-4f6e-8a1d-3e5f7a9b1c2d")
NEW_PASSWORD = "a long new password"


def admin_api(*users: dict) -> FakeSupabase:
    """GET /auth/v1/admin/users lists `users` on one page; PUT /auth/v1/admin/users/{id} updates one."""

    def route(request):
        if request.method == "GET" and request.url.path == "/auth/v1/admin/users":
            return 200, {"aud": "authenticated", "users": list(users)}
        if request.method == "PUT" and request.url.path.startswith("/auth/v1/admin/users/"):
            return 200, user_body(UUID(request.url.path.rsplit("/", 1)[1]))
        return 404, {"code": 404, "error_code": "not_found", "msg": "Not found"}

    return FakeSupabase(route=route)


def test_set_password_finds_the_user_and_sets_it():
    supabase = admin_api(user_body(OTHER, "dev@example.com"), user_body(USER, EMAIL))
    assert asyncio.run(set_password(URL, KEY, "Maya@Example.com ", NEW_PASSWORD, client_for(supabase)))
    (put,) = [r for r in supabase.requests if r.method == "PUT"]
    assert put.url.path == f"/auth/v1/admin/users/{USER}" and json.loads(put.content) == {"password": NEW_PASSWORD}
    assert put.headers["apikey"] == KEY and "authorization" not in put.headers


def test_set_password_for_an_unknown_email_changes_nothing():
    supabase = admin_api(user_body(OTHER, "dev@example.com"))
    assert not asyncio.run(set_password(URL, KEY, EMAIL, NEW_PASSWORD, client_for(supabase)))
    assert [r.method for r in supabase.requests] == ["GET"]


def test_set_password_never_prints_the_password(capsys):
    supabase = admin_api(user_body(USER, EMAIL))
    settings = SimpleNamespace(supabase_url=f"{URL}/rest/v1/", supabase_secret_key=KEY)
    code = main([EMAIL], ask=lambda prompt: NEW_PASSWORD, settings=settings, client=client_for(supabase))
    out = capsys.readouterr()
    assert code == 0 and out.out.strip()  # it says that it worked
    assert NEW_PASSWORD not in out.out + out.err


def test_two_different_passwords_change_nothing(capsys):
    supabase = admin_api(user_body(USER, EMAIL))
    answers = iter([NEW_PASSWORD, "a different one"])
    settings = SimpleNamespace(supabase_url=URL, supabase_secret_key=KEY)
    code = main([EMAIL], ask=lambda prompt: next(answers), settings=settings, client=client_for(supabase))
    assert code == 1 and supabase.requests == []
    assert NEW_PASSWORD not in capsys.readouterr().out


def test_the_hidden_prompt_says_nothing_shows_while_typing(capsys):
    supabase = admin_api(user_body(USER, EMAIL))
    settings = SimpleNamespace(supabase_url=URL, supabase_secret_key=KEY)
    main([EMAIL], ask=lambda prompt: NEW_PASSWORD, settings=settings, client=client_for(supabase), terminal=True)
    assert "Nothing shows as you type" in capsys.readouterr().out


def test_a_console_that_cant_hide_typing_reads_the_password_where_it_shows(capsys, monkeypatch):
    supabase = admin_api(user_body(USER, EMAIL))
    settings = SimpleNamespace(supabase_url=URL, supabase_secret_key=KEY)
    monkeypatch.setattr("builtins.input", lambda prompt: NEW_PASSWORD)
    code = main([EMAIL], settings=settings, client=client_for(supabase), terminal=False)
    assert code == 0 and [r.method for r in supabase.requests] == ["GET", "PUT"]
    assert "will show" in capsys.readouterr().out


def test_visible_reads_the_password_where_it_shows(monkeypatch):
    supabase = admin_api(user_body(USER, EMAIL))
    settings = SimpleNamespace(supabase_url=URL, supabase_secret_key=KEY)
    monkeypatch.setattr("builtins.input", lambda prompt: NEW_PASSWORD)
    assert main([EMAIL, "--visible"], settings=settings, client=client_for(supabase), terminal=True) == 0
