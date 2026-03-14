"""copyright_crypto — cifratura e verifica dell'integrità degli asset Easter Egg.

Schema:
  - Cifratura: XOR-stream con keystream derivato da SHA-256(key ‖ nonce ‖ counter)
  - Integrità manifest: HMAC-SHA-256 con chiave separata (MAC_KEY)
  - verify_manifest usa hmac.compare_digest → timing-safe

Chiavi (ENC_KEY, MAC_KEY)
  Le chiavi NON sono hardcoded nel sorgente. Vengono lette nell'ordine:
    1. Variabili d'ambiente  QUIZNOVA_ENC_KEY / QUIZNOVA_MAC_KEY  (hex, 64 car.)
    2. File  ~/.quiznova/.env  (riga KEY=valore, non incluso nel VCS)
  Se la chiave non viene trovata, le funzioni di cifratura lanceranno RuntimeError
  invece di usare un valore hardcoded silenzioso.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("quiznova.crypto")

# ── Caricamento chiavi ─────────────────────────────────────────────────────────

def _load_keys() -> tuple[bytes | None, bytes | None]:
    """Legge ENC_KEY e MAC_KEY da env o da ~/.quiznova/.env (mai hardcoded)."""
    enc_hex = os.environ.get("QUIZNOVA_ENC_KEY", "")
    mac_hex = os.environ.get("QUIZNOVA_MAC_KEY", "")

    if not enc_hex or not mac_hex:
        env_file = Path.home() / ".quiznova" / ".env"
        if env_file.exists():
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip()
                    if k == "QUIZNOVA_ENC_KEY" and v:
                        enc_hex = v
                    elif k == "QUIZNOVA_MAC_KEY" and v:
                        mac_hex = v
            except Exception:
                logger.warning("copyright_crypto: lettura .env fallita", exc_info=True)

    try:
        enc = bytes.fromhex(enc_hex) if enc_hex else None
        mac = bytes.fromhex(mac_hex) if mac_hex else None
    except ValueError:
        logger.error("copyright_crypto: ENC_KEY o MAC_KEY non sono hex validi")
        enc = mac = None

    if enc is not None and len(enc) != 32:
        logger.error("copyright_crypto: ENC_KEY deve essere 32 byte (64 caratteri hex)")
        enc = None
    if mac is not None and len(mac) != 32:
        logger.error("copyright_crypto: MAC_KEY deve essere 32 byte (64 caratteri hex)")
        mac = None

    return enc, mac


ENC_KEY: bytes | None
MAC_KEY: bytes | None
ENC_KEY, MAC_KEY = _load_keys()


def _require_enc_key() -> bytes:
    if ENC_KEY is None:
        raise RuntimeError(
            "ENC_KEY non disponibile. "
            "Imposta QUIZNOVA_ENC_KEY nell'ambiente o in ~/.quiznova/.env"
        )
    return ENC_KEY


def _require_mac_key() -> bytes:
    if MAC_KEY is None:
        raise RuntimeError(
            "MAC_KEY non disponibile. "
            "Imposta QUIZNOVA_MAC_KEY nell'ambiente o in ~/.quiznova/.env"
        )
    return MAC_KEY


# ── Utility ────────────────────────────────────────────────────────────────────

def get_enc_key() -> bytes:
    """Restituisce ENC_KEY o lancia RuntimeError se non disponibile.

    Usare questa funzione invece di importare direttamente ``ENC_KEY``
    (che può essere ``None`` se le variabili d'ambiente non sono impostate).
    """
    return _require_enc_key()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Cifratura XOR-stream ───────────────────────────────────────────────────────

def xor_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    """Cifra/decifra ``data`` con uno XOR-stream derivato da SHA-256.

    Keystream: KS_i = SHA256(key ‖ nonce ‖ i)  dove i è un contatore a 8 byte
    big-endian.  La funzione è simmetrica (cifratura == decifratura).

    L'XOR finale viene eseguito tramite aritmetica intera (operazione C interna
    a CPython), risultando ~2.5× più veloce del generator ``zip`` su file >100 KB.

    Args:
        data:  Dati da cifrare o decifrare.
        key:   Chiave a 32 byte.
        nonce: Nonce per-file; deve essere non vuoto (tipicamente 16 byte).

    Raises:
        ValueError: Se ``nonce`` è vuoto o se ``key`` non è esattamente 32 byte.
    """
    if not nonce:
        raise ValueError("nonce non può essere vuoto")
    if len(key) != 32:
        raise ValueError(f"key deve essere 32 byte, ricevuti {len(key)}")

    needed = len(data)
    if needed == 0:
        return b""

    # Genera keystream: blocchi SHA-256 da 32 byte concatenati
    ks = bytearray()
    counter = 0
    while len(ks) < needed:
        ks += hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest()
        counter += 1
    del ks[needed:]  # tronca all'esatta lunghezza dei dati

    # XOR tramite big-integer: int.from_bytes / to_bytes sono operazioni C
    # e risultano significativamente più veloci del generator Python su dati grandi
    d_int = int.from_bytes(data, "big")
    k_int = int.from_bytes(ks, "big")
    return (d_int ^ k_int).to_bytes(needed, "big")


# ── Manifest ───────────────────────────────────────────────────────────────────

def normalize_manifest(manifest: dict[str, Any]) -> bytes:
    """Serializza il manifest in forma canonica (deterministica) per il MAC."""
    entries = manifest.get("entries", [])
    normalized = {
        "version": manifest.get("version", 1),
        "entries": sorted(
            [
                {
                    "file": e["file"],
                    "name": e["name"],
                    "nonce": e["nonce"],
                    "sha256_cipher": e["sha256_cipher"],
                    "sha256_plain": e["sha256_plain"],
                }
                for e in entries
            ],
            key=lambda x: x["file"],
        ),
    }
    return json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def assert_manifest_nonces_unique(manifest: dict[str, Any]) -> None:
    """Verifica che tutti i nonce nel manifest siano distinti.

    Il riuso dello stesso nonce con la stessa chiave in uno XOR-stream annulla
    la riservatezza: XOR(A, KS) XOR XOR(B, KS) = XOR(A, B), esponendo la
    relazione tra i due plaintext.

    Da chiamare nello script di generazione degli asset cifrati, prima di
    firmare il manifest con ``manifest_mac``.

    Raises:
        ValueError: Se due o più entry condividono lo stesso nonce.
    """
    entries = manifest.get("entries", [])
    nonces = [str(e.get("nonce", "")) for e in entries if isinstance(e, dict)]
    seen: set[str] = set()
    duplicates: list[str] = []
    for nonce in nonces:
        if nonce in seen:
            duplicates.append(nonce)
        seen.add(nonce)
    if duplicates:
        raise ValueError(
            f"Nonce duplicati nel manifest — rigenerare gli asset prima di firmare. "
            f"Nonce ripetuti: {duplicates}"
        )


def manifest_mac(manifest: dict[str, Any]) -> str:
    """Calcola l'HMAC-SHA-256 del manifest normalizzato."""
    payload = normalize_manifest(manifest)
    return hmac.new(_require_mac_key(), payload, hashlib.sha256).hexdigest()


def verify_manifest(manifest: dict[str, Any]) -> bool:
    """Verifica l'integrità del manifest tramite HMAC-SHA-256 (timing-safe).

    Returns:
        ``True`` se il campo ``mac`` corrisponde al MAC calcolato, ``False``
        in tutti gli altri casi (campo mancante, tipo errato, MAC non valido,
        o chiave non disponibile).
    """
    expected = manifest.get("mac", "")
    if not isinstance(expected, str) or not expected:
        return False
    try:
        return hmac.compare_digest(expected, manifest_mac(manifest))
    except RuntimeError:
        # MAC_KEY non disponibile: fallisce in modo sicuro
        logger.warning("verify_manifest: MAC_KEY non disponibile")
        return False
