"""Framework utilities for connector configuration.

Convention-derived paths and seed entries shared across connectors.
Implements decisions D4 and D5 from connector-configuration-lifecycle.md.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import platform
import secrets
import uuid
from pathlib import Path

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

# Magic header to distinguish encrypted files from legacy plaintext
_HEADER = b"FP_ENC\x01"


def credential_path(connector: str, account: str) -> Path:
    """Convention: ~/.config/footprinter/{connector}_{account}_token.json

    Returns the portable tilde-form path. Callers needing a filesystem path
    should call ``.expanduser()`` on the result.
    """
    return Path(f"~/.config/footprinter/{connector}_{account}_token.json")


def _salt_path() -> Path:
    """Path to the salt file used for token encryption key derivation."""
    return Path(os.path.expanduser("~/.config/footprinter/.token_salt"))


def _get_or_create_salt(salt_file: Path | None = None) -> bytes:
    """Read existing salt or generate a new 16-byte random salt."""
    path = salt_file or _salt_path()
    path = Path(os.path.expanduser(str(path)))
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_bytes(16)
    path.write_bytes(salt)
    return salt


def _derive_key(salt: bytes) -> bytes:
    """Derive a Fernet key from machine identity and salt.

    Uses PBKDF2-HMAC-SHA256 with machine identity (MAC address + hostname)
    as the password material. Returns a URL-safe base64-encoded 32-byte key
    suitable for Fernet.
    """
    machine_id = f"{uuid.getnode()}-{platform.node()}".encode()
    raw = hashlib.pbkdf2_hmac("sha256", machine_id, salt, 480_000)
    return base64.urlsafe_b64encode(raw)


def save_token(path: Path, data: str | bytes) -> None:
    """Encrypt *data* with Fernet and write the ciphertext to *path*."""
    path = Path(os.path.expanduser(str(path)))
    path.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(data, str):
        raw = data.encode()
    else:
        raw = data

    salt = _get_or_create_salt()
    key = _derive_key(salt)
    f = Fernet(key)
    encrypted = f.encrypt(raw)
    path.write_bytes(_HEADER + encrypted)


def load_token(path: Path) -> str | bytes | None:
    """Read and decrypt a token file.

    Returns ``None`` if the file does not exist. Reads legacy plaintext files
    transparently (files without the encryption header).
    """
    path = Path(os.path.expanduser(str(path)))
    if not path.exists():
        return None

    raw = path.read_bytes()

    if raw.startswith(_HEADER):
        salt = _get_or_create_salt()
        key = _derive_key(salt)
        f = Fernet(key)
        decrypted = f.decrypt(raw[len(_HEADER) :])
        # Return same type that was saved: try UTF-8, fall back to bytes
        try:
            return decrypted.decode()
        except UnicodeDecodeError:
            return decrypted

    # Legacy plaintext — return as string
    try:
        return raw.decode()
    except UnicodeDecodeError:
        return raw


def source_seed_entry(
    source_type: str,
    account: str,
    *,
    name: str | None = None,
    label: str | None = None,
) -> dict:
    """Build a source seed dict for config.

    Args:
        source_type: Seed source type (e.g. "remote").
        account: Account name.
        name: Override the default ``{source_type}_{account}`` name.
        label: Override the default ``{Source_type} ({account})`` label.
    """
    return {
        "name": name or f"{source_type}_{account}",
        "source_type": source_type,
        "account": account,
        "label": label or f"{source_type.title()} ({account})",
        "icon": "cloud",
        "enabled": True,
    }


def account_label(account: dict) -> str:
    """Return user-facing label for an account config entry.

    Falls back to the internal name if label is missing or empty.
    """
    return account.get("label") or account["name"]
