"""
Webhook token encryption and hashing utilities.

Tokens are stored in two forms:
- webhook_token:       Fernet-encrypted plaintext (for one-time display to the user)
- webhook_token_hash:  SHA-256 hash (for SQL lookups — cannot reverse, only compare)

The Fernet key is deterministically derived from settings.secret_key so that
encrypted tokens survive application restarts without a separate key management system.
"""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from config import settings

logger = logging.getLogger("wolfhost.webhook_crypto")

_fernet_key: bytes = base64.urlsafe_b64encode(
    hashlib.sha256(f"wolfhost-webhook:{settings.secret_key}".encode()).digest()
)
_fernet = Fernet(_fernet_key)


def encrypt_token(plaintext: str) -> str:
    """Encrypt a webhook token for storage. Returns URL-safe base64 ciphertext."""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str | None:
    """Decrypt a stored webhook token. Returns None if decryption fails."""
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception) as exc:
        logger.warning(f"Failed to decrypt webhook token: {exc}")
        return None


def hash_token(plaintext: str) -> str:
    """Produce a deterministic SHA-256 hash of a webhook token for SQL lookups."""
    return hashlib.sha256(f"wolfhost-wh:{plaintext}".encode()).hexdigest()
