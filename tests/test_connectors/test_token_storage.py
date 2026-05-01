"""Tests for save_token() / load_token() encrypted token storage."""


# ---------------------------------------------------------------------------
# RED 1 — Round-trip: string
# ---------------------------------------------------------------------------


class TestTokenStorageRoundTrip:
    def test_round_trip_string(self, tmp_path):
        from footprinter.connectors.config_utils import load_token, save_token

        token_path = tmp_path / "token.json"
        data = '{"access_token": "ya29.abc", "refresh_token": "1//xyz"}'

        save_token(token_path, data)
        result = load_token(token_path)

        assert result == data

    # RED 2 — Round-trip: bytes
    def test_round_trip_bytes(self, tmp_path):
        from footprinter.connectors.config_utils import load_token, save_token

        token_path = tmp_path / "token.bin"
        data = b"\x00\x01\x02binary-token-data\xff\xfe"

        save_token(token_path, data)
        result = load_token(token_path)

        assert result == data


# ---------------------------------------------------------------------------
# RED 3 — Missing file returns None
# ---------------------------------------------------------------------------


class TestTokenStorageMissingFile:
    def test_load_missing_file_returns_none(self, tmp_path):
        from footprinter.connectors.config_utils import load_token

        result = load_token(tmp_path / "nonexistent.json")
        assert result is None


# ---------------------------------------------------------------------------
# RED 4 — File is encrypted on disk
# ---------------------------------------------------------------------------


class TestTokenStorageEncryption:
    def test_file_is_encrypted_on_disk(self, tmp_path):
        from footprinter.connectors.config_utils import save_token

        token_path = tmp_path / "token.json"
        plaintext = "super-secret-access-token-value"

        save_token(token_path, plaintext)

        raw = token_path.read_bytes()
        assert plaintext.encode() not in raw
        assert raw.startswith(b"FP_ENC\x01")


# ---------------------------------------------------------------------------
# RED 5 — Legacy plaintext read
# ---------------------------------------------------------------------------


class TestTokenStorageLegacy:
    def test_reads_legacy_plaintext_transparently(self, tmp_path):
        from footprinter.connectors.config_utils import load_token

        token_path = tmp_path / "legacy_token.json"
        legacy_content = '{"token": "old-format"}'
        token_path.write_text(legacy_content)

        result = load_token(token_path)
        assert result == legacy_content


# ---------------------------------------------------------------------------
# RED 6 — Always encrypts (cryptography is a base dep)
# ---------------------------------------------------------------------------


class TestTokenStorageAlwaysEncrypts:
    def test_no_plaintext_fallback(self, tmp_path):
        """cryptography is a base dep — save_token always encrypts."""
        from footprinter.connectors.config_utils import save_token

        token_path = tmp_path / "token.json"
        data = "should-be-encrypted"

        save_token(token_path, data)

        raw = token_path.read_bytes()
        assert raw.startswith(b"FP_ENC\x01"), "File should be encrypted"
        assert data.encode() not in raw, "Plaintext should not appear in file"


# ---------------------------------------------------------------------------
# RED 7 — Salt file created on first use
# ---------------------------------------------------------------------------


class TestTokenStorageSalt:
    def test_salt_file_created(self, tmp_path):
        from unittest.mock import patch

        from footprinter.connectors.config_utils import save_token

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        salt_path = config_dir / ".token_salt"
        token_path = config_dir / "test_token.json"

        with patch("footprinter.connectors.config_utils._salt_path", return_value=salt_path):
            save_token(token_path, "test-data")

        assert salt_path.exists()
        assert len(salt_path.read_bytes()) == 16


# ---------------------------------------------------------------------------
# RED 8 — Key derivation is deterministic
# ---------------------------------------------------------------------------


class TestTokenStorageKeyDerivation:
    def test_key_derivation_deterministic(self):
        from footprinter.connectors.config_utils import _derive_key

        salt = b"0123456789abcdef"
        key1 = _derive_key(salt)
        key2 = _derive_key(salt)
        assert key1 == key2
        assert len(key1) == 44  # base64-encoded Fernet key length
