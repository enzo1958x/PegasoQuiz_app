from __future__ import annotations

import hashlib
import html as html_lib
import json
import logging
import os
import random
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import ssl
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


APP_ROOT = Path.home() / ".quiznova"
APP_ROOT.mkdir(parents=True, exist_ok=True)
STATE_FILE = APP_ROOT / "state.json"

logger = logging.getLogger("quiznova.backend")

# ── Credenziali: lette da variabili d'ambiente o da ~/.quiznova/.env.
# Mai hardcoded nel sorgente. Imposta QUIZNOVA_SUPABASE_URL e
# QUIZNOVA_SUPABASE_ANON_KEY nell'ambiente oppure in ~/.quiznova/.env
# (non incluso nel VCS).
SUPABASE_URL: str = os.environ.get("QUIZNOVA_SUPABASE_URL", "")
SUPABASE_ANON_KEY: str = os.environ.get("QUIZNOVA_SUPABASE_ANON_KEY", "")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    # Fallback: prova a leggere da file .env nella home dell'app (non in repo)
    _env_file = APP_ROOT / ".env"
    if _env_file.exists():
        try:
            for _line in _env_file.read_text(encoding="utf-8").splitlines():
                _k, _, _v = _line.partition("=")
                _k, _v = _k.strip(), _v.strip()
                if _k == "QUIZNOVA_SUPABASE_ANON_KEY" and _v:
                    SUPABASE_ANON_KEY = _v
                elif _k == "QUIZNOVA_SUPABASE_URL" and _v:
                    SUPABASE_URL = _v
        except Exception:
            logger.warning("Lettura .env fallita", exc_info=True)

if not SUPABASE_URL:
    logger.warning(
        "QUIZNOVA_SUPABASE_URL non configurato: le funzionalità cloud "
        "non saranno disponibili. Imposta la variabile d'ambiente o "
        "aggiungila a ~/.quiznova/.env"
    )

DEFAULT_GITHUB_MANIFEST_URL = "https://raw.githubusercontent.com/enzo1958x/quiztest/main/JSON/manifest_full.json"

@dataclass
class QuizItem:
    id: str
    question: str
    choices: list[str]
    correct_index: int
    chapter: str
    explanation: str = ""
    image_url:   str = ""


# =============================================================================
# _StorageManager — persistenza locale (state.json, prefs)
# =============================================================================

class _StorageManager:
    """Gestisce la lettura/scrittura di state.json e le preferenze utente.

    Tutte le scritture passano per _save_state() che è protetta da un lock
    di threading per evitare race condition su write concorrenti.
    """

    # ── inizializzazione ──────────────────────────────────────────────────────

    def _init_storage(self) -> None:
        """Da chiamare in __init__ della classe figlia."""
        self._state: dict[str, Any] = self._load_state()
        self._state_lock = threading.Lock()

    # ── lettura / scrittura ───────────────────────────────────────────────────

    def _load_state(self) -> dict[str, Any]:
        if not STATE_FILE.exists():
            return {"used": {}, "stats": [], "profiles": {}, "prefs": {}, "wrong": {}}
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("used", {})
                data.setdefault("stats", [])
                data.setdefault("prefs", {})
                data.setdefault("profiles", {})
                data.setdefault("wrong", {})
                return data
        except Exception:
            logger.warning("_load_state: lettura state.json fallita", exc_info=True)
        return {"used": {}, "stats": [], "profiles": {}, "prefs": {}, "wrong": {}}

    def _save_state(self) -> None:
        with self._state_lock:
            STATE_FILE.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    # ── preferenze ───────────────────────────────────────────────────────────

    def get_pref(self, key: str, default: str = "") -> str:
        return str(self._state.get("prefs", {}).get(key, default) or default)

    def set_pref(self, key: str, value: str) -> None:
        prefs = self._state.setdefault("prefs", {})
        prefs[str(key)] = str(value or "")
        self._save_state()


# =============================================================================
# _NetMixin — connessioni HTTP con gestione SSL
# =============================================================================

class _NetMixin:
    """Fornisce _urlopen_with_ssl_fallback per tutte le operazioni di rete.

    Strategia SSL (in ordine):
      1. Bundle CA di sistema (default).
      2. Bundle CA certifi (incluso nel pacchetto).
      3. Errore esplicito — mai ssl._create_unverified_context().
    """

    def _urlopen_with_ssl_fallback(
        self, req: urllib.request.Request, timeout: int = 30
    ):
        def _is_ssl_error(exc: Exception) -> bool:
            if isinstance(exc, ssl.SSLCertVerificationError):
                return True
            msg = str(exc)
            if "CERTIFICATE_VERIFY_FAILED" in msg:
                return True
            if isinstance(exc, urllib.error.URLError):
                return "CERTIFICATE_VERIFY_FAILED" in str(getattr(exc, "reason", ""))
            return False

        # Tentativo 1: SSL di sistema
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except Exception as e:
            if not _is_ssl_error(e):
                raise

        # Tentativo 2: bundle CA certifi
        try:
            import certifi  # type: ignore
            ctx = ssl.create_default_context(cafile=certifi.where())
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        except ImportError:
            logger.debug("certifi non disponibile")
        except Exception as e:
            if not _is_ssl_error(e):
                raise

        raise ssl.SSLCertVerificationError(
            "Verifica del certificato SSL fallita con entrambi i bundle CA "
            "(sistema e certifi). Aggiorna i certificati di sistema oppure "
            "reinstalla l'applicazione per aggiornare il bundle CA incluso."
        )


# =============================================================================
# _CloudMixin — integrazione Supabase (auth, REST, entries, stats, profiles)
# =============================================================================

class _CloudMixin(_NetMixin):
    """Tutto il codice che parla con Supabase.

    Dipende da _StorageManager (usa self._state, self.get_pref, self.set_pref)
    e da _NetMixin (usa self._urlopen_with_ssl_fallback).
    Non dipende dalla logica quiz.
    """

    def _cloud_state_enabled(self) -> bool:
        uid = str((self.current_user or {}).get("id") or "").strip()
        tok = str(self.auth_access_token or "").strip()
        return self.current_source_mode == "cloud" and bool(uid and tok)

    def _cloud_user_enabled(self) -> bool:
        """True se l'utente è autenticato, indipendentemente dalla sorgente del dataset."""
        uid = str((self.current_user or {}).get("id") or "").strip()
        tok = str(self.auth_access_token or "").strip()
        return bool(uid and tok)

    def _wrong_read(self) -> list[dict[str, Any]]:
        """Legge il bacino errori: cloud con fallback locale."""
        if self._cloud_user_enabled():
            try:
                return self._wrong_cloud_fetch()
            except Exception:
                logger.warning("_wrong_read: cloud fallito, uso locale", exc_info=True)
        return self._wrong_local_get()

    def _wrong_write(self, rows: list[dict[str, Any]]) -> None:
        """Scrive il bacino errori: cloud con fallback locale."""
        if self._cloud_user_enabled():
            try:
                self._wrong_cloud_set(rows)
                return
            except Exception:
                logger.warning("_wrong_write: cloud fallito, scrivo in locale", exc_info=True)
        self._wrong_local_set(rows)

    def _supabase_rest(
        self,
        table: str,
        method: str = "GET",
        query: dict[str, str] | None = None,
        payload: Any | None = None,
        prefer: str | None = None,
    ) -> Any:
        uid = str((self.current_user or {}).get("id") or "").strip()
        tok = str(self.auth_access_token or "").strip()
        if not uid or not tok:
            raise RuntimeError("Sessione Supabase non valida")
        endpoint = f"{SUPABASE_URL}/rest/v1/{table}"
        if query:
            endpoint += "?" + urllib.parse.urlencode(query, doseq=True, safe="(),.*:|")
        headers = {
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(endpoint, data=body, headers=headers, method=method.upper())
        try:
            with self._urlopen_with_ssl_fallback(req, timeout=30) as r:
                raw = r.read().decode("utf-8", errors="replace").strip()
        except urllib.error.HTTPError as e:
            err_raw = e.read().decode("utf-8", errors="replace")
            try:
                obj = json.loads(err_raw)
                msg = obj.get("message") or obj.get("error") or obj.get("hint") or err_raw
            except Exception:
                msg = err_raw or str(e)
            raise RuntimeError(f"Supabase {table} {method} fallito: {msg}") from e
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return raw


# =============================================================================
# _QuizEngine — logica quiz (parse, generate, correct, pool, stats locali)
# =============================================================================

class _QuizEngine:
    """Logica di business pura: parsing item, generazione quiz, correzione,
    gestione pool usati, statistiche locali.

    Non dipende da Qt né da rete. Dipende da _StorageManager tramite
    self._state e self._save_state().
    """

    # ── utils id/hash ─────────────────────────────────────────────────────────

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    def _qid(self, it: QuizItem) -> str:
        if it.id and not re.match(r"^Q\d+$", it.id):
            return it.id
        return "H" + self._hash(it.question.strip().lower())

    @staticmethod
    def _norm_stats_name(value: str) -> str:
        txt = str(value or "").strip().lower()
        txt = re.sub(r"\s+", " ", txt)
        txt = Path(txt).stem
        return txt

    @staticmethod
    def _chapter_sort_key(name: str) -> tuple[int, str]:
        txt = str(name or "").strip()
        m = re.search(r"(?:lezione|lesson)\s*(\d+)", txt, re.IGNORECASE)
        if m:
            try:
                return (int(m.group(1)), txt.lower())
            except Exception:
                pass
        m2 = re.search(r"(\d+)", txt)
        if m2:
            try:
                return (int(m2.group(1)), txt.lower())
            except Exception:
                pass
        return (10**9, txt.lower())


# =============================================================================
# QuizNovaBackend — facade pubblica
# Eredita da tutti i mixin; coordina stato e delega alle classi interne.
# =============================================================================

class QuizNovaBackend(_StorageManager, _CloudMixin, _QuizEngine):
    def __init__(self) -> None:
        # ── storage (state.json, prefs, lock) ────────────────────────────────
        self._init_storage()   # da _StorageManager: imposta _state e _state_lock

        # ── dataset ───────────────────────────────────────────────────────────
        self.raw_items: list[dict[str, Any]] = []
        self.all_items: list[QuizItem] = []
        self.current_items: list[QuizItem] = []
        self.dataset_name: str = ""
        self.selected_chapters: list[str] = []
        self.last_pool_total: int = 0
        self.last_pool_used: int = 0

        # ── cloud / auth ──────────────────────────────────────────────────────
        self.auth_access_token: str = ""
        self.current_user: dict[str, Any] | None = None
        self.cloud_entries: list[tuple[str, str]] = []
        self.current_source_mode: str = "local"
        self._cloud_stats_cache: list[dict[str, Any]] = []
        self._cloud_stats_dirty: bool = True
        self._cloud_stats_for_json: str = ""

        # ── percent / profili ─────────────────────────────────────────────────
        self.use_percent_mode: bool = False
        self.percent_map: dict[str, float] = {}

        # ── sorgente corrente ─────────────────────────────────────────────────
        self.current_json_file_path: Path | None = None
        self.current_json_url: str = ""
        self._pdf_cache: dict[str, Path] = {}
        self._pdf_dialog = None

        # ── modalità errori ───────────────────────────────────────────────────
        self._wrong_count_cache: int = -1
        self._wrong_count_dirty: bool = True
        self.base_raw_items: list[dict[str, Any]] = []
        self.base_all_items: list[QuizItem] = []
        self.base_dataset_name: str = ""
        self.base_source_mode: str = "local"
        self.base_json_file_path: Path | None = None
        self.base_json_url: str = ""
        self.is_wrong_mode: bool = False

        # ── indice lookup O(1) ────────────────────────────────────────────────
        self._item_index: dict[str, "QuizItem"] = {}

    def _rebuild_item_index(self) -> None:
        """Ricostruisce l'indice di ricerca rapida dopo ogni cambio di dataset."""
        idx: dict[str, "QuizItem"] = {}
        items = list(self.base_all_items or []) + list(self.all_items or [])
        seen: set[str] = set()
        for it in items:
            for key in (
                str(it.id or "").strip(),
                str(it.id or "").strip().lower(),
                str(self._qid(it) or "").strip(),
                str(self._qid(it) or "").strip().lower(),
            ):
                if key and key not in seen:
                    idx[key] = it
                    seen.add(key)
        self._item_index = idx

    # ---------- utils ----------
    def _qid(self, it: QuizItem) -> str:
        if it.id and not re.match(r"^Q\d+$", it.id):
            return it.id
        return "H" + self._hash(it.question.strip().lower())

    def _dataset_hash(self) -> str:
        joined = "|".join(sorted(self._qid(i) + "#" + i.chapter for i in self.all_items))
        return self._hash(joined)

    def _chap_sig(self, chapters: list[str]) -> str:
        return ",".join(sorted(chapters)).lower()

    def _used_key(self, chapters: list[str]) -> str:
        return f"{self._dataset_hash()}:{self._chap_sig(chapters)}"


    def _base_dataset_hash(self) -> str:
        items = self.base_all_items if self.base_all_items else self.all_items
        joined = "|".join(sorted(self._qid(i) + "#" + i.chapter for i in items))
        return self._hash(joined) if joined else ""

    def _wrong_dataset_key(self) -> str:
        # Usa l'hash del contenuto come chiave primaria: è deterministico
        # indipendentemente dal nome del file o dalla sorgente (locale/cloud).
        # Il nome viene usato solo come fallback se l'hash non è disponibile.
        h = self._base_dataset_hash()
        if h:
            return h
        base_name = self.base_dataset_name or self.dataset_name
        stem = self._norm_stats_name(base_name)
        return stem or "default"

    def _as_raw_question(self, it: QuizItem) -> dict[str, Any]:
        return {
            "id": str(it.id or ""),
            "question": str(it.question or ""),
            "chapter": str(it.chapter or "Generale"),
            "choices": [
                {"text": str(c), "correct": (i == int(it.correct_index))}
                for i, c in enumerate(it.choices or [])
            ],
            "correctIndex": int(it.correct_index),
            "explanation": str(it.explanation or ""),
            "image_url":   str(it.image_url or ""),
        }

    def _find_item_by_any_id(self, rid: str) -> QuizItem | None:
        """Lookup O(1) tramite indice pre-costruito da _rebuild_item_index().

        Ricade su scansione lineare solo se l'indice è vuoto (non ancora
        costruito o dataset appena modificato senza chiamare _rebuild).
        """
        key = str(rid or "").strip()
        if not key:
            return None
        # Percorso veloce: indice pre-costruito
        if self._item_index:
            result = self._item_index.get(key) or self._item_index.get(key.lower())
            if result is not None:
                return result
            return None
        # Fallback lineare (solo se l'indice non è stato ancora costruito)
        low = key.lower()
        items = list(self.base_all_items or []) + list(self.all_items or [])
        for it in items:
            try:
                if str(it.id or "").strip() == key:
                    return it
                if str(self._qid(it) or "").strip() == key:
                    return it
                if str(it.id or "").strip().lower() == low:
                    return it
                if str(self._qid(it) or "").strip().lower() == low:
                    return it
            except Exception:
                continue
        return None

    def _cache_current_as_base(self) -> None:
        self.base_raw_items = [dict(x) for x in (self.raw_items or []) if isinstance(x, dict)]
        self.base_all_items = list(self.all_items or [])
        self.base_dataset_name = str(self.dataset_name or "")
        self.base_source_mode = str(self.current_source_mode or "local")
        self.base_json_file_path = self.current_json_file_path
        self.base_json_url = str(self.current_json_url or "")
        self.is_wrong_mode = False
        self._rebuild_item_index()
        # Il dataset_key è cambiato: invalida la cache così wrong_count()
        # rilegge con la chiave corretta al prossimo accesso.
        self.invalidate_wrong_count_cache()

    def _wrong_local_get(self) -> list[dict[str, Any]]:
        bucket = self._state.setdefault("wrong", {})
        rows = bucket.get(self._wrong_dataset_key(), []) if isinstance(bucket, dict) else []
        return [dict(r) for r in rows if isinstance(r, dict)]

    def _wrong_local_set(self, rows: list[dict[str, Any]]) -> None:
        bucket = self._state.setdefault("wrong", {})
        if not isinstance(bucket, dict):
            bucket = {}
            self._state["wrong"] = bucket
        bucket[self._wrong_dataset_key()] = [dict(r) for r in rows if isinstance(r, dict)]
        self._save_state()

    def _wrong_cloud_fetch(self) -> list[dict[str, Any]]:
        uid = str((self.current_user or {}).get("id") or "")
        key = self._wrong_dataset_key()
        rows = self._supabase_rest(
            "quiz_wrong_items",
            "GET",
            {
                "select": "qid,payload",
                "user_id": f"eq.{uid}",
                "dataset_key": f"eq.{key}",
                "order": "updated_at.desc",
            },
        ) or []
        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            payload = r.get("payload")
            obj: dict[str, Any] | None = None
            if isinstance(payload, dict):
                obj = dict(payload)
            elif isinstance(payload, str):
                try:
                    j = json.loads(payload)
                    if isinstance(j, dict):
                        obj = j
                except Exception:
                    logger.debug("wrong_cloud_fetch: parsing payload fallito")
                    obj = None
            if obj is None:
                continue
            if not obj.get("qid"):
                obj["qid"] = str(r.get("qid") or obj.get("id") or "")
            # unwrap nested payload formats
            if isinstance(obj.get("payload"), dict):
                inner = obj.get("payload")
                if isinstance(inner, dict):
                    merged = dict(inner)
                    if not merged.get("qid"):
                        merged["qid"] = obj.get("qid")
                    obj = merged
            elif isinstance(obj.get("payload"), str):
                try:
                    inner2 = json.loads(str(obj.get("payload") or ""))
                    if isinstance(inner2, dict):
                        merged2 = dict(inner2)
                        if not merged2.get("qid"):
                            merged2["qid"] = obj.get("qid")
                        obj = merged2
                except Exception:
                    pass
            obj = self._coerce_wrong_row(obj)
            out.append(obj)
        return out

    def _wrong_cloud_set(self, rows: list[dict[str, Any]]) -> None:
        uid = str((self.current_user or {}).get("id") or "")
        key = self._wrong_dataset_key()
        self._supabase_rest(
            "quiz_wrong_items",
            "DELETE",
            {
                "user_id": f"eq.{uid}",
                "dataset_key": f"eq.{key}",
            },
        )
        payload = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            q = self._normalize_item(r)
            if q is None:
                continue
            payload.append({
                "user_id": uid,
                "dataset_key": key,
                "qid": self._qid(q),
                "payload": dict(r),
            })
        if payload:
            self._supabase_rest("quiz_wrong_items", "POST", payload=payload)

    def invalidate_wrong_count_cache(self) -> None:
        """Da chiamare ogni volta che il bacino errori viene modificato."""
        self._wrong_count_dirty = True

    def wrong_count(self) -> int:
        """Conta le domande nel bacino errori.

        Usa una cache invalidata esplicitamente per evitare una richiesta
        Supabase ad ogni chiamata da _sync_all().
        """
        if not self._wrong_count_dirty and self._wrong_count_cache >= 0:
            return self._wrong_count_cache
        try:
            rows = self._wrong_read()
            count = len(rows)
        except Exception:
            logger.warning("wrong_count: lettura bacino errori fallita", exc_info=True)
            count = max(0, self._wrong_count_cache)  # conserva l'ultimo valore noto
        self._wrong_count_cache = count
        self._wrong_count_dirty = False
        return count

    def save_wrong_questions(self, wrong_items: list[QuizItem], correct_items: list[QuizItem] | None = None) -> None:
        """Aggiorna il bacino errori.

        - ``wrong_items``: domande sbagliate da aggiungere/mantenere.
        - ``correct_items``: domande risposte correttamente da rimuovere dal bacino
          (usato in modalità errori per non cancellare tutto il bacino).
        """
        if not self.base_all_items and not self.all_items:
            return
        existing = []
        try:
            existing = self._wrong_read()
        except Exception:
            logger.warning("save_wrong_questions: lettura esistenti fallita", exc_info=True)
            existing = []

        # Costruisce l'insieme degli id da rimuovere (risposte corrette in wrong mode)
        correct_ids: set[str] = set()
        if correct_items:
            for it in correct_items:
                correct_ids.add(self._qid(it))

        # Parte dagli esistenti, rimuovendo quelli risposti correttamente
        merged: dict[str, dict[str, Any]] = {}
        for r in existing:
            q = self._normalize_item(r)
            if q is None:
                continue
            if self._qid(q) not in correct_ids:
                merged[self._qid(q)] = dict(r)

        # Aggiunge/aggiorna quelli sbagliati
        for it in wrong_items:
            merged[self._qid(it)] = self._as_raw_question(it)

        out = list(merged.values())
        self._wrong_write(out)
        self.invalidate_wrong_count_cache()

    def clear_wrong_questions(self) -> tuple[bool, str]:
        try:
            self._wrong_write([])
            self.invalidate_wrong_count_cache()
            return True, "File errori cancellato"
        except Exception as e:
            logger.warning("clear_wrong_questions fallito", exc_info=True)
            return False, f"Errore cancellazione errori: {e}"


    def _coerce_wrong_row(self, row: dict[str, Any]) -> dict[str, Any]:
        rr = dict(row or {})

        def _try_obj(v: Any) -> dict[str, Any] | None:
            if isinstance(v, dict):
                return dict(v)
            if isinstance(v, str):
                t = v.strip()
                if t.startswith("{") and t.endswith("}"):
                    try:
                        j = json.loads(t)
                        if isinstance(j, dict):
                            return dict(j)
                    except Exception:
                        return None
            return None

        # unwrap known nested containers
        for k in ("payload", "data", "item", "row", "question_obj"):
            obj = _try_obj(rr.get(k))
            if obj:
                merged = dict(obj)
                for key in ("id", "qid", "chapter", "capitolo", "lezione", "question", "domanda", "choices", "answers", "risposte", "correctIndex", "correct_index", "answerIndex", "risposta_corretta", "explanation", "spiegazione"):
                    if key not in merged and key in rr:
                        merged[key] = rr.get(key)
                rr = merged
                break

        # legacy case: question field contains a full JSON object as string
        qraw = rr.get("question")
        qobj = _try_obj(qraw)
        if qobj and ("choices" in qobj or "answers" in qobj or "risposte" in qobj):
            merged = dict(qobj)
            for key in ("id", "qid", "chapter", "capitolo", "lezione", "correctIndex", "correct_index", "answerIndex", "risposta_corretta", "explanation", "spiegazione"):
                if key not in merged and key in rr:
                    merged[key] = rr.get(key)
            rr = merged

        return rr

    def wrong_pool_payload(self) -> list[dict[str, Any]]:
        """Ritorna payload grezzo del bacino errori.

        In cloud mode usa solo Supabase (niente fallback locale silenzioso),
        per evitare anteprime stale da dati del device.
        """
        rows: list[dict[str, Any]] = []
        rows = self._wrong_read()

        out: list[dict[str, Any]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            rr = self._coerce_wrong_row(r)
            q = self._normalize_item(rr)

            if q is None:
                rid = str(rr.get("qid") or rr.get("id") or "").strip()
                by_id = self._find_item_by_any_id(rid)
                if by_id is not None:
                    rr2 = self._as_raw_question(by_id)
                    rr2["qid"] = self._qid(by_id)
                    if rr.get("chapter") and not rr2.get("chapter"):
                        rr2["chapter"] = rr.get("chapter")
                    rr = rr2
                    q = by_id

            if q is None:
                continue

            if not rr.get("qid"):
                rr["qid"] = self._qid(q)
            out.append(rr)
        return out

    def print_wrong_pool_grouped(self) -> tuple[bool, str]:
        rows = self.wrong_pool_payload()
        if not rows:
            return False, "Bacino errori vuoto"

        items: list[QuizItem] = []
        for r in rows:
            it = self._normalize_item(r)
            if it is not None:
                items.append(it)
        if not items:
            return False, "Nessuna domanda errore valida da stampare"

        grouped: dict[str, list[QuizItem]] = {}
        for it in items:
            grouped.setdefault(str(it.chapter or "Generale"), []).append(it)
        chapters = sorted(grouped.keys(), key=self._chapter_sort_key)

        def esc(v: Any) -> str:
            return html_lib.escape(str(v or ""))

        parts = [
            "<html><head><meta charset='utf-8'></head>",
            "<body style='font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif;background:#fff;color:#111;margin:0;padding:16px'>",
            f"<div style='font-size:18pt;font-weight:700;margin:0 0 8px 0;text-align:center'>Bacino errori - {esc(self.base_dataset_name or self.dataset_name)}</div>",
            f"<div style='font-size:13pt;margin:0 0 12px 0;text-align:center'>Data: {esc(datetime.now().strftime('%d/%m/%Y %H:%M:%S'))} · Domande: {len(items)} · Lezioni: {len(chapters)}</div>",
        ]

        for ch in chapters:
            parts.append(f"<div style='font-size:16pt;font-weight:700;margin:14px 0 8px 0;text-align:center'>{esc(ch)}</div>")
            for i, it in enumerate(grouped.get(ch, []), start=1):
                parts.append("<div style='border:1px solid #000;margin:0 0 10px 0'>")
                parts.append("<div style='padding:8px 10px;border-bottom:1px solid #000'>")
                parts.append("<div style='display:block'>")
                parts.append(f"<div style='font-size:11pt;font-weight:700;line-height:1.25'>{i}. {esc(it.question)}</div>")
                parts.append("</div></div></div>")
                parts.append("<div style='padding:2px 0'>")
                for ci, txt in enumerate(it.choices or []):
                    weight = '700' if ci == int(it.correct_index) else '400'
                    n = str(ci + 1) + "."
                    parts.append(
                        f"<div style='padding:4px 10px;border-top:1px solid #000;font-size:10pt;line-height:1.3'>"
                        f"<span style='display:inline-block;min-width:24px;font-size:10pt;font-weight:400'>{n}</span>"
                        f"<span style='font-size:10pt;font-weight:{weight}'>{esc(txt)}</span>"
                        f"</div>"
                    )
                parts.append("</div></div>")
                parts.append("<div style='height:6pt'></div><div style='font-family:monospace;font-size:11pt;line-height:1;text-align:center;white-space:nowrap;overflow:hidden'>------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------</div><div style='height:6pt'></div>")

        parts.append("</body></html>")
        html = "".join(parts)

        try:
            from PySide6.QtGui import QTextDocument
            from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import Qt

            doc = QTextDocument()
            doc.setHtml(html)

            app = QApplication.instance()
            parent = app.activeWindow() if app is not None else None
            printer = QPrinter(QPrinter.HighResolution)
            preview = QPrintPreviewDialog(printer, parent)
            preview.setWindowTitle("Anteprima stampa bacino errori")
            preview.setWindowModality(Qt.ApplicationModal)
            preview.resize(1200, 840)

            def _paint(pr):
                if hasattr(doc, "print_"):
                    doc.print_(pr)
                else:
                    doc.print(pr)

            preview.paintRequested.connect(_paint)

            self._wrong_preview_dialog = preview
            self._wrong_preview_doc = doc
            self._wrong_preview_html = html

            preview.show()
            preview.raise_()
            preview.activateWindow()
            rc = int(preview.exec())
            return True, f"Anteprima bacino errori pronta ({len(items)} domande, rc={rc})"
        except Exception as e:
            return False, f"Errore anteprima/stampa errori: {e}"

    def load_wrong_only(self) -> tuple[bool, str]:
        if not self.base_all_items:
            return False, "Carica prima un JSON base"
        try:
            raw = self._wrong_read()
        except Exception as e:
            return False, f"Errore lettura errori: {e}"
        items: list[QuizItem] = []
        valid_raw: list[dict[str, Any]] = []
        for r in raw:
            it = self._normalize_item(r)
            if it is None:
                continue
            items.append(it)
            valid_raw.append(dict(r))
        if not items:
            return False, "Non ci sono domande sbagliate salvate"
        self.raw_items = valid_raw
        self.all_items = items
        self.current_items = []
        self.dataset_name = f"{self.base_dataset_name} [Errori]"
        self.selected_chapters = sorted({i.chapter for i in items}, key=self._chapter_sort_key)
        self.is_wrong_mode = True
        self.last_pool_total = len(items)
        self.last_pool_used = 0
        self._rebuild_item_index()
        return True, f"Modalità errori attiva ✓ ({len(items)} quiz)"

    def load_base_mode(self) -> tuple[bool, str]:
        if not self.base_all_items:
            return False, "Nessun dataset base disponibile"
        self.raw_items = [dict(x) for x in (self.base_raw_items or []) if isinstance(x, dict)]
        self.all_items = list(self.base_all_items)
        self.current_items = []
        self.dataset_name = str(self.base_dataset_name or self.dataset_name)
        self.current_source_mode = str(self.base_source_mode or self.current_source_mode)
        self.current_json_file_path = self.base_json_file_path
        self.current_json_url = str(self.base_json_url or "")
        self.is_wrong_mode = False
        self.selected_chapters = sorted({i.chapter for i in self.all_items}, key=self._chapter_sort_key)
        if not self.is_wrong_mode:
            self._sync_pool_counter(self.selected_chapters)
        return True, f"Modalità questionario attiva ✓ ({len(self.all_items)} quiz)"

    def toggle_context_mode(self) -> tuple[bool, str]:
        if self.is_wrong_mode:
            return self.load_base_mode()
        return self.load_wrong_only()

    def _stats_norm_for_mode(self, mode: str) -> str:
        if str(mode) == "wrong":
            base = str(self.base_dataset_name or self.dataset_name or "")
            base = re.sub(r"\s*\[\s*errori\s*\]\s*$", "", base, flags=re.IGNORECASE).strip()
            return self._norm_stats_name(base)
        base_name = str(self.base_dataset_name or self.dataset_name or "")
        base_name = re.sub(r"\s*\[\s*errori\s*\]\s*$", "", base_name, flags=re.IGNORECASE).strip()
        return self._norm_stats_name(base_name)

    def _stats_dataset_key_for_mode(self, mode: str) -> str:
        """Chiave univoca dataset per filtrare le statistiche senza collisioni tra JSON."""
        if str(mode) == "wrong":
            return self._base_dataset_hash() or self._dataset_hash()
        if self.base_all_items:
            return self._base_dataset_hash()
        return self._dataset_hash()

    def set_percent_mode(self, enabled: bool) -> None:
        self.use_percent_mode = bool(enabled)

    def set_percent_map(self, pct_map: dict[str, float]) -> None:
        clean: dict[str, float] = {}
        for k, v in (pct_map or {}).items():
            try:
                fv = float(v)
            except Exception:
                continue
            if fv < 0:
                fv = 0.0
            clean[str(k)] = fv
        self.percent_map = clean

    def get_percent_map(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for ch in self.selected_chapters:
            out[ch] = float(self.percent_map.get(ch, 0.0))
        return out


    def _profile_dataset_key(self) -> str:
        stem = re.sub(r"\s+", " ", Path(str(self.dataset_name or "")).stem.strip().lower())
        if stem:
            return f"json:{stem}"
        return self._dataset_hash()

    def _profiles_local_get(self) -> dict[str, Any]:
        all_profiles = self._state.setdefault("profiles", {})
        bucket = all_profiles.get(self._profile_dataset_key(), {})
        return bucket if isinstance(bucket, dict) else {}

    def _profiles_local_set(self, profiles: dict[str, Any]) -> None:
        all_profiles = self._state.setdefault("profiles", {})
        all_profiles[self._profile_dataset_key()] = profiles
        self._save_state()

    def _profiles_cloud_get(self) -> dict[str, Any]:
        uid = str((self.current_user or {}).get("id") or "")
        dataset_key = self._profile_dataset_key()
        rows = self._supabase_rest(
            "quiz_profiles",
            "GET",
            {
                "select": "profile_name,payload",
                "user_id": f"eq.{uid}",
                "dataset_key": f"eq.{dataset_key}",
                "order": "profile_name.asc",
            },
        ) or []
        out: dict[str, Any] = {}
        for r in rows:
            name = str(r.get("profile_name") or "").strip()
            payload = r.get("payload") if isinstance(r.get("payload"), dict) else {}
            if name:
                out[name] = payload
        return out

    def _profiles_cloud_set(self, profiles: dict[str, Any], current_name: str | None = None) -> None:
        uid = str((self.current_user or {}).get("id") or "")
        dataset_key = self._profile_dataset_key()
        self._supabase_rest(
            "quiz_profiles",
            "DELETE",
            {
                "user_id": f"eq.{uid}",
                "dataset_key": f"eq.{dataset_key}",
            },
        )
        rows = []
        for name, payload in (profiles or {}).items():
            nm = str(name or "").strip()
            if not nm:
                continue
            rows.append(
                {
                    "user_id": uid,
                    "dataset_key": dataset_key,
                    "profile_name": nm,
                    "payload": payload if isinstance(payload, dict) else {},
                    "is_current": bool(current_name and nm == current_name),
                }
            )
        if rows:
            self._supabase_rest("quiz_profiles", "POST", payload=rows)

    def percent_profiles(self) -> dict[str, Any]:
        if self._cloud_state_enabled():
            try:
                return self._profiles_cloud_get()
            except Exception:
                logger.warning("percent_profiles: lettura cloud fallita", exc_info=True)
                return {}
        return self._profiles_local_get()

    def save_percent_profile(self, name: str) -> tuple[bool, str]:
        nm = str(name or "").strip()
        if not nm:
            return False, "Nome profilo mancante"
        payload = {
            "chapters": list(self.selected_chapters or []),
            "percents": dict(self.get_percent_map() or {}),
            "ts": datetime.now().isoformat(),
        }
        if self._cloud_state_enabled():
            try:
                p = self._profiles_cloud_get()
                p[nm] = payload
                self._profiles_cloud_set(p, current_name=nm)
                return True, f"Profilo '{nm}' salvato (cloud)"
            except Exception as e:
                return False, f"Errore salvataggio profilo cloud: {e}"
        p = self._profiles_local_get()
        p[nm] = payload
        self._profiles_local_set(p)
        return True, f"Profilo '{nm}' salvato"

    def load_percent_profile(self, name: str) -> tuple[bool, str]:
        nm = str(name or "").strip()
        if not nm:
            return False, "Nome profilo mancante"
        try:
            p = self._profiles_cloud_get() if self._cloud_state_enabled() else self._profiles_local_get()
        except Exception as e:
            logger.warning("load_percent_profile: lettura profili fallita", exc_info=True)
            return False, f"Errore lettura profili cloud: {e}"
        if nm not in p:
            return False, f"Profilo '{nm}' non trovato"
        cfg = p.get(nm) if isinstance(p.get(nm), dict) else {}
        ch = cfg.get("chapters") if isinstance(cfg.get("chapters"), list) else []
        self.set_selected_chapters([str(x) for x in ch])
        pct = cfg.get("percents") if isinstance(cfg.get("percents"), dict) else {}
        self.set_percent_map(pct)
        self.set_percent_mode(True)
        return True, f"Profilo '{nm}' caricato"

    def delete_percent_profile(self, name: str) -> tuple[bool, str]:
        nm = str(name or "").strip()
        if not nm:
            return False, "Nome profilo mancante"
        if self._cloud_state_enabled():
            try:
                p = self._profiles_cloud_get()
                if nm not in p:
                    return False, f"Profilo '{nm}' non trovato"
                del p[nm]
                self._profiles_cloud_set(p, current_name=None)
                return True, f"Profilo '{nm}' eliminato (cloud)"
            except Exception as e:
                return False, f"Errore eliminazione profilo cloud: {e}"
        p = self._profiles_local_get()
        if nm not in p:
            return False, f"Profilo '{nm}' non trovato"
        del p[nm]
        self._profiles_local_set(p)
        return True, f"Profilo '{nm}' eliminato"

    def pick_by_percent(self, items: list[QuizItem], num: int, pct_map: dict[str, float]) -> list[QuizItem]:
        by_ch: dict[str, list[QuizItem]] = {}
        for it in items:
            by_ch.setdefault(it.chapter, []).append(it)
        chapters = [ch for ch in by_ch.keys() if ch in pct_map]
        if not chapters:
            return random.sample(items, min(num, len(items)))
        weights = {ch: max(0.0, float(pct_map.get(ch, 0.0))) for ch in chapters}
        if sum(weights.values()) <= 0:
            weights = {ch: 1.0 for ch in chapters}

        allocations = []
        den = sum(weights.values())
        for ch in chapters:
            exact = (num * weights[ch]) / den
            allocations.append({"ch": ch, "exact": exact, "base": int(exact), "rem": exact - int(exact)})

        for a in allocations:
            cap = len(by_ch[a["ch"]])
            if a["base"] > cap:
                a["base"] = cap

        total = sum(a["base"] for a in allocations)
        remaining = max(0, num - total)
        allocations.sort(key=lambda x: x["rem"], reverse=True)
        for a in allocations:
            if remaining <= 0:
                break
            cap = len(by_ch[a["ch"]])
            if a["base"] < cap:
                a["base"] += 1
                remaining -= 1

        picked: list[QuizItem] = []
        for a in allocations:
            arr = by_ch[a["ch"]][:]
            random.shuffle(arr)
            picked.extend(arr[:a["base"]])
        random.shuffle(picked)
        return picked[:num]

    def _sync_pool_counter(self, chapters: list[str]) -> None:
        pool = [i for i in self.all_items if i.chapter in chapters]
        used_ids = set(self._state["used"].get(self._used_key(chapters), []))
        pool_ids = {self._qid(i) for i in pool}
        self.last_pool_total = len(pool)
        self.last_pool_used = len(pool_ids.intersection(used_ids))

    # ---------- load ----------
    def _extract_choices_and_correct(self, row: dict[str, Any]) -> tuple[list[str], int | None]:
        raw_choices = row.get("choices", row.get("answers", row.get("risposte")))
        choices: list[str] = []
        correct_idx: int | None = None

        if isinstance(raw_choices, list):
            for i, c in enumerate(raw_choices):
                is_ok = False
                if isinstance(c, dict):
                    txt = str(c.get("text", c.get("label", c.get("value", c.get("answer", c.get("risposta", ""))))) or "").strip()
                    is_ok = bool(c.get("correct", c.get("isCorrect", c.get("ok", False))))
                else:
                    txt = str(c or "").strip()

                if re.search(r"§§§\s*\[OK\]", txt, re.IGNORECASE) or re.search(r"\[OK\]", txt, re.IGNORECASE):
                    is_ok = True
                txt = re.sub(r"§§§\s*\[OK\]", "", txt, flags=re.IGNORECASE).strip()
                txt = re.sub(r"\[OK\]", "", txt, flags=re.IGNORECASE).strip()

                choices.append(txt)
                if is_ok and correct_idx is None:
                    correct_idx = i

        ci = row.get("correctIndex", row.get("correct_index", row.get("answerIndex", row.get("risposta_corretta"))))
        if ci is not None:
            try:
                ci_int = int(ci)
                if 0 <= ci_int < len(choices):
                    correct_idx = ci_int
            except Exception:
                pass

        if correct_idx is None:
            ca = str(row.get("correctAnswer", row.get("correct_answer", row.get("solution", ""))) or "").strip()
            if ca:
                if len(ca) == 1 and ca.upper() in "ABCD":
                    idx = ord(ca.upper()) - ord("A")
                    if 0 <= idx < len(choices):
                        correct_idx = idx
                else:
                    for i, ch in enumerate(choices):
                        if ch.strip().lower() == ca.lower():
                            correct_idx = i
                            break

        return choices, correct_idx

    def _normalize_item(self, row: dict[str, Any]) -> QuizItem | None:
        if not isinstance(row, dict):
            return None

        q = str(row.get("question", row.get("domanda", row.get("text", ""))) or "").strip()
        choices, ci = self._extract_choices_and_correct(row)
        if not q or len(choices) < 2 or ci is None:
            return None

        if ci < 0 or ci >= len(choices):
            return None

        return QuizItem(
            id=str(row.get("id", row.get("qid", ""))).strip(),
            question=q,
            choices=choices,
            correct_index=ci,
            chapter=str(row.get("chapter", row.get("capitolo", row.get("lezione", "Generale"))) or "").strip() or "Generale",
            explanation=str(row.get("explanation", row.get("spiegazione", "")) or "").strip(),
            image_url=str(row.get("image_url", "") or "").strip(),
        )

    def load_from_payload(self, payload: Any, dataset_name: str) -> tuple[bool, str]:
        if isinstance(payload, dict):
            for k in ("questions", "items", "domande", "quiz", "data"):
                v = payload.get(k)
                if isinstance(v, list):
                    payload = v
                    break
        if not isinstance(payload, list):
            return False, "JSON non valido: atteso array"
        items: list[QuizItem] = []
        raw: list[dict[str, Any]] = []
        for row in payload:
            it = self._normalize_item(row)
            if it is None:
                continue
            items.append(it)
            raw.append(dict(row))
        if not items:
            return False, "JSON valido ma senza domande utilizzabili"
        self.raw_items = raw
        self.all_items = items
        self.current_items = []
        self.dataset_name = dataset_name
        self.selected_chapters = sorted({i.chapter for i in items}, key=self._chapter_sort_key)
        # Cambio dataset: invalida cache statistiche per evitare leak tra JSON.
        self._cloud_stats_dirty = True
        self._cloud_stats_for_json = ""
        self._sync_pool_counter(self.selected_chapters)
        return True, f"Caricato {len(items)} quiz"

    def load_from_file(self, path: str) -> tuple[bool, str]:
        p = Path(path)
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            return False, f"Errore lettura file: {e}"
        ok, msg = self.load_from_payload(payload, p.name)
        if ok:
            self.current_source_mode = "local"
            self.current_json_file_path = p
            self.current_json_url = ""
            self._cache_current_as_base()
        return ok, msg

    def load_from_url(self, url: str, dataset_name: str | None = None) -> tuple[bool, str]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "QuizNova/1.0"})
            with self._urlopen_with_ssl_fallback(req, timeout=25) as r:
                text = r.read().decode("utf-8", errors="replace")
            payload = json.loads(text)
        except Exception as e:
            return False, f"Errore caricamento URL: {e}"
        ok, msg = self.load_from_payload(payload, str(dataset_name or url))
        if ok:
            self.current_json_url = str(url or "")
            if self.current_source_mode != "cloud":
                self.current_source_mode = "local"
            if self.current_source_mode != "cloud":
                self.current_json_file_path = None
            self._cache_current_as_base()
        return ok, msg

    def load_from_paste(self, text: str) -> tuple[bool, str]:
        try:
            payload = json.loads(text)
        except Exception as e:
            return False, f"JSON incollato non valido: {e}"
        ok, msg = self.load_from_payload(payload, "JSON incollato")
        if ok:
            self.current_source_mode = "local"
            self.current_json_file_path = None
            self.current_json_url = ""
            self._cache_current_as_base()
        return ok, msg

    def load_from_pdf(self, path: str, progress_cb=None, run_mode: str = "complete") -> tuple[bool, str]:
        """Genera un dataset quiz da un PDF di dispense tramite GPT-4o-mini vision."""
        from pdf_quiz_generator import load_from_pdf_module
        return load_from_pdf_module(self, path, progress_cb=progress_cb, run_mode=run_mode)

    # ---------- cloud picker (supabase + github manifest) ----------
    @staticmethod
    def _is_http_url(value: str) -> bool:
        t = str(value or "").strip().lower()
        return t.startswith("http://") or t.startswith("https://")

    @staticmethod
    def _normalize_github_raw_url(url: str) -> str:
        raw = str(url or "").strip()
        if not raw:
            return raw
        try:
            parts = urllib.parse.urlsplit(raw)
            host = (parts.netloc or "").lower()
            path = (parts.path or "").strip("/")
            if host != "github.com":
                return raw
            chunks = path.split("/")
            if len(chunks) >= 5 and chunks[2] in ("blob", "raw"):
                owner, repo, _tag, branch = chunks[0], chunks[1], chunks[2], chunks[3]
                tail = "/".join(chunks[4:])
                return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{tail}"
            return raw
        except Exception:
            return raw

    def _normalize_cloud_manifest_url(self, value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return DEFAULT_GITHUB_MANIFEST_URL
        raw = self._normalize_github_raw_url(raw)
        try:
            parts = urllib.parse.urlsplit(raw)
            host = (parts.netloc or "").lower()
            path = (parts.path or "").strip("/")
            if host == "github.com":
                chunks = path.split("/")
                if len(chunks) >= 2:
                    owner, repo = chunks[0], chunks[1]
                    branch = "main"
                    folder = "JSON"
                    if len(chunks) >= 5 and chunks[2] == "tree":
                        branch = chunks[3]
                        rest = "/".join(chunks[4:]).strip("/")
                        if rest.endswith(".json"):
                            return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{rest}"
                        if rest:
                            folder = rest
                    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{folder.rstrip('/')}/manifest.json"
            if host == "raw.githubusercontent.com" and path and not path.lower().endswith(".json"):
                return raw.rstrip("/") + "/manifest.json"
        except Exception:
            pass
        return raw

    def _normalize_cloud_file_url(self, value: str, manifest_url: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return raw
        raw = self._normalize_github_raw_url(raw)
        if self._is_http_url(raw):
            return raw
        m_url = self._normalize_cloud_manifest_url(manifest_url)
        try:
            parts = urllib.parse.urlsplit(m_url)
            base_path = parts.path
            if base_path.lower().endswith(".json"):
                base_path = base_path.rsplit("/", 1)[0] + "/"
            else:
                base_path = base_path.rstrip("/") + "/"
            base = urllib.parse.urlunsplit((parts.scheme, parts.netloc, base_path, "", ""))
            rel = urllib.parse.quote(urllib.parse.unquote(raw.lstrip("/")), safe="/._-")
            return urllib.parse.urljoin(base, rel)
        except Exception:
            return raw

    @staticmethod
    def _manifest_row_kind_and_name(row_name: str, row_type: str) -> tuple[str, str]:
        name = str(row_name or "").strip()
        typ = str(row_type or "").strip().lower()
        suffix = Path(name).suffix.lower()
        kind = typ or (suffix[1:] if suffix.startswith(".") else suffix)
        if "application/pdf" in typ:
            kind = "pdf"
        elif "spreadsheet" in typ or "excel" in typ:
            kind = "xlsx"
        elif "json" in typ:
            kind = "json"
        stem = Path(name).stem.strip() if name else ""
        if kind in ("json", "qmap", "pdf", "xlsx"):
            return kind, stem
        if suffix in (".json", ".qmap", ".pdf", ".xlsx"):
            return suffix[1:], stem
        return "", stem

    def _fetch_manifest_entries(self, manifest_url: str) -> list[tuple[str, str]]:
        url = self._normalize_cloud_manifest_url(manifest_url)
        req = urllib.request.Request(url, headers={"User-Agent": "QuizNovaCloudPicker/1.0"})
        with self._urlopen_with_ssl_fallback(req, timeout=30) as resp:
            data = resp.read().decode("utf-8", errors="replace")
        payload = json.loads(data)

        candidates: list[Any] = []
        if isinstance(payload, dict):
            for key in ("items", "files", "datasets", "jsons", "entries", "data", "list"):
                val = payload.get(key)
                if isinstance(val, list):
                    candidates = val
                    break
            if not candidates and any(k in payload for k in ("url", "href", "link")):
                candidates = [payload]
        elif isinstance(payload, list):
            candidates = payload

        out: list[tuple[str, str]] = []
        for i, row in enumerate(candidates):
            row_type = ""
            if isinstance(row, str):
                file_url = self._normalize_cloud_file_url(row.strip(), url)
                name = Path(urllib.parse.urlsplit(file_url).path).name or f"JSON {i+1}.json"
            elif isinstance(row, dict):
                file_url = str(row.get("url") or row.get("href") or row.get("link") or row.get("json_url") or row.get("download_url") or "").strip()
                if not file_url:
                    file_url = str(row.get("path") or row.get("file") or row.get("filename") or row.get("file_name") or "").strip()
                file_url = self._normalize_cloud_file_url(file_url, url)
                name = str(
                    row.get("name")
                    or row.get("title")
                    or row.get("label")
                    or row.get("file")
                    or row.get("filename")
                    or row.get("file_name")
                    or row.get("path")
                    or ""
                ).strip()
                row_type = str(row.get("type") or row.get("kind") or row.get("format") or row.get("mime") or row.get("mimeType") or "").strip()
                if not name:
                    name = Path(urllib.parse.urlsplit(file_url).path).name or f"JSON {i+1}.json"
            else:
                continue
            if not file_url:
                continue
            kind, _ = self._manifest_row_kind_and_name(name, row_type)
            name_l = str(name).lower()
            is_aux = kind in ("qmap", "pdf", "xlsx") or name_l.endswith((".qmap", ".pdf", ".xlsx", "_pdf_map.json", ".old"))
            is_json_like = kind == "json" or name_l.endswith(".json") or (not is_aux and isinstance(row, dict))
            if not is_json_like:
                continue
            display = Path(name).stem.strip() or f"JSON {i+1}"
            out.append((display, file_url))

        if not out and url.endswith('/manifest.json'):
            try:
                full_url = url[:-len('manifest.json')] + 'manifest_full.json'
                req2 = urllib.request.Request(full_url, headers={"User-Agent": "QuizNovaCloudPicker/1.0"})
                with self._urlopen_with_ssl_fallback(req2, timeout=30) as resp2:
                    payload2 = json.loads(resp2.read().decode("utf-8", errors="replace"))
                if isinstance(payload2, list):
                    for i, row in enumerate(payload2):
                        if not isinstance(row, dict):
                            continue
                        file_url = str(row.get("url") or row.get("href") or row.get("link") or row.get("json_url") or row.get("download_url") or row.get("path") or "").strip()
                        file_url = self._normalize_cloud_file_url(file_url, full_url)
                        name = str(row.get("name") or row.get("title") or row.get("label") or row.get("file") or row.get("filename") or row.get("path") or "").strip()
                        kind, _ = self._manifest_row_kind_and_name(name, str(row.get("type") or row.get("kind") or ""))
                        if kind != "json" and not str(name).lower().endswith(".json"):
                            continue
                        if not file_url:
                            continue
                        display = Path(name).stem.strip() or f"JSON {i+1}"
                        out.append((display, file_url))
            except Exception:
                pass

        dedup: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for n, u in out:
            k = (n.lower(), u)
            if k in seen:
                continue
            seen.add(k)
            dedup.append((n, u))
        return dedup

    def supabase_sign_in_password(self, username: str, password: str) -> tuple[bool, str]:
        ident = str(username or "").strip()
        pwd = str(password or "")
        if not ident or not pwd:
            return False, "Inserisci username e password"
        if "@" not in ident:
            ident = ident + "@app.local"

        endpoint = f"{SUPABASE_URL}/auth/v1/token?grant_type=password"
        payload = json.dumps({"email": ident, "password": pwd}).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={"apikey": SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._urlopen_with_ssl_fallback(req, timeout=25) as r:
                obj = json.loads(r.read().decode("utf-8", errors="replace"))
            token = str(obj.get("access_token") or "").strip()
            user = obj.get("user") if isinstance(obj.get("user"), dict) else {}
            if not token or not user:
                return False, "Risposta login Supabase non valida"
            self.auth_access_token = token
            self.current_user = user
            self.set_pref("supabase_last_user", username)
            self._cloud_stats_dirty = True
            return True, f"Login eseguito: {user.get('email') or username}"
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                o = json.loads(body)
                msg = o.get("error_description") or o.get("msg") or o.get("message") or body
            except Exception:
                msg = body or str(e)
            return False, f"Login Supabase fallito: {msg}"
        except Exception as e:
            return False, f"Errore rete login Supabase: {e}"

    def supabase_change_password(self, new_password: str) -> tuple[bool, str]:
        pwd = str(new_password or "").strip()
        if len(pwd) < 6:
            return False, "Password troppo corta (minimo 6 caratteri)"
        uid = str((self.current_user or {}).get("id") or "").strip()
        tok = str(self.auth_access_token or "").strip()
        if not uid or not tok:
            return False, "Effettua prima login cloud"

        endpoint = f"{SUPABASE_URL}/auth/v1/user"
        payload = json.dumps({"password": pwd}).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {tok}",
                "Content-Type": "application/json",
            },
            method="PUT",
        )
        try:
            with self._urlopen_with_ssl_fallback(req, timeout=25):
                pass
            return True, "Password aggiornata"
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                obj = json.loads(body)
                msg = obj.get("error_description") or obj.get("msg") or obj.get("message") or body
            except Exception:
                msg = body or str(e)
            return False, f"Cambio password fallito: {msg}"
        except Exception as e:
            return False, f"Errore rete cambio password: {e}"


    def cloud_fetch_entries(self, manifest_url: str) -> tuple[bool, str]:
        if not (self.current_user and self.auth_access_token):
            return False, "Accesso Supabase richiesto"
        final_url = self._normalize_cloud_manifest_url(manifest_url or self.get_pref("cloud_manifest_url", DEFAULT_GITHUB_MANIFEST_URL))
        self.set_pref("cloud_manifest_url", final_url)
        try:
            self.cloud_entries = self._fetch_manifest_entries(final_url)
        except Exception as e:
            self.cloud_entries = []
            return False, f"Errore lettura manifest: {e}"
        if not self.cloud_entries:
            return False, "Manifest vuoto o senza JSON validi"
        return True, f"Trovati {len(self.cloud_entries)} JSON da GitHub"

    def cloud_entries_payload(self) -> list[dict[str, str]]:
        return [{"name": n, "url": u} for (n, u) in self.cloud_entries]

    def cloud_load_selected(self, url: str, name: str) -> tuple[bool, str]:
        if not url:
            return False, "URL JSON non valido"
        self.current_source_mode = "cloud"
        ok, msg = self.load_from_url(url, dataset_name=name or url)
        if ok:
            self._cache_current_as_base()
            self._cloud_stats_dirty = True
            self._cloud_stats_for_json = ""
        return ok, msg

    def _desktop_snapshot_to_html(self, snapshot_rows: list[dict[str, Any]]) -> str:
        rows = snapshot_rows if isinstance(snapshot_rows, list) else []
        parts: list[str] = []
        for idx, q in enumerate(rows, start=1):
            question = html_lib.escape(str(q.get("question") or ""))
            chapter = html_lib.escape(str(q.get("chapter") or "Generale"))
            choices = q.get("choices") if isinstance(q.get("choices"), list) else []

            labels: list[str] = []
            selected_correct = False
            has_selected = False
            correct_text = ""

            for c in choices:
                raw_text = str((c or {}).get("text") or "")
                text = html_lib.escape(raw_text)
                is_correct = bool((c or {}).get("correct"))
                is_selected = bool((c or {}).get("selected"))
                if is_correct and not correct_text:
                    correct_text = text

                cls: list[str] = []
                if is_correct:
                    cls.append("correct")
                if is_selected and not is_correct:
                    cls.append("wrong")
                if is_selected:
                    has_selected = True
                    if is_correct:
                        selected_correct = True
                class_attr = f" class='{' '.join(cls)}'" if cls else ""
                checked = " checked" if is_selected else ""
                labels.append(
                    f"<label{class_attr}><span class='choice-n'></span>"
                    f"<span>{text}</span><input type='radio' value='{html_lib.escape(raw_text)}' correct='{'true' if is_correct else 'false'}'{checked}></label>"
                )

            if selected_correct:
                res_cls = "result ok"
                res_txt = "Corretto"
            else:
                res_cls = "result err"
                if has_selected:
                    res_txt = f"Risposta esatta: {correct_text}"
                else:
                    res_txt = f"Nessuna selezione - risposta esatta: {correct_text}"

            parts.append(
                "<div class='q'>"
                "<div class='qhead'>"
                f"<div class='qnum'>{idx}</div>"
                "<div class='qtitle'>"
                f"{question}"
                f"<span class='qref'>Paragrafo di riferimento - {chapter}</span>"
                "</div></div>"
                f"<div class='choices'>{''.join(labels)}</div>"
                f"<div class='{res_cls}'>{res_txt}</div>"
                "<div class='explain' style='display:none'></div>"
                "</div>"
            )
        return ''.join(parts)

    def _parse_html_snapshot_to_desktop_snapshot(self, html_snapshot: str) -> list[dict[str, Any]]:
        txt_raw = str(html_snapshot or "")
        if not txt_raw.strip():
            return []
        txt = txt_raw
        if "<div" not in txt_raw.lower() and "&lt;div" in txt_raw.lower():
            txt = html_lib.unescape(txt_raw)

        def strip_tags(v: str) -> str:
            raw = re.sub(r"<[^>]+>", " ", str(v or ""), flags=re.IGNORECASE | re.DOTALL)
            raw = html_lib.unescape(raw)
            raw = re.sub(r"\s+", " ", raw).strip()
            return raw

        starts = [m.start() for m in re.finditer(r'<div\s+class=["\']q\b', txt, flags=re.IGNORECASE)]
        if not starts:
            plain = strip_tags(txt)
            return [{"question": plain or "Snapshot non decodificabile", "chapter": "Generale", "choices": []}] if plain else []
        starts.append(len(txt))

        out: list[dict[str, Any]] = []
        for i in range(len(starts) - 1):
            block = txt[starts[i]:starts[i + 1]]
            q_title = ""
            m_title = re.search(r'<div\s+class=["\']qtitle["\'][^>]*>(.*?)</div>', block, flags=re.IGNORECASE | re.DOTALL)
            if m_title:
                t_html = re.sub(r'<span\s+class=["\']qref["\'][^>]*>.*?</span>', "", m_title.group(1), flags=re.IGNORECASE | re.DOTALL)
                q_title = strip_tags(t_html)

            chapter = "Generale"
            m_ref = re.search(r'<span\s+class=["\']qref["\'][^>]*>(.*?)</span>', block, flags=re.IGNORECASE | re.DOTALL)
            if m_ref:
                ref = strip_tags(m_ref.group(1))
                ref = re.sub(r"^Paragrafo\s+di\s+riferimento\s*-\s*", "", ref, flags=re.IGNORECASE).strip()
                if ref:
                    chapter = ref

            result_text = ""
            m_res = re.search(r'<div\s+class=["\'][^"\']*result[^"\']*["\'][^>]*>(.*?)</div>', block, flags=re.IGNORECASE | re.DOTALL)
            if m_res:
                result_text = strip_tags(m_res.group(1)).lower()

            labels = re.findall(r'<label([^>]*)>(.*?)</label>', block, flags=re.IGNORECASE | re.DOTALL)
            choices: list[dict[str, Any]] = []
            sel_idx = -1
            cor_idx = -1
            for idx, (lbl_attrs, body) in enumerate(labels):
                m_in = re.search(r'<input([^>]*)>', body, flags=re.IGNORECASE | re.DOTALL)
                in_attrs = m_in.group(1) if m_in else ""
                is_correct = bool(re.search(r'correct\s*=\s*["\']?true["\']?', in_attrs, flags=re.IGNORECASE))
                if is_correct and cor_idx < 0:
                    cor_idx = idx

                cls = str(lbl_attrs or "").lower()
                is_checked = bool(re.search(r'\bchecked\b', in_attrs, flags=re.IGNORECASE))
                # Compat HTML: molte versioni marcano la scelta utente via classi del label
                if ("wrong" in cls) or ("selected" in cls) or ("checked" in cls):
                    is_checked = True
                if is_checked and sel_idx < 0:
                    sel_idx = idx

                text_body = re.sub(r'<input[^>]*>', "", body, flags=re.IGNORECASE | re.DOTALL)
                text_body = re.sub(r'<span\s+class=["\']choice-n["\'][^>]*>.*?</span>', "", text_body, flags=re.IGNORECASE | re.DOTALL)
                choices.append({"text": strip_tags(text_body), "correct": is_correct, "selected": is_checked})

            # Fallback: deduce errore/correttezza da testo risultato se selezione non esplicita
            is_wrong = False
            if "errata" in result_text or "risposta esatta" in result_text:
                is_wrong = True
            elif "corretto" in result_text:
                is_wrong = False
            elif sel_idx >= 0 and cor_idx >= 0:
                is_wrong = sel_idx != cor_idx

            out.append({
                "question": q_title or "",
                "chapter": chapter,
                "choices": choices,
                "selectedIndex": sel_idx,
                "correctIndex": cor_idx,
                "isWrong": is_wrong,
            })
        return out


    def _extract_snapshot_parts(self, snap_obj: Any) -> tuple[list[dict[str, Any]], str]:
        def _norm_rows(rows: Any) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            if not isinstance(rows, list):
                return out
            for r in rows:
                if isinstance(r, dict):
                    out.append(r)
            return out

        rows: list[dict[str, Any]] = []
        html_snap = ""

        if isinstance(snap_obj, list):
            rows = _norm_rows(snap_obj)
        elif isinstance(snap_obj, dict):
            for k in ("desktop_snapshot", "detail", "items", "snapshot", "rows", "data", "questions"):
                v = snap_obj.get(k)
                if isinstance(v, list):
                    rows = _norm_rows(v)
                    if rows:
                        break
                if isinstance(v, str) and v.strip().startswith("["):
                    try:
                        vv = json.loads(v)
                        rows = _norm_rows(vv)
                        if rows:
                            break
                    except Exception:
                        pass
            for k in ("html", "snapshot_html", "html_snapshot"):
                v = snap_obj.get(k)
                if isinstance(v, str) and v.strip():
                    html_snap = v
                    break
        elif isinstance(snap_obj, str):
            txt = snap_obj.strip()
            if txt:
                if "<div" in txt.lower() or "&lt;div" in txt.lower():
                    html_snap = txt
                else:
                    try:
                        obj = json.loads(txt)
                        rows, html_snap = self._extract_snapshot_parts(obj)
                    except Exception:
                        pass

        if (not rows) and html_snap:
            rows = self._parse_html_snapshot_to_desktop_snapshot(html_snap)

        return rows, html_snap





    def _download_temp_file(self, url: str, suffix: str = ".pdf") -> Path:
        key = self._hash(str(url or ""))
        if key in self._pdf_cache and self._pdf_cache[key].exists():
            return self._pdf_cache[key]
        dst = Path(tempfile.gettempdir()) / f"quiznova_{key}{suffix}"
        req = urllib.request.Request(str(url or ""), headers={"User-Agent": "QuizNova/1.0"})
        with self._urlopen_with_ssl_fallback(req, timeout=30) as r:
            data = r.read()
        dst.write_bytes(data)
        self._pdf_cache[key] = dst
        return dst

    @staticmethod
    def _normalize_name_key(value: str) -> str:
        v = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
        return v.strip()

    def _guess_local_companion(self, suffixes: list[str]) -> Path | None:
        p = self.current_json_file_path
        if not p:
            return None
        base = p.parent
        target = self._normalize_name_key(p.stem)
        cands = sorted(base.iterdir()) if base.exists() else []
        for ext in suffixes:
            for f in cands:
                if not f.is_file():
                    continue
                if f.suffix.lower() != ext.lower():
                    continue
                if self._normalize_name_key(f.stem) == target:
                    return f
        for ext in suffixes:
            for f in cands:
                if f.is_file() and f.suffix.lower() == ext.lower():
                    return f
        return None

    def _qmap_to_pages(self, text: str) -> dict[str, int]:
        try:
            obj = json.loads(text)
        except Exception:
            return {}
        if isinstance(obj, dict) and isinstance(obj.get("chapterToPage"), dict):
            obj = obj.get("chapterToPage")
        if not isinstance(obj, dict):
            return {}
        out: dict[str, int] = {}
        for k, v in obj.items():
            try:
                iv = int(v)
            except Exception:
                continue
            out[str(k).strip()] = iv
        return out

    def _find_page_for_chapter(self, chapter: str, mapping: dict[str, int]) -> int | None:
        if not mapping:
            return None
        ch = str(chapter or "").strip()
        if ch in mapping:
            return mapping[ch]
        low = ch.lower()
        for k, v in mapping.items():
            if str(k).lower() == low:
                return v
        nkh = self._normalize_name_key(ch)
        for k, v in mapping.items():
            if self._normalize_name_key(k) == nkh:
                return v
        return None



    def _resolve_pdf_context(self) -> tuple[str, dict[str, int]]:
        pdf_url = ""
        mapping: dict[str, int] = {}

        if self.current_json_file_path:
            pdf = self._guess_local_companion([".pdf"])
            qmap = self._guess_local_companion([".qmap", ".old"])
            if pdf:
                pdf_url = pdf.resolve().as_uri()
            if qmap and qmap.exists():
                try:
                    mapping = self._qmap_to_pages(qmap.read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    mapping = {}

        if not pdf_url and self.current_json_url.startswith(("http://", "https://")):
            u = urllib.parse.urlsplit(self.current_json_url)
            base_path = u.path.rsplit('/', 1)[0] + '/'
            stem = Path(urllib.parse.unquote(u.path)).stem
            pdf_url = urllib.parse.urlunsplit((u.scheme, u.netloc, base_path + urllib.parse.quote(stem + '.pdf'), '', ''))
            qmap_url = urllib.parse.urlunsplit((u.scheme, u.netloc, base_path + urllib.parse.quote(stem + '.qmap'), '', ''))
            try:
                req = urllib.request.Request(qmap_url, headers={"User-Agent": "QuizNova/1.0"})
                with self._urlopen_with_ssl_fallback(req, timeout=15) as r:
                    txt = r.read().decode("utf-8", errors="replace")
                mapping = self._qmap_to_pages(txt)
            except Exception:
                mapping = {}

        return pdf_url, mapping

    def can_open_pdf_for_chapter(self, chapter: str) -> bool:
        chapter = str(chapter or "").strip()
        if not chapter:
            return False
        pdf_url, mapping = self._resolve_pdf_context()
        if not pdf_url:
            return False
        page = self._find_page_for_chapter(chapter, mapping)
        return bool(page and int(page) > 0)

    def open_pdf_for_chapter(self, chapter: str) -> tuple[bool, str]:
        chapter = str(chapter or "").strip()
        if not chapter:
            return False, "Capitolo mancante"

        pdf_url = ""
        page = None

        # Local mode: same folder as selected JSON
        if self.current_json_file_path:
            pdf = self._guess_local_companion([".pdf"])
            qmap = self._guess_local_companion([".qmap", ".old"])
            if pdf:
                pdf_url = pdf.resolve().as_uri()
            if qmap and qmap.exists():
                try:
                    mp = self._qmap_to_pages(qmap.read_text(encoding="utf-8", errors="replace"))
                    page = self._find_page_for_chapter(chapter, mp)
                except Exception:
                    pass

        # Cloud mode: same URL folder as selected JSON
        if not pdf_url and self.current_json_url.startswith(("http://", "https://")):
            u = urllib.parse.urlsplit(self.current_json_url)
            base_path = u.path.rsplit('/', 1)[0] + '/'
            stem = Path(urllib.parse.unquote(u.path)).stem
            pdf_url = urllib.parse.urlunsplit((u.scheme, u.netloc, base_path + urllib.parse.quote(stem + '.pdf'), '', ''))
            qmap_url = urllib.parse.urlunsplit((u.scheme, u.netloc, base_path + urllib.parse.quote(stem + '.qmap'), '', ''))
            try:
                req = urllib.request.Request(qmap_url, headers={"User-Agent": "QuizNova/1.0"})
                with self._urlopen_with_ssl_fallback(req, timeout=15) as r:
                    txt = r.read().decode("utf-8", errors="replace")
                mp = self._qmap_to_pages(txt)
                page = self._find_page_for_chapter(chapter, mp)
            except Exception:
                pass

        if not pdf_url:
            return False, "PDF non trovato per questo dataset"
        if not page or int(page) <= 0:
            return False, "Nessuna pagina qmap per il capitolo selezionato"

        try:
            from PySide6.QtWidgets import QDialog, QVBoxLayout, QPushButton
            from PySide6.QtCore import Qt, QPointF
            from PySide6.QtPdf import QPdfDocument
            from PySide6.QtPdfWidgets import QPdfView

            if pdf_url.startswith('file://'):
                # url2pathname gestisce correttamente Windows: /C:/... → C:/...
                local_path = Path(urllib.request.url2pathname(urllib.parse.urlsplit(pdf_url).path))
            else:
                local_path = self._download_temp_file(pdf_url, '.pdf')

            if not local_path.exists():
                return False, "PDF non disponibile"

            dlg = QDialog()
            dlg.setWindowTitle("Viewer PDF")
            dlg.resize(1100, 820)
            lay = QVBoxLayout(dlg)

            doc = QPdfDocument(dlg)
            st = doc.load(str(local_path))
            try:
                none_err = getattr(QPdfDocument.Error, "None_", 0)
                if int(st) != int(none_err):
                    return False, "Errore apertura PDF"
            except Exception:
                pass

            view = QPdfView(dlg)
            view.setDocument(doc)
            view.setPageMode(QPdfView.PageMode.MultiPage)
            lay.addWidget(view, 1)

            btn = QPushButton("Chiudi", dlg)
            btn.clicked.connect(dlg.close)
            lay.addWidget(btn, 0, Qt.AlignRight)

            if page and int(page) > 0:
                try:
                    view.pageNavigator().jump(int(page) - 1, QPointF(0, 0), 1.0)
                except Exception:
                    pass

            self._pdf_dialog = dlg
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            return True, f"PDF aperto{' a pagina ' + str(int(page)) if page else ''}"
        except Exception as e:
            return False, f"Errore apertura PDF interno: {e}"

    def ask_ai_explain(self, item: dict[str, Any]) -> tuple[bool, str, str]:
        api_key = str(self.get_pref("openai_api_key", "") or "").strip()
        if not api_key:
            return False, "Chiave AI mancante (Easter Egg)", ""

        q = str((item or {}).get("question") or "").strip()
        chapter = str((item or {}).get("chapter") or "").strip()
        choices = (item or {}).get("choices") if isinstance((item or {}).get("choices"), list) else []

        selected_txt = "Nessuna risposta"
        correct_txt = "N/D"
        for c in choices:
            if not isinstance(c, dict):
                continue
            t = str(c.get("text") or "")
            if c.get("selected"):
                selected_txt = t
            if c.get("correct"):
                correct_txt = t

        prompt = (
            "Spiega in italiano in modo approfondito ma chiaro la differenza tra risposta selezionata e corretta. "
            "Usa struttura: 1) Perché è sbagliata 2) Perché è corretta 3) Come evitare l'errore.\n\n"
            f"Capitolo: {chapter}\nDomanda: {q}\nRisposta selezionata: {selected_txt}\nRisposta corretta: {correct_txt}"
        )

        endpoint = "https://api.openai.com/v1/chat/completions"
        payload = {
            "model": "gpt-4o-mini",
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": "Sei un tutor universitario. Rispondi in italiano."},
                {"role": "user", "content": prompt},
            ],
        }
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._urlopen_with_ssl_fallback(req, timeout=40) as r:
                obj = json.loads(r.read().decode("utf-8", errors="replace"))
            text = str((((obj.get("choices") or [{}])[0].get("message") or {}).get("content") or "")).strip()
            if not text:
                return False, "Risposta AI vuota", ""
            return True, "Spiegazione AI pronta", text
        except Exception as e:
            return False, f"Errore AI: {e}", ""


    def print_snapshot_errors(self, snapshot_rows: list[dict[str, Any]]) -> tuple[bool, str]:
        rows = snapshot_rows if isinstance(snapshot_rows, list) else []
        wrong_rows: list[dict[str, Any]] = []

        def _idxes(item: dict[str, Any]) -> tuple[int, int]:
            sel = -1
            cor = -1
            if isinstance(item.get("selected"), int):
                sel = int(item.get("selected"))
            if isinstance(item.get("correctIndex"), int):
                cor = int(item.get("correctIndex"))
            choices = item.get("choices") if isinstance(item.get("choices"), list) else []
            if sel < 0:
                for i, ch in enumerate(choices):
                    if isinstance(ch, dict) and ch.get("selected"):
                        sel = i
                        break
            if cor < 0:
                for i, ch in enumerate(choices):
                    if isinstance(ch, dict) and ch.get("correct"):
                        cor = i
                        break
            return sel, cor

        for r in rows:
            if not isinstance(r, dict):
                continue
            sel, cor = _idxes(r)
            is_correct = r.get("isCorrect")
            wrong = (not is_correct) if isinstance(is_correct, bool) else (sel != cor)
            if wrong:
                wrong_rows.append(r)

        if not wrong_rows:
            return False, "Nessun errore da stampare"

        normalized: list[dict[str, Any]] = []
        for r in wrong_rows:
            if not isinstance(r, dict):
                continue
            ci = int(r.get("correctIndex") or 0)
            choices = r.get("choices") if isinstance(r.get("choices"), list) else []
            out_choices: list[dict[str, Any]] = []
            for j, c in enumerate(choices):
                if isinstance(c, dict):
                    out_choices.append({
                        "text": str(c.get("text") or ""),
                        "correct": bool(c.get("correct")) or (j == ci),
                    })
                else:
                    out_choices.append({"text": str(c), "correct": (j == ci)})
            normalized.append({
                "id": str(r.get("id") or r.get("qid") or ""),
                "qid": str(r.get("qid") or r.get("id") or ""),
                "chapter": str(r.get("chapter") or "Generale"),
                "question": str(r.get("question") or ""),
                "choices": out_choices,
                "correctIndex": ci,
            })

        original = self.wrong_pool_payload
        try:
            self.wrong_pool_payload = lambda: normalized  # type: ignore[assignment]
            return self.print_wrong_pool_grouped()
        finally:
            self.wrong_pool_payload = original  # type: ignore[assignment]

    def _stats_cloud_fetch(self, mode: str = "base") -> list[dict[str, Any]]:
        uid = str((self.current_user or {}).get("id") or "")
        mode = "wrong" if str(mode) == "wrong" else "base"
        rows = self._supabase_rest(
            "quiz_stats",
            "GET",
            {
                "select": "id,json_name,mode,dataset_key,total,correct,pct,duration_sec,note,snapshot,created_at",
                "user_id": f"eq.{uid}",
                "mode": f"eq.{mode}",
                "order": "created_at.desc",
            },
        ) or []
        current_norm = self._stats_norm_for_mode(mode)
        current_key = self._stats_dataset_key_for_mode(mode)
        if not current_norm and not current_key:
            return []
        out: list[dict[str, Any]] = []
        for r in rows:
            row_json = str(r.get("json_name") or "")
            row_key = str(r.get("dataset_key") or "").strip()
            if current_key and row_key and row_key != current_key:
                continue

            # Mantieni filtro legacy per nome dataset (retrocompat + anti-regressione).
            row_norm = self._norm_stats_name(row_json)
            if mode == "wrong":
                row_norm_clean = self._norm_stats_name(re.sub(r"\s*\[\s*errori\s*\]\s*$", "", row_json, flags=re.IGNORECASE))
                if current_norm and (row_norm != current_norm and row_norm_clean != current_norm):
                    continue
            else:
                if current_norm and row_norm != current_norm:
                    continue
            created = str(r.get("created_at") or "")
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone()
                date_txt = dt.strftime("%d/%m/%Y %H:%M:%S")
            except Exception:
                date_txt = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            out.append(
                {
                    "id": str(r.get("id") or ""),
                    "date": date_txt,
                    "json": row_json or self.dataset_name,
                    "dataset_key": row_key,
                    "total": int(r.get("total") or 0),
                    "correct": int(r.get("correct") or 0),
                    "pct": int(r.get("pct") or 0),
                    "note": str(r.get("note") or ""),
                    "snapshot": self._extract_snapshot_parts(r.get("snapshot"))[0],
                    "snapshot_html": self._extract_snapshot_parts(r.get("snapshot"))[1],
                }
            )
        for row in out:
            snap = row.get("snapshot")
            if isinstance(snap, list) and snap:
                continue
            h = str(row.get("snapshot_html") or "").strip()
            if h:
                row["snapshot"] = self._parse_html_snapshot_to_desktop_snapshot(h)
        return out

    def _stats_cloud_insert(self, record: dict[str, Any]) -> dict[str, Any]:
        uid = str((self.current_user or {}).get("id") or "")
        payload = {
            "user_id": uid,
            "json_name": self.dataset_name,
            "mode": "wrong" if self.is_wrong_mode else "base",
            "dataset_key": str(record.get("dataset_key") or self._stats_dataset_key_for_mode("wrong" if self.is_wrong_mode else "base")),
            "total": int(record.get("total") or 0),
            "correct": int(record.get("correct") or 0),
            "pct": int(record.get("pct") or 0),
            "duration_sec": 0,
            "note": str(record.get("note") or ""),
            "snapshot": {"desktop_snapshot": record.get("snapshot") or [], "html": self._desktop_snapshot_to_html(record.get("snapshot") or [])},
        }
        rows = self._supabase_rest(
            "quiz_stats",
            "POST",
            {"select": "id,json_name,total,correct,pct,created_at"},
            payload=payload,
            prefer="return=representation",
        ) or []
        if isinstance(rows, list) and rows:
            r = rows[0]
            record["id"] = str(r.get("id") or record.get("id") or "")
            created = str(r.get("created_at") or "")
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone()
                record["date"] = dt.strftime("%d/%m/%Y %H:%M:%S")
            except Exception:
                pass
        return record

    # ---------- quiz ----------
    def chapters(self) -> list[str]:
        return sorted({i.chapter for i in self.all_items}, key=self._chapter_sort_key)

    def set_selected_chapters(self, chapters: list[str]) -> None:
        if not chapters:
            self.selected_chapters = []
            self.last_pool_total = 0
            self.last_pool_used = 0
            return
        available = set(self.chapters())
        self.selected_chapters = [c for c in chapters if c in available]
        self._sync_pool_counter(self.selected_chapters)

    def generate_quiz(self, count: int) -> tuple[bool, str]:
        if not self.all_items:
            return False, "Carica prima un JSON"
        if not self.selected_chapters:
            # fallback robusto: se UI non ha ancora sincronizzato i check, usa tutti i capitoli
            self.selected_chapters = self.chapters()
        pool = [i for i in self.all_items if i.chapter in self.selected_chapters]
        if not pool:
            return False, "Nessuna domanda nel bacino selezionato"
        if self.is_wrong_mode:
            source = pool
            used_ids = set()
        else:
            used_key = self._used_key(self.selected_chapters)
            used_ids = set(self._state["used"].get(used_key, []))
            available = [i for i in pool if self._qid(i) not in used_ids]
            source = available if available else pool
            if not available and used_ids:
                # refill automatico, ma il contatore resta aggiornato solo dopo correzione
                self._state["used"][used_key] = []
                self._save_state()
        n = max(1, min(int(count), len(source)))
        if self.use_percent_mode:
            picked = self.pick_by_percent(source, n, self.get_percent_map())
        else:
            picked = random.sample(source, n)

        # Shuffle le scelte di ogni domanda una volta sola al momento della
        # generazione, aggiornando correct_index di conseguenza.
        # In questo modo current_quiz_payload e correct_all lavorano sempre
        # sullo stesso ordine e la correzione resta coerente.
        shuffled: list[QuizItem] = []
        for it in picked:
            indices = list(range(len(it.choices)))
            random.shuffle(indices)
            shuffled.append(QuizItem(
                id=it.id,
                question=it.question,
                choices=[it.choices[i] for i in indices],
                correct_index=indices.index(it.correct_index),
                chapter=it.chapter,
                explanation=it.explanation,
                image_url=it.image_url,
            ))
        self.current_items = shuffled
        return True, f"Questionario generato: {n} domande"

    def clear_quiz(self) -> tuple[bool, str]:
        self.current_items = []
        return True, "Questionario chiuso"

    def current_quiz_payload(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for idx, it in enumerate(self.current_items):
            out.append(
                {
                    "index": idx,
                    "chapter": it.chapter,
                    "question": it.question,
                    "choices": it.choices,
                    "correctIndex": it.correct_index,
                    "image_url": it.image_url,
                }
            )
        return out

    def correct_all(self, answers: list[int]) -> dict[str, Any]:
        total = len(self.current_items)
        if total == 0:
            return {"ok": False, "message": "Nessun quiz in corso"}

        correct = 0
        correct_items: list[QuizItem] = []
        wrong_items: list[QuizItem] = []
        detail: list[dict[str, Any]] = []
        for i, it in enumerate(self.current_items):
            sel = answers[i] if i < len(answers) else -1
            is_ok = sel == it.correct_index
            if is_ok:
                correct += 1
                correct_items.append(it)
            else:
                wrong_items.append(it)
            detail.append(
                {
                    "index": i,
                    "selected": sel,
                    "correctIndex": it.correct_index,
                    "chapter": it.chapter,
                    "question": it.question,
                    "correctText": it.choices[it.correct_index],
                    "isCorrect": is_ok,
                    "image_url": it.image_url,
                    "choices": [
                        {
                            "text": str(ch),
                            "correct": (ci == it.correct_index),
                            "selected": (ci == sel),
                        }
                        for ci, ch in enumerate(it.choices)
                    ],
                }
            )

        pct = round((correct / max(1, total)) * 100)

        # update used pool ONLY on completed correction (base mode)
        if not self.is_wrong_mode:
            used_key = self._used_key(self.selected_chapters)
            used_ids = set(self._state["used"].get(used_key, []))
            for it in self.current_items:
                used_ids.add(self._qid(it))
            self._state["used"][used_key] = sorted(used_ids)

        row = {
            "id": "L" + self._hash(datetime.now().isoformat() + self.dataset_name + str(total) + str(correct)),
            "date": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            "json": self.dataset_name,
            "mode": "wrong" if self.is_wrong_mode else "base",
            "dataset_key": self._stats_dataset_key_for_mode("wrong" if self.is_wrong_mode else "base"),
            "total": total,
            "correct": correct,
            "pct": pct,
            "note": "",
            "snapshot": detail,
        }
        self.save_wrong_questions(wrong_items, correct_items=correct_items if self.is_wrong_mode else None)

        if self._cloud_state_enabled():
            try:
                row = self._stats_cloud_insert(row)
                self._cloud_stats_dirty = True
                self._save_state()   # salva il pool usato su disco anche in cloud mode
            except Exception:
                # fallback locale se cloud temporaneamente non raggiungibile
                self._state["stats"].insert(0, row)
                self._state["stats"] = self._state["stats"][:200]
                self._save_state()
        else:
            self._state["stats"].insert(0, row)
            self._state["stats"] = self._state["stats"][:200]
            self._save_state()
        if not self.is_wrong_mode:
            self._sync_pool_counter(self.selected_chapters)

        return {
            "ok": True,
            "message": "Correzione completata",
            "total": total,
            "correct": correct,
            "wrong": total - correct,
            "pct": pct,
            "detail": detail,
        }

    def reset_pool(self) -> tuple[bool, str]:
        if self.is_wrong_mode:
            return False, "In modalità errori il bacino non viene usato"
        if not self.selected_chapters:
            return False, "Seleziona almeno un capitolo"
        key = self._used_key(self.selected_chapters)
        self._state["used"][key] = []
        self._save_state()
        self._sync_pool_counter(self.selected_chapters)
        return True, "Bacino resettato"




    def get_stat_snapshot(self, stat_id: str) -> tuple[list[dict[str, Any]], str]:
        rid = str(stat_id or "").strip()
        if not rid:
            return [], "ID statistica mancante"

        def _finalize_snapshot(snap_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            for row in snap_rows:
                if not isinstance(row, dict):
                    continue
                choices = row.get("choices") if isinstance(row.get("choices"), list) else []
                sel = row.get("selectedIndex", row.get("selected"))
                cor = row.get("correctIndex", row.get("answerIndex"))
                try:
                    sel_i = int(sel)
                except Exception:
                    sel_i = -1
                try:
                    cor_i = int(cor)
                except Exception:
                    cor_i = -1

                # Recover indexes from per-choice flags for retrocompat snapshots.
                if sel_i < 0:
                    for i, c in enumerate(choices):
                        if isinstance(c, dict) and bool(c.get("selected") or c.get("checked") or c.get("isSelected")):
                            sel_i = i
                            break
                if cor_i < 0:
                    for i, c in enumerate(choices):
                        if isinstance(c, dict) and bool(c.get("correct") or c.get("isCorrect") or c.get("ok")):
                            cor_i = i
                            break

                if cor_i >= 0:
                    row["correctIndex"] = cor_i
                if sel_i >= 0:
                    row["selectedIndex"] = sel_i

                if isinstance(row.get("isWrong"), bool):
                    wrong = bool(row.get("isWrong"))
                else:
                    wrong = (sel_i >= 0 and cor_i >= 0 and sel_i != cor_i)
                row["isWrong"] = wrong
                row["hasPdfPage"] = bool(wrong and self.can_open_pdf_for_chapter(str(row.get("chapter") or "")))
            return snap_rows

        # 1) Fast path: usa subito cache gia caricata (prefetch) senza rete.
        rows_cached = self.stats() + self.wrong_stats()
        rec = next((r for r in rows_cached if str((r or {}).get("id") or "") == rid), None)
        if isinstance(rec, dict):
            snap = rec.get("snapshot")
            if isinstance(snap, list) and snap:
                return _finalize_snapshot(snap), "Snapshot caricato"
            html_snap = str(rec.get("snapshot_html") or "").strip()
            if html_snap:
                parsed = self._parse_html_snapshot_to_desktop_snapshot(html_snap)
                if parsed:
                    return _finalize_snapshot(parsed), "Snapshot caricato"

        # 2) Solo se la cache non contiene snapshot, prova fetch mirato da cloud.
        if self._cloud_state_enabled():
            uid = str((self.current_user or {}).get("id") or "")
            try:
                rows = self._supabase_rest(
                    "quiz_stats",
                    "GET",
                    {
                        "select": "id,snapshot",
                        "id": f"eq.{rid}",
                        "user_id": f"eq.{uid}",
                    },
                ) or []
                if isinstance(rows, list) and rows:
                    r0 = rows[0] if isinstance(rows[0], dict) else {}
                    snap_obj = r0.get("snapshot")
                    snap_rows, snap_html = self._extract_snapshot_parts(snap_obj)
                    if isinstance(snap_rows, list) and snap_rows:
                        return _finalize_snapshot(snap_rows), "Snapshot caricato"
                    if str(snap_html or "").strip():
                        parsed = self._parse_html_snapshot_to_desktop_snapshot(str(snap_html))
                        if parsed:
                            return _finalize_snapshot(parsed), "Snapshot caricato"
            except Exception:
                pass

        # 3) Ultimo fallback: stato locale grezzo legacy.
        rows_all = list(self._state.get("stats", []))
        rec_local = next((r for r in rows_all if str((r or {}).get("id") or "") == rid), None)
        if isinstance(rec_local, dict):
            snap = rec_local.get("snapshot")
            if isinstance(snap, list) and snap:
                return _finalize_snapshot(snap), "Snapshot caricato"
            html_snap = str(rec_local.get("snapshot_html") or "").strip()
            if html_snap:
                parsed = self._parse_html_snapshot_to_desktop_snapshot(html_snap)
                if parsed:
                    return _finalize_snapshot(parsed), "Snapshot caricato"

        return [], "Nessuno snapshot disponibile"

    _UUID_RE = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )

    def delete_stat(self, stat_id: str) -> tuple[bool, str]:
        rid = str(stat_id or "").strip()
        if not rid:
            return False, "ID statistica mancante"
        is_uuid = bool(self._UUID_RE.match(rid))
        if self._cloud_state_enabled() and is_uuid:
            uid = str((self.current_user or {}).get("id") or "")
            try:
                self._supabase_rest("quiz_stats", "DELETE", {"id": f"eq.{rid}", "user_id": f"eq.{uid}"})
                self._cloud_stats_dirty = True
                return True, "Statistica eliminata"
            except Exception as e:
                return False, f"Errore eliminazione statistica cloud: {e}"
        # ID locale (prefisso "L" + hash) oppure cloud non abilitato: cancella da _state
        rows = list(self._state.get("stats", []))
        new_rows = [r for r in rows if str((r or {}).get("id") or "") != rid]
        if len(new_rows) == len(rows):
            return False, "Statistica non trovata"
        self._state["stats"] = new_rows
        self._save_state()
        self._cloud_stats_dirty = True  # invalida cache cloud così il fetch successivo è fresco
        return True, "Statistica eliminata"

    def _stats_local_by_mode(self, mode: str) -> list[dict[str, Any]]:
        rows = list(self._state.get("stats", []))
        out: list[dict[str, Any]] = []
        target_mode = "wrong" if str(mode) == "wrong" else "base"
        current = self._stats_norm_for_mode(target_mode)
        current_key = self._stats_dataset_key_for_mode(target_mode)
        if not current and not current_key:
            return []
        for r in rows:
            if not isinstance(r, dict):
                continue
            row_json = str(r.get("json", ""))
            row_key = str(r.get("dataset_key") or "").strip()
            row_mode = str(r.get("mode") or "")
            inferred_wrong = ("[errori]" in row_json.lower())
            if target_mode == "wrong":
                if row_mode and row_mode != "wrong" and not inferred_wrong:
                    continue
            else:
                if row_mode == "wrong" or inferred_wrong:
                    continue

            if current_key and row_key and row_key != current_key:
                continue

            # Mantieni filtro legacy per nome dataset (retrocompat + anti-regressione).
            row_norm = self._norm_stats_name(row_json)
            row_norm_clean = self._norm_stats_name(re.sub(r"\s*\[\s*errori\s*\]\s*$", "", row_json, flags=re.IGNORECASE))
            if target_mode == "wrong":
                if current and row_norm != current and row_norm_clean != current:
                    continue
            else:
                if current and row_norm != current:
                    continue
            out.append(r)
        return out

    def wrong_stats(self) -> list[dict[str, Any]]:
        # Non mostrare statistiche errori finche non e stato caricato almeno un dataset.
        has_dataset = bool(self.dataset_name or self.base_dataset_name or self.all_items or self.base_all_items)
        if not has_dataset:
            return []

        current = self._stats_norm_for_mode("wrong")
        if self._cloud_state_enabled():
            try:
                rows = self._stats_cloud_fetch("wrong")
                # cache wrong snapshot list separately in state fallback only through return
                return rows
            except Exception:
                return self._stats_local_by_mode("wrong")
        return self._stats_local_by_mode("wrong")

    def stats(self) -> list[dict[str, Any]]:
        if not self.dataset_name:
            return []

        current = self._stats_norm_for_mode("base")
        current_key = self._stats_dataset_key_for_mode("base")
        cache_tag = f"{current}|{current_key}"

        if self._cloud_state_enabled():
            if self._cloud_stats_dirty or self._cloud_stats_for_json != cache_tag:
                try:
                    self._cloud_stats_cache = self._stats_cloud_fetch("base")
                    self._cloud_stats_for_json = cache_tag
                    self._cloud_stats_dirty = False
                except Exception:
                    self._cloud_stats_cache = self._stats_local_by_mode("base")
            return list(self._cloud_stats_cache)

        return self._stats_local_by_mode("base")

