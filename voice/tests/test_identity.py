import asyncio
from uuid import UUID

from app.voice.identity import Identity, resolve_identity

DEV_USER = UUID("6f1c2c1e-3d1a-4a52-9a43-0b7f2a1f9e10")
SIGNED_IN = UUID("0b8d1f4e-9c2a-4f6e-8a1d-3e5f7a9b1c2d")


class Auth:
    """Stands in for SupabaseAuth.user_for_token."""

    def __init__(self, users: dict[str, UUID]):
        self.users, self.asked = users, []

    async def user_for_token(self, token):
        self.asked.append(token)
        return self.users.get(token)


def resolve(token, host, dev_user=DEV_USER, auth=None):
    return asyncio.run(resolve_identity(token, host, dev_user, auth))


def test_dev_identity_is_accepted_only_from_loopback():
    assert resolve(None, "127.0.0.1") == Identity(DEV_USER, dev=True)
    assert resolve(None, "::1") == Identity(DEV_USER, dev=True)
    assert resolve(None, "192.168.1.20") is None
    assert resolve(None, None) is None


def test_no_dev_user_means_no_identity():
    assert resolve(None, "127.0.0.1", dev_user=None) is None


def test_a_token_beats_the_dev_identity():
    auth = Auth({"good-token": SIGNED_IN})
    assert resolve("good-token", "127.0.0.1", auth=auth) == Identity(SIGNED_IN, dev=False)
    assert resolve("junk", "127.0.0.1", auth=auth) is None  # a refused token never falls back to the dev identity


def test_the_dev_identity_still_works_from_loopback_without_a_token():
    auth = Auth({})
    assert resolve(None, "127.0.0.1", auth=auth) == Identity(DEV_USER, dev=True)
    assert auth.asked == []


def test_without_an_auth_client_a_token_resolves_to_nobody():
    assert resolve("eyJhbGciOi...", "127.0.0.1") is None
