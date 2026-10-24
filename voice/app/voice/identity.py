"""Who is on the other end of a connection.

A sign-in token decides when there is one: SupabaseAuth checks it with Supabase, and a
token it refuses means nobody, never the dev identity. Without a token, the local
development identity applies: WREN_DEV_USER_ID, accepted only from this machine. It skips
the age check, because it only exists on the owner's machine. A deploy must leave it unset
(see the design doc, "Keys and access").
"""

import ipaddress
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


class TokenChecker(Protocol):
    async def user_for_token(self, token: str) -> UUID | None: ...


@dataclass(frozen=True)
class Identity:
    user_id: UUID
    dev: bool  # the loopback dev identity, only on the owner's machine


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


async def resolve_identity(
    token: str | None, client_host: str | None, dev_user_id: UUID | None, auth: TokenChecker | None,
) -> Identity | None:
    """Raises Throttled when token lookups are over their cap."""
    if token is not None:
        user = await auth.user_for_token(token) if auth is not None else None
        return Identity(user, dev=False) if user is not None else None
    if dev_user_id is not None and is_loopback(client_host):
        return Identity(dev_user_id, dev=True)
    return None
