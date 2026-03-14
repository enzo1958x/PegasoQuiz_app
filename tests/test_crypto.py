"""test_crypto.py — unit test per copyright_crypto.

Copertura:
  - xor_stream: simmetria, lunghezze limite, errori attesi
  - sha256_hex: formato output
  - normalize_manifest: ordine deterministico
  - manifest_mac / verify_manifest: verifica HMAC, tamper detection
  - assert_manifest_nonces_unique: nonce duplicati e univoci
"""
import hashlib
import hmac
import json
import os
import sys
import pytest

# Permette l'import senza installare il pacchetto
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Imposta chiavi di test prima dell'import del modulo
_TEST_ENC_KEY = "a" * 64   # 32 byte hex
_TEST_MAC_KEY = "b" * 64
os.environ.setdefault("QUIZNOVA_ENC_KEY", _TEST_ENC_KEY)
os.environ.setdefault("QUIZNOVA_MAC_KEY", _TEST_MAC_KEY)

import copyright_crypto as cc  # noqa: E402


# ── Fixture ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def sample_manifest():
    """Manifest minimale valido (senza MAC)."""
    return {
        "version": 1,
        "entries": [
            {
                "file": "beta.enc",
                "name": "Beta",
                "nonce": "02" * 16,
                "sha256_cipher": "cc" * 32,
                "sha256_plain": "dd" * 32,
            },
            {
                "file": "alpha.enc",
                "name": "Alpha",
                "nonce": "01" * 16,
                "sha256_cipher": "aa" * 32,
                "sha256_plain": "bb" * 32,
            },
        ],
    }


# ── xor_stream ─────────────────────────────────────────────────────────────────

class TestXorStream:
    KEY   = bytes.fromhex(_TEST_ENC_KEY)
    NONCE = bytes(16)

    def test_symmetry(self):
        """Cifrare due volte restituisce il testo originale."""
        plain = b"Hello, PegasoQuiz!"
        cipher = cc.xor_stream(plain, self.KEY, self.NONCE)
        assert cc.xor_stream(cipher, self.KEY, self.NONCE) == plain

    def test_empty_data(self):
        assert cc.xor_stream(b"", self.KEY, self.NONCE) == b""

    def test_length_preserved(self):
        data = os.urandom(1337)
        assert len(cc.xor_stream(data, self.KEY, self.NONCE)) == len(data)

    def test_different_nonces_differ(self):
        data = b"test data"
        c1 = cc.xor_stream(data, self.KEY, b"\x00" * 16)
        c2 = cc.xor_stream(data, self.KEY, b"\x01" * 16)
        assert c1 != c2

    def test_empty_nonce_raises(self):
        with pytest.raises(ValueError, match="nonce"):
            cc.xor_stream(b"data", self.KEY, b"")

    def test_wrong_key_length_raises(self):
        with pytest.raises(ValueError, match="32 byte"):
            cc.xor_stream(b"data", b"short_key", self.NONCE)

    def test_large_data(self):
        """Verifica correttezza oltre il primo blocco SHA-256 (>32 byte)."""
        data = os.urandom(500)
        cipher = cc.xor_stream(data, self.KEY, self.NONCE)
        assert cc.xor_stream(cipher, self.KEY, self.NONCE) == data


# ── sha256_hex ─────────────────────────────────────────────────────────────────

class TestSha256Hex:
    def test_format(self):
        result = cc.sha256_hex(b"")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_known_value(self):
        # SHA-256 di b"abc" è noto
        expected = hashlib.sha256(b"abc").hexdigest()
        assert cc.sha256_hex(b"abc") == expected


# ── normalize_manifest ─────────────────────────────────────────────────────────

class TestNormalizeManifest:
    def test_deterministic(self, sample_manifest):
        b1 = cc.normalize_manifest(sample_manifest)
        b2 = cc.normalize_manifest(sample_manifest)
        assert b1 == b2

    def test_entries_sorted_by_file(self, sample_manifest):
        data = json.loads(cc.normalize_manifest(sample_manifest))
        files = [e["file"] for e in data["entries"]]
        assert files == sorted(files)

    def test_order_independent(self, sample_manifest):
        """Invertire l'ordine delle entries non cambia il normalizzato."""
        import copy
        m2 = copy.deepcopy(sample_manifest)
        m2["entries"] = list(reversed(m2["entries"]))
        assert cc.normalize_manifest(sample_manifest) == cc.normalize_manifest(m2)

    def test_only_required_fields(self, sample_manifest):
        """Il normalizzato include solo i 5 campi canonici."""
        data = json.loads(cc.normalize_manifest(sample_manifest))
        for e in data["entries"]:
            assert set(e.keys()) == {"file", "name", "nonce", "sha256_cipher", "sha256_plain"}


# ── manifest_mac / verify_manifest ────────────────────────────────────────────

class TestManifestMac:
    def test_verify_valid(self, sample_manifest):
        sample_manifest["mac"] = cc.manifest_mac(sample_manifest)
        assert cc.verify_manifest(sample_manifest) is True

    def test_verify_tampered_entry(self, sample_manifest):
        sample_manifest["mac"] = cc.manifest_mac(sample_manifest)
        sample_manifest["entries"][0]["name"] = "Tampered"
        assert cc.verify_manifest(sample_manifest) is False

    def test_verify_missing_mac(self, sample_manifest):
        assert cc.verify_manifest(sample_manifest) is False

    def test_verify_wrong_mac(self, sample_manifest):
        sample_manifest["mac"] = "0" * 64
        assert cc.verify_manifest(sample_manifest) is False

    def test_verify_empty_mac(self, sample_manifest):
        sample_manifest["mac"] = ""
        assert cc.verify_manifest(sample_manifest) is False

    def test_mac_changes_on_version_bump(self, sample_manifest):
        mac1 = cc.manifest_mac(sample_manifest)
        sample_manifest["version"] = 2
        mac2 = cc.manifest_mac(sample_manifest)
        assert mac1 != mac2


# ── assert_manifest_nonces_unique ─────────────────────────────────────────────

class TestAssertNoncesUnique:
    def test_unique_nonces_pass(self, sample_manifest):
        cc.assert_manifest_nonces_unique(sample_manifest)   # non solleva

    def test_duplicate_nonce_raises(self, sample_manifest):
        sample_manifest["entries"][1]["nonce"] = sample_manifest["entries"][0]["nonce"]
        with pytest.raises(ValueError, match="Nonce duplicati"):
            cc.assert_manifest_nonces_unique(sample_manifest)

    def test_empty_entries_pass(self):
        cc.assert_manifest_nonces_unique({"version": 1, "entries": []})

    def test_single_entry_pass(self, sample_manifest):
        m = {"version": 1, "entries": [sample_manifest["entries"][0]]}
        cc.assert_manifest_nonces_unique(m)
