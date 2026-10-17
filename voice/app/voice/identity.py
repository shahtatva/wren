"""Who is on the other end of a connection.

Real sign-in doesn't exist yet, so the only identity is the local development one:
WREN_DEV_USER_ID, accepted only from this machine. A token is never trusted until there
is code that verifies it. Deploying voice/ needs that sign-in first (see the design doc,
"Keys and access").
"""

import ipaddress
from uuid import UUID


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def resolve_identity(token: str | None, client_host: str | None, dev_user_id: UUID | None) -> UUID | None:
    if token is not None:
        return None
    if dev_user_id is not None and is_loopback(client_host):
        return dev_user_id
    return None
