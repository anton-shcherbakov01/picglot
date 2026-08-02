"""Identifier helpers.

Public identifiers are prefixed ULID-like strings: sortable by creation time,
opaque to clients, and impossible to confuse across entity types when they show
up in logs or support tickets (``prj_…`` vs ``job_…``).
"""

from __future__ import annotations

import os
import secrets
import time
import uuid

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32, no I/L/O/U


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def ulid() -> str:
    """26 character, lexicographically sortable, 80 bits of randomness."""
    timestamp = int(time.time() * 1000)
    return _encode(timestamp, 10) + _encode(int.from_bytes(os.urandom(10), "big"), 16)


def prefixed_id(prefix: str) -> str:
    return f"{prefix}_{ulid()}"


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def token(nbytes: int = 32) -> str:
    """URL-safe secret suitable for share links, magic links and API keys."""
    return secrets.token_urlsafe(nbytes)


def request_id() -> str:
    return f"req_{ulid()}"


PREFIXES = {
    "user": "usr",
    "session": "ses",
    "workspace": "wsp",
    "project": "prj",
    "asset": "ast",
    "page": "pag",
    "region": "reg",
    "job": "job",
    "export": "exp",
    "api_key": "key",
    "webhook": "whk",
    "delivery": "dlv",
    "share": "shr",
    "glossary": "gls",
    "payment": "pay",
    "subscription": "sub",
    "ledger": "led",
    "table": "tbl",
    "invite": "inv",
    "guest": "gst",
}


def make_id(entity: str) -> str:
    return prefixed_id(PREFIXES.get(entity, entity[:3]))
