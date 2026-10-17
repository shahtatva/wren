from uuid import UUID

from app.voice.identity import resolve_identity

USER = UUID("6f1c2c1e-3d1a-4a52-9a43-0b7f2a1f9e10")


def test_dev_identity_is_accepted_only_from_loopback():
    assert resolve_identity(None, "127.0.0.1", USER) == USER
    assert resolve_identity(None, "::1", USER) == USER
    assert resolve_identity(None, "192.168.1.20", USER) is None
    assert resolve_identity(None, None, USER) is None


def test_no_dev_user_means_no_identity():
    assert resolve_identity(None, "127.0.0.1", None) is None


def test_tokens_are_not_trusted_until_real_sign_in_exists():
    assert resolve_identity("eyJhbGciOi...", "127.0.0.1", USER) is None
