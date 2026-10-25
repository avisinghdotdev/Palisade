"""Ed25519 keys as the settings and the registry carry them: base64 of the raw 32 bytes."""

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

KEY_BYTES = 32


def private_key(seed: str) -> Ed25519PrivateKey:
    """The key for a base64 seed of 32 random bytes.

    Raises:
        ValueError: the seed is not base64 of exactly 32 bytes.
    """
    raw = base64.b64decode(seed, validate=True)
    if len(raw) != KEY_BYTES:
        raise ValueError(f"an Ed25519 seed is {KEY_BYTES} bytes, not {len(raw)}")
    return Ed25519PrivateKey.from_private_bytes(raw)


def public_key(encoded: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded, validate=True))


def encode_public_key(key: Ed25519PublicKey) -> str:
    return base64.b64encode(key.public_bytes_raw()).decode()
