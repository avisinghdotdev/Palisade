"""Keys handed out once to whoever created something, and kept only as a hash.

A conversation and a mandate each get one: the id alone is public or guessable from public
records, so holding the key is what lets a caller act on the thing.
"""

import hashlib
import secrets

KEY_BYTES = 24


def new_key() -> str:
    return secrets.token_urlsafe(KEY_BYTES)


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def key_matches(key: str | None, stored_hash: str) -> bool:
    """Whether the key is the one handed out, compared in constant time."""
    return key is not None and secrets.compare_digest(key_hash(key), stored_hash)
