"""pdf_quiz_generator — PDF dispense → JSON quiz PegasoQuiz (con immagini).

Flusso completo
───────────────
1.  pdfminer  : estrae il testo pagina per pagina
2.  pypdf+PIL : estrae le immagini pagina per pagina (JPEG/PNG/etc.)
3.  chunking  : raggruppa testo e immagini in chunk per capitolo
4.  salvataggio immagini: ogni immagine estratta viene salvata in
       <pdf_stem>_images/<slug_capitolo>_N.png
    e il suo percorso assoluto (file://) viene memorizzato nel chunk
5.  GPT-4o-mini vision: per ogni chunk costruisce un messaggio multimodale
    (testo + immagini in base64 "detail:low") e chiede domande JSON
6.  validazione: scarta item malformati
7.  image_url assignment: ogni domanda generata riceve il campo image_url
    con il file:// dell'immagine a cui si riferisce (vuoto se nessuna)
8.  load_from_payload() + _cache_current_as_base()
    → da qui in poi tutto il flusso PegasoQuiz è invariato

Formato JSON prodotto (compatibile con _normalize_item esistente):
    {
      "question":    "...",
      "chapter":     "Capitolo 3",
      "choices":     [{"text": "...", "correct": false}, ...],
      "explanation": "...",
      "image_url":   "file:///Users/mario/Anatomia_images/cap3_0.png"
                     ← "" se la domanda non ha immagine
    }

Modifiche necessarie ai file esistenti
───────────────────────────────────────
  quiznova_backend.py   → +1 campo QuizItem  (image_url: str = "")
                        → _normalize_item legge image_url
                        → _as_raw_question  passa image_url
                        → current_quiz_payload passa image_url
                        → correct_all detail passa image_url
                        → +1 metodo load_from_pdf()
  main_quiznova.py      → +1 worker  LoadPdfWorker
                        → +1 slot    loadFromPdf()
  Main.qml              → Image{} nella quiz card  (quiz attivo)
                        → Image{} nella quiz card  (correzione)
                        → Image{} nello snapshot dialog
                        → FileDialog pdfFileDialog
                        → FancyButton "PDF → Quiz"

I diff esatti si trovano in fondo a questo file.

Dipendenze (già tutte presenti):
  pdfminer.six, pypdf, Pillow
"""
from __future__ import annotations

import base64
import json
import logging
import difflib
import random
import re
import time
import shutil
import urllib.request
import urllib.error
import urllib.parse
from io import BytesIO
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from quiznova_backend import QuizNovaBackend

logger = logging.getLogger("quiznova.pdf_gen")

# ── Costanti ──────────────────────────────────────────────────────────────────

OPENAI_ENDPOINT      = "https://api.openai.com/v1/chat/completions"
MODEL                = "gpt-4o-mini"   # supporta vision dal 2024-07
TEMPERATURE          = 0.3
MAX_TOKENS           = 4096  # aumentato da 2200: evita troncamento JSON su chunk lunghi
QUESTIONS_PER_CHUNK  = 6     # fallback: domande per chunk
MAX_CHUNK_CHARS      = 4000   # caratteri max inviati all'LLM per chunk
MAX_PAGES_PER_CHUNK  = 10     # pagine max per chunk: evita capitoli enormi su PDF senza intestazioni
MAX_QUESTIONS_PER_PDF = 400    # limite assoluto domande generate per singolo PDF
QUESTIONS_PER_PAGE    = 3      # budget domande calcolato in base al numero di pagine

# Limiti per modalità quick-check (se richiesta da UI)
PDF_QUICK_MAX_PAGES = 3
PDF_QUICK_MAX_CHUNKS = 3
PDF_QUICK_MAX_QUESTIONS = 12
MAX_IMAGES_PER_CHUNK = 2      # immagini max per chiamata API (costo)
MAX_IMAGE_SIDE_PX    = 640    # resize: lato lungo max prima dell'invio
MIN_IMAGE_SIDE_PX    = 80     # scarta immagini più piccole (icone/decorazioni)
HEADING_FONT_SIZE    = 13.5   # pt: soglia font per riconoscere intestazioni

# Keywords nella domanda che suggeriscono un riferimento visivo
_VISUAL_KW = re.compile(
    r"\b(figura|immagine|grafico|schema|diagramma|tabella|illustrazione"
    r"|foto|fotografia|disegno|mostra|raffigura|rappresenta)\b",
    re.IGNORECASE,
)

_PLACEHOLDER_RE = re.compile(r"\b(placeholder|lorem ipsum|lorem|ipsum|dummy|xxx|todo|tbd)\b|\[\.\.\.\]", re.IGNORECASE)
_LOW_SIGNAL_RE = re.compile(r"\b(quale immagine|figura seguente|in figura|nell'immagine|diagramma seguente|tabella seguente|si osserva|placeholder)\b", re.IGNORECASE)

# Pattern testuale per intestazioni di capitolo
_HEADING_RE = re.compile(
    r"^(cap(itolo)?|lezione|modulo|sezione|parte|unit[aà]|argomento"
    r"|chapter|lecture|topic)\s*[\d.IVXivx]+",
    re.IGNORECASE,
)


# ═════════════════════════════════════════════════════════════════════════════
# FASE 1 — estrazione testo per pagina
# ═════════════════════════════════════════════════════════════════════════════

def _char_max_fontsize(el: Any) -> float:
    # Legacy stub: with pypdf text extraction we no longer depend on pdfminer layout objects.
    return 0.0


def _extract_text_pages(pdf_path: str) -> list[tuple[int, str]]:
    """Ritorna [(page_index_0based, testo_pagina), ...] usando pypdf (bundle-safe)."""
    from pypdf import PdfReader

    pages: list[tuple[int, str]] = []
    try:
        reader = PdfReader(pdf_path)
        for pnum, page in enumerate(reader.pages):
            txt = page.extract_text() or ""
            txt = txt.replace("\r", "").strip()
            pages.append((pnum, txt))
    except Exception as e:
        raise RuntimeError(f"Errore estrazione testo: {e}") from e
    return pages


# ═════════════════════════════════════════════════════════════════════════════
# FASE 2 — estrazione immagini per pagina
# ═════════════════════════════════════════════════════════════════════════════

def _extract_image_pages(pdf_path: str, progress_cb=None) -> dict[int, list[bytes]]:
    """Ritorna {page_index: [png_bytes, ...]} per le immagini significative."""
    result: dict[int, list[bytes]] = {}
    try:
        from pypdf import PdfReader
        from PIL import Image

        reader = PdfReader(pdf_path)
        total_pages = len(reader.pages)
        for pnum, page in enumerate(reader.pages):
            if callable(progress_cb):
                try:
                    progress_cb(f"PDF: estrazione immagini pagina {pnum + 1}/{total_pages}…")
                except Exception:
                    pass
            page_imgs: list[bytes] = []
            try:
                for img_obj in page.images:
                    try:
                        pil: Image.Image = img_obj.image
                        if pil is None:
                            continue
                        w, h = pil.size
                        if w < MIN_IMAGE_SIDE_PX or h < MIN_IMAGE_SIDE_PX:
                            continue                     # scarta icone
                        if max(w, h) > MAX_IMAGE_SIDE_PX:
                            ratio = MAX_IMAGE_SIDE_PX / max(w, h)
                            pil = pil.resize(
                                (int(w * ratio), int(h * ratio)), Image.LANCZOS)
                        if pil.mode not in ("RGB", "L"):
                            pil = pil.convert("RGB")
                        buf = BytesIO()
                        pil.save(buf, format="PNG", optimize=True)
                        page_imgs.append(buf.getvalue())
                    except Exception:
                        continue
            except Exception:
                continue
            if page_imgs:
                result[pnum] = page_imgs
    except Exception as e:
        logger.warning("Estrazione immagini fallita (%s) — continuo solo testo", e)
    return result


# ═════════════════════════════════════════════════════════════════════════════
# FASE 3 — chunking testo + immagini per capitolo
# ═════════════════════════════════════════════════════════════════════════════

def _build_chunks(
    text_pages: list[tuple[int, str]],
    image_pages: dict[int, list[bytes]],
) -> list[dict[str, Any]]:
    """Ritorna lista di chunk:
       {"chapter": str, "text": str, "raw_images": [png_bytes, ...]}

    Strategia di suddivisione (in ordine di priorità):
      1. Nuova intestazione di capitolo rilevata → flush e nuovo chunk.
      2. Chunk corrente supera MAX_PAGES_PER_CHUNK pagine → flush forzato,
         anche senza intestazione. Evita chunk enormi su PDF privi di indice.
      3. Chunk corrente supera MAX_CHUNK_CHARS caratteri → split per paragrafi.
    """
    chunks: list[dict[str, Any]] = []
    cur_chapter = "Generale"
    cur_lines:  list[str] = []
    cur_images: list[bytes] = []
    cur_pages:  int = 0

    def _flush() -> None:
        nonlocal cur_lines, cur_images, cur_pages
        body = "\n".join(cur_lines).strip()
        if body or cur_images:
            chunks.append({
                "chapter":    cur_chapter,
                "text":       body,
                "raw_images": list(cur_images),
            })
        cur_lines  = []
        cur_images = []
        cur_pages  = 0

    for pnum, page_text in text_pages:
        for img_bytes in image_pages.get(pnum, []):
            if len(cur_images) < MAX_IMAGES_PER_CHUNK:
                cur_images.append(img_bytes)

        new_heading: str | None = None
        page_lines: list[str] = []

        for line in page_text.splitlines():
            line = line.strip()
            if not line:
                continue
            clean = re.sub(r"\s+", " ", line)
            is_h  = (bool(_HEADING_RE.match(clean))
                     or _char_max_fontsize_line(line) >= HEADING_FONT_SIZE)
            if is_h and len(clean) <= 120:
                if new_heading is None:
                    new_heading = clean
            else:
                page_lines.append(line)

        if new_heading is not None:
            # Nuova intestazione: flush e cambia capitolo
            _flush()
            cur_chapter = new_heading
        elif cur_pages >= MAX_PAGES_PER_CHUNK and cur_lines:
            # Troppo lungo senza intestazione: flush forzato
            _flush()

        cur_lines.extend(page_lines)
        cur_pages += 1

    _flush()

    if not chunks:
        raise RuntimeError(
            "Nessun testo estraibile dal PDF. "
            "I PDF scansionati richiedono OCR preventivo."
        )

    # Dividi chunk ancora troppo lunghi per caratteri (split per paragrafi)
    final: list[dict[str, Any]] = []
    for chunk in chunks:
        text = chunk["text"]
        if len(text) <= MAX_CHUNK_CHARS:
            final.append(chunk)
            continue
        paras = re.split(r"\n{2,}", text)
        part, part_len, idx = [], 0, 1
        imgs_used = False
        for para in paras:
            if part_len + len(para) > MAX_CHUNK_CHARS and part:
                label = (f"{chunk['chapter']} (parte {idx})"
                         if idx > 1 else chunk["chapter"])
                final.append({
                    "chapter":    label,
                    "text":       "\n\n".join(part),
                    "raw_images": chunk["raw_images"] if not imgs_used else [],
                })
                imgs_used = True
                idx += 1
                part, part_len = [], 0
            part.append(para)
            part_len += len(para)
        if part:
            label = (f"{chunk['chapter']} (parte {idx})"
                     if idx > 1 else chunk["chapter"])
            final.append({
                "chapter":    label,
                "text":       "\n\n".join(part),
                "raw_images": chunk["raw_images"] if not imgs_used else [],
            })

    return final


def _char_max_fontsize_line(line: str) -> float:
    """Fallback: stima la font size da un singolo testo senza layout object."""
    return 0.0   # usato solo come fallback nel chunking puro-testo


def _build_image_only_chunks(
    image_pages: dict[int, list[bytes]],
) -> list[dict[str, Any]]:
    """Fallback OCR-vision: costruisce chunk per pagina quando il PDF non ha testo estraibile."""
    chunks: list[dict[str, Any]] = []
    for pnum in sorted(image_pages.keys()):
        imgs = image_pages.get(pnum, [])[:MAX_IMAGES_PER_CHUNK]
        if not imgs:
            continue
        chunks.append(
            {
                "chapter": f"Pagina {pnum + 1}",
                "text": (
                    "Documento scansionato senza testo estraibile. "
                    "Ricava i concetti didattici direttamente dalle immagini della pagina."
                ),
                "raw_images": imgs,
            }
        )
    return chunks


# ═════════════════════════════════════════════════════════════════════════════
# FASE 4 — salvataggio immagini su disco
# ═════════════════════════════════════════════════════════════════════════════

def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower())
    return s.strip("_")[:40] or "chunk"


def _save_chunk_images(
    raw_images: list[bytes],
    images_dir: Path,
    chapter_slug: str,
) -> list[str]:
    """Salva PNG su disco, ritorna lista di URI file://."""
    images_dir.mkdir(parents=True, exist_ok=True)
    uris: list[str] = []
    for i, png in enumerate(raw_images):
        out = images_dir / f"{chapter_slug}_{i}.png"
        try:
            out.write_bytes(png)
            uris.append(out.resolve().as_uri())
        except Exception as e:
            logger.warning("Impossibile salvare immagine %s: %s", out.name, e)
    return uris


# ═════════════════════════════════════════════════════════════════════════════
# FASE 5 — chiamata GPT-4o-mini (testo + vision)
# ═════════════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = (
    "Sei un professore universitario esperto nella creazione di quiz accademici. "
    "Generi domande a risposta multipla chiare e pedagogicamente valide "
    "basandoti ESCLUSIVAMENTE sul materiale fornito. "
    "Rispondi SOLO con un array JSON valido, zero testo fuori dall'array."
)

_USER_PROMPT = """\
Dal materiale seguente genera esattamente {n} domande a risposta multipla in italiano.

REGOLE:
- Esattamente 4 scelte in formato testuale; UNA sola risposta deve terminare con " §§§[OK]"
- Domande su concetti DIVERSI, nessuna ripetizione
- Le domande devono essere testuali e autosufficienti (nessun riferimento a immagini/figure/grafici).\n- Vietato usare placeholder o testi fittizi (es. "Placeholder", "Lorem ipsum", "[...]").\n- chapter deve essere: "{chapter}"
- image_url: lascia sempre stringa vuota "" (NON inserire percorsi immagini)

FORMATO — array JSON puro, NIENTE markdown, NIENTE testo fuori:
[
  {{
    "question":    "Testo della domanda?",
    "chapter":     "{chapter}",
    "image_url":   "",
    "choices": [
      "Risposta A",
      "Risposta B §§§[OK]",
      "Risposta C",
      "Risposta D"
    ],
    "explanation": "Spiegazione breve della risposta corretta."
  }}
]

PERCORSI IMMAGINI DISPONIBILI PER QUESTO CAPITOLO:
{image_uris_list}

TESTO:
{text}"""


# Codici HTTP OpenAI considerati transienti (meritano retry)
_OPENAI_TRANSIENT_CODES = {"429", "500", "502", "503", "504"}

_OPENAI_MAX_RETRIES = 3          # tentativi totali (incluso il primo)
_OPENAI_RETRY_BASE_DELAY = 2.0   # secondi base per backoff esponenziale


def _call_openai_with_retry(
    api_key: str,
    chapter: str,
    text: str,
    image_uris: list[str],
    n: int,
    urlopen_fn: Any,
) -> list[dict[str, Any]]:
    """Wrapper con retry e backoff esponenziale attorno a ``_call_openai``.

    Riprova solo su errori transienti (429, 5xx).  Gli errori permanenti
    (401, 400, risposta malformata, rete non disponibile) vengono rilanciati
    immediatamente senza ulteriori tentativi.
    """
    last_exc: Exception | None = None
    for attempt in range(_OPENAI_MAX_RETRIES):
        try:
            return _call_openai(api_key, chapter, text, image_uris, n, urlopen_fn)
        except RuntimeError as e:
            err_str = str(e)
            is_transient = any(f"OpenAI {code}" in err_str for code in _OPENAI_TRANSIENT_CODES)
            if not is_transient:
                raise
            last_exc = e
            if attempt < _OPENAI_MAX_RETRIES - 1:
                delay = _OPENAI_RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0.0, 1.0)
                logger.warning(
                    "OpenAI errore transiente (tentativo %d/%d) per chunk '%s': %s — "
                    "attendo %.1fs prima di riprovare",
                    attempt + 1, _OPENAI_MAX_RETRIES, chapter, e, delay,
                )
                time.sleep(delay)

    raise RuntimeError(
        f"OpenAI: {_OPENAI_MAX_RETRIES} tentativi falliti per chunk '{chapter}': {last_exc}"
    )


def _parse_json_response(raw: str, chapter: str = "") -> list[dict[str, Any]]:
    """Parsifica la risposta JSON di OpenAI con recovery robusto.

    Gestisce tre casi di risposta malformata:
      1. JSON valido → parse diretto.
      2. JSON troncato nel mezzo di un oggetto → recupera gli oggetti
         completi già presenti nell'array prima del troncamento.
      3. JSON con array parziale ma senza ] finale → aggiunge la
         chiusura e riprova.

    In tutti i casi di troncamento restituisce le domande già complete
    invece di scartare l'intero chunk.
    """
    # Caso 1: JSON valido
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return result
        # Se OpenAI ha restituito un oggetto singolo invece di un array
        if isinstance(result, dict):
            return [result]
    except json.JSONDecodeError:
        pass

    # Estrai il sottostringa che inizia con '['
    start = raw.find("[")
    if start == -1:
        # Nessun array trovato: prova a interpretare come oggetto singolo
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return [obj]
        except Exception:
            pass
        raise RuntimeError(f"JSON non trovato nella risposta per chunk '{chapter}'")

    raw_arr = raw[start:]

    # Caso 2: array completo ma con testo extra dopo ']'
    end = raw_arr.rfind("]")
    if end != -1:
        try:
            result = json.loads(raw_arr[: end + 1])
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # Caso 3: array troncato — recupera oggetti completi uno a uno
    # Strategia: scansiona cercando oggetti { } bilanciati dall'inizio dell'array
    recovered: list[dict[str, Any]] = []
    depth = 0
    in_string = False
    escape_next = False
    obj_start: int | None = None

    for i, ch in enumerate(raw_arr):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and obj_start is not None:
                candidate = raw_arr[obj_start : i + 1]
                try:
                    obj = json.loads(candidate)
                    if isinstance(obj, dict):
                        recovered.append(obj)
                except json.JSONDecodeError:
                    pass
                obj_start = None

    if recovered:
        logger.warning(
            "JSON troncato per chunk '%s': recuperate %d domande complete su risposta parziale.",
            chapter, len(recovered),
        )
        return recovered

    raise RuntimeError(
        f"Impossibile recuperare JSON valido dalla risposta per chunk '{chapter}'. "
        f"Anteprima: {raw[:200]}"
    )


def _call_openai(
    api_key: str,
    chapter: str,
    text: str,
    image_uris: list[str],
    n: int,
    urlopen_fn: Any,
) -> list[dict[str, Any]]:
    uris_list = "\n".join(image_uris) if image_uris else "(nessuna)"
    prompt = _USER_PROMPT.format(
        n=n, chapter=chapter,
        text=text[:MAX_CHUNK_CHARS],
        image_uris_list=uris_list,
    )

    # Se ci sono immagini, inviale solo per supporto interno (output resta senza image_url).
    if image_uris:
        user_content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for uri in image_uris:
            try:
                png = Path(urllib.request.url2pathname(urllib.parse.urlsplit(uri).path)).read_bytes()
                b64 = base64.b64encode(png).decode()
                user_content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{b64}",
                        "detail": "low",
                    },
                })
            except Exception as e:
                logger.debug("Impossibile leggere immagine per API: %s", e)
    else:
        user_content = prompt  # type: ignore[assignment]

    payload = {
        "model":       MODEL,
        "temperature": TEMPERATURE,
        "max_tokens":  MAX_TOKENS,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ],
    }

    req = urllib.request.Request(
        OPENAI_ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    try:
        with urlopen_fn(req, timeout=90) as r:
            obj = json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            msg = json.loads(body).get("error", {}).get("message", body)
        except Exception:
            msg = body
        raise RuntimeError(f"OpenAI {e.code}: {msg}") from e
    except Exception as e:
        raise RuntimeError(f"Rete OpenAI: {e}") from e

    raw = (
        ((obj.get("choices") or [{}])[0].get("message") or {})
        .get("content", "")
        .strip()
    )
    if not raw:
        raise RuntimeError("Risposta OpenAI vuota")

    # Controlla finish_reason: "length" = risposta troncata per MAX_TOKENS
    finish_reason = (
        ((obj.get("choices") or [{}])[0]).get("finish_reason", "")
    )
    if finish_reason == "length":
        logger.warning(
            "OpenAI ha troncato la risposta per il chunk '%s' (finish_reason=length). "
            "Tento recovery parziale del JSON.",
            chapter,
        )

    # Rimuove eventuale fence markdown ```json … ```
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw).strip()

    items = _parse_json_response(raw, chapter)
    if not isinstance(items, list):
        raise RuntimeError(f"Risposta non è un array (tipo: {type(items).__name__})")
    return items


# ═════════════════════════════════════════════════════════════════════════════
# FASE 6 — validazione e arricchimento image_url
# ═════════════════════════════════════════════════════════════════════════════

def _normalize_question_text(q: str) -> str:
    q = (q or "").strip().lower()
    q = re.sub(r"\s+", " ", q)
    q = re.sub(r"[^a-z0-9àèéìíîòóùúçñ ]+", "", q)
    return q


def _is_near_duplicate_question(question: str, seen_norm: set[str]) -> bool:
    nq = _normalize_question_text(question)
    if not nq:
        return True
    if nq in seen_norm:
        return True
    for prev in seen_norm:
        if abs(len(prev) - len(nq)) > 30:
            continue
        if difflib.SequenceMatcher(None, prev, nq).ratio() >= 0.9:
            return True
    return False


def _has_text_overlap(question: str, source_text: str) -> bool:
    src = _normalize_question_text(source_text)
    qn = _normalize_question_text(question)
    if not src or not qn:
        return False
    q_tokens = [t for t in qn.split() if len(t) >= 5]
    if not q_tokens:
        return True
    hits = sum(1 for t in set(q_tokens) if t in src)
    # Richiede almeno un ancoraggio lessicale al contenuto sorgente.
    return hits >= 1


def _validate_and_enrich(
    raw: Any,
    image_uris: list[str],
) -> dict[str, Any] | None:
    """Valida l'item e assegna image_url se mancante ma implicito."""
    if not isinstance(raw, dict):
        return None
    question = str(raw.get("question") or "").strip()
    if not question:
        return None
    # Niente domande dipendenti da immagini o placeholder.
    if _VISUAL_KW.search(question):
        return None
    if _PLACEHOLDER_RE.search(question):
        return None
    if _LOW_SIGNAL_RE.search(question):
        return None

    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) < 2:
        return None

    parsed_choices: list[str] = []
    correct_idx: int | None = None
    seen_choice = set()

    for i, c in enumerate(choices):
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

        if not txt:
            return None
        if _PLACEHOLDER_RE.search(txt):
            return None
        key = txt.lower()
        if key in seen_choice:
            return None
        seen_choice.add(key)
        parsed_choices.append(txt)

        if is_ok:
            if correct_idx is not None:
                return None
            correct_idx = i

    if correct_idx is None:
        ci = raw.get("correctIndex", raw.get("correct_index", raw.get("answerIndex", raw.get("risposta_corretta"))))
        if ci is not None:
            try:
                ci_int = int(ci)
                if 0 <= ci_int < len(parsed_choices):
                    correct_idx = ci_int
            except Exception:
                pass

    if correct_idx is None:
        return None

    legacy_choices = [
        (txt + " §§§[OK]") if i == int(correct_idx) else txt
        for i, txt in enumerate(parsed_choices)
    ]

    image_url = str(raw.get("image_url") or "").strip()

    # Se l'LLM non ha compilato image_url ma la domanda fa riferimento
    # a una figura E ci sono immagini nel chunk, usa la prima
    if not image_url and image_uris and _VISUAL_KW.search(question):
        image_url = image_uris[0]

    return {
        "question":    question,
        "chapter":     str(raw.get("chapter") or "Generale").strip() or "Generale",
        "image_url":   image_url,
        "choices":     legacy_choices,
        "explanation": str(raw.get("explanation") or "").strip(),
    }


def _to_legacy_output_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, it in enumerate(items, 1):
        out.append(
            {
                "id": f"Q{i:03d}",
                "question": str(it.get("question") or "").strip(),
                "choices": [str(x) for x in (it.get("choices") or [])],
                "chapter": str(it.get("chapter") or "Generale").strip() or "Generale",
                "explanation": str(it.get("explanation") or "").strip(),
                "img_domanda": str(it.get("image_url") or ""),
            }
        )
    return out


def _write_json_snapshot(path: Path, items: list[dict[str, Any]]) -> None:
    """Salva snapshot incrementale del JSON; best-effort."""
    try:
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Impossibile aggiornare JSON parziale %s: %s", path, e)


_SNAPSHOT_KEEP = 3   # numero massimo di snapshot da conservare per stem PDF


def _cleanup_old_json_snapshots(pdf_path: Path, keep: int = _SNAPSHOT_KEEP) -> None:
    """Mantiene solo i ``keep`` snapshot JSON più recenti per questo PDF.

    Gli snapshot vengono identificati dal pattern ``<stem>_YYYYMMDD_HHMMSS.json``
    nella stessa directory del PDF.  Il file ``<stem>.json`` (senza timestamp)
    non viene toccato perché è il file principale dell'esecuzione corrente.

    Chiamare questa funzione *prima* di creare il nuovo snapshot, in modo che
    il conteggio non includa il file che sta per essere scritto.
    """
    parent = pdf_path.parent
    stem = pdf_path.stem
    # Pattern: stem + underscore + 8 cifre data + underscore + 6 cifre ora + .json
    import re as _re
    _ts_pattern = _re.compile(
        r"^" + _re.escape(stem) + r"_\d{8}_\d{6}\.json$"
    )
    candidates = sorted(
        (f for f in parent.iterdir() if f.is_file() and _ts_pattern.match(f.name)),
        key=lambda f: f.name,   # ordine lessicografico = ordine cronologico
    )
    to_delete = candidates[: max(0, len(candidates) - keep + 1)]
    for old in to_delete:
        try:
            old.unlink()
            logger.debug("Rimosso snapshot obsoleto: %s", old)
        except Exception as e:
            logger.warning("Impossibile rimuovere snapshot %s: %s", old, e)


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def load_from_pdf_module(
    backend: "QuizNovaBackend",
    path: str,
    progress_cb=None,
    run_mode: str = "complete",
) -> tuple[bool, str]:
    """Chiamato da QuizNovaBackend.load_from_pdf().

    Genera un dataset quiz testuale da un PDF e lo carica via load_from_payload().
    Salva JSON accanto al PDF originale per riuso senza nuove API call.
    """
    pdf_path = Path(path)
    if not pdf_path.exists():
        return False, f"File non trovato: {path}"
    if pdf_path.suffix.lower() != ".pdf":
        return False, "Il file non è un PDF"

    api_key = str(backend.get_pref("openai_api_key", "") or "").strip()
    if not api_key:
        return False, "Chiave OpenAI mancante — inseriscila nell'Easter Egg"

    logger.info("load_from_pdf: '%s'", pdf_path.name)
    quick_mode = (str(run_mode or "complete").strip().lower() == "quick")

    def _progress(msg: str) -> None:
        if callable(progress_cb):
            try:
                progress_cb(str(msg or ""))
            except Exception:
                pass

    _progress("PDF: lettura testo…")

    # Fase 1: testo
    try:
        text_pages = _extract_text_pages(str(pdf_path))
    except RuntimeError as e:
        return False, str(e)

    no_extractable_text = not any(t.strip() for _, t in text_pages)

    # TEMP quick check: limita subito le pagine processate.
    if quick_mode and len(text_pages) > PDF_QUICK_MAX_PAGES:
        text_pages = text_pages[:PDF_QUICK_MAX_PAGES]
        _progress(f"PDF: quick-check ON — pagine limitate a {len(text_pages)}")

    # Fase 2: chunking
    _progress("PDF: preparazione chunk…")
    if no_extractable_text:
        _progress("PDF: scansione rilevata, uso immagini per analisi…")
        image_pages = _extract_image_pages(str(pdf_path), progress_cb=_progress)
        if not image_pages:
            return False, "PDF senza testo estraibile e senza immagini utilizzabili"
        if quick_mode and len(image_pages) > PDF_QUICK_MAX_PAGES:
            keep = sorted(image_pages.keys())[:PDF_QUICK_MAX_PAGES]
            image_pages = {k: image_pages[k] for k in keep}
            _progress(f"PDF: quick-check ON — pagine immagini limitate a {len(image_pages)}")
        chunks = _build_image_only_chunks(image_pages)
        if not chunks:
            return False, "PDF senza testo estraibile: OCR automatico non riuscito"

        images_dir = pdf_path.parent / f"{pdf_path.stem}_images"
        if images_dir.exists():
            try:
                shutil.rmtree(images_dir)
            except Exception:
                pass
        for chunk in chunks:
            if chunk.get("raw_images"):
                slug = _slug(chunk["chapter"])
                chunk["image_uris"] = _save_chunk_images(chunk["raw_images"], images_dir, slug)
            else:
                chunk["image_uris"] = []
            if "raw_images" in chunk:
                del chunk["raw_images"]
    else:
        try:
            chunks = _build_chunks(text_pages, {})
        except RuntimeError as e:
            return False, str(e)
        if not chunks:
            return False, "Nessun contenuto utile trovato nel PDF"

        for chunk in chunks:
            chunk["image_uris"] = []
            if "raw_images" in chunk:
                del chunk["raw_images"]

    # Budget domande: domande-per-pagina con tetto massimo 400.
    if no_extractable_text:
        pages_for_budget = max(1, len(image_pages))
    else:
        non_empty_pages = sum(1 for _, t in text_pages if t.strip())
        pages_for_budget = max(1, non_empty_pages)
    target_questions = min(MAX_QUESTIONS_PER_PDF, pages_for_budget * QUESTIONS_PER_PAGE)

    # Se i chunk sono più del budget, riduci i chunk processati.
    if len(chunks) > target_questions:
        chunks = chunks[:target_questions]

    # TEMP quick check: riduzione ulteriore per test rapido.
    if quick_mode:
        target_questions = min(target_questions, PDF_QUICK_MAX_QUESTIONS)
        if len(chunks) > PDF_QUICK_MAX_CHUNKS:
            chunks = chunks[:PDF_QUICK_MAX_CHUNKS]
        _progress(
            f"PDF: quick-check ON — max {target_questions} domande, "
            f"max {len(chunks)} chunk"
        )

    # Output JSON incrementale: append progressivo chunk per chunk.
    out_json = pdf_path.with_suffix(".json")
    if out_json.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_json = out_json.with_name(f"{pdf_path.stem}_{ts}.json")
    # Rimuove i vecchi snapshot prima di crearne uno nuovo
    _cleanup_old_json_snapshots(pdf_path, keep=_SNAPSHOT_KEEP)
    _write_json_snapshot(out_json, [])

    _progress(f"PDF: generazione quiz… (target {target_questions}) · mode={'quick' if quick_mode else 'complete'}")

    urlopen_fn = backend._urlopen_with_ssl_fallback
    all_items: list[dict[str, Any]] = []
    seen_questions_norm: set[str] = set()
    errors: list[str] = []
    asked_so_far = 0

    for idx, chunk in enumerate(chunks, 1):
        chapter = chunk["chapter"]
        text = chunk["text"]
        image_uris: list[str] = list(chunk.get("image_uris") or [])

        remaining_budget = max(0, target_questions - asked_so_far)
        remaining_chunks = len(chunks) - idx + 1
        if remaining_budget <= 0:
            break
        q_per_chunk = max(1, min(remaining_budget, (remaining_budget + remaining_chunks - 1) // remaining_chunks))
        asked_so_far += q_per_chunk

        pct = 10 + int(idx * 85 / max(1, len(chunks)))
        if chapter.startswith("Pagina "):
            _progress(
                f"PDF: pagina {idx}/{len(chunks)} ({pct}%) — "
                f"quiz {len(all_items)}/{target_questions}"
            )
        else:
            _progress(
                f"PDF: chunk {idx}/{len(chunks)} ({pct}%) — "
                f"{chapter} · {len(all_items)}/{target_questions}"
            )

        logger.info(
            "Chunk %d/%d — '%s' (%d car) q=%d",
            idx,
            len(chunks),
            chapter,
            len(text),
            q_per_chunk,
        )
        try:
            raw_items = _call_openai_with_retry(
                api_key,
                chapter,
                text,
                image_uris,
                q_per_chunk,
                urlopen_fn,
            )
        except RuntimeError as e:
            logger.warning("Chunk '%s' fallito definitivamente: %s", chapter, e)
            errors.append(f"{chapter}: {e}")
            continue

        before_chunk_count = len(all_items)
        for raw in raw_items:
            item = _validate_and_enrich(raw, image_uris)
            if not item:
                continue

            qtxt = item.get("question", "")
            if _is_near_duplicate_question(qtxt, seen_questions_norm):
                continue

            # Evita domande non ancorate al testo quando il PDF contiene testo estraibile.
            if not no_extractable_text and not _has_text_overlap(qtxt, text):
                continue

            item["image_url"] = ""
            all_items.append(item)
            seen_questions_norm.add(_normalize_question_text(qtxt))

        if len(all_items) > before_chunk_count:
            _write_json_snapshot(out_json, _to_legacy_output_items(all_items))
            _progress(
                f"PDF: salvataggio parziale {len(all_items)}/{target_questions} "
                f"(capitolo/chunk {idx}/{len(chunks)})"
            )

    if len(all_items) > MAX_QUESTIONS_PER_PDF:
        all_items = all_items[:MAX_QUESTIONS_PER_PDF]

    if not all_items:
        detail = "; ".join(errors[:3]) if errors else "risposta non valida"
        return False, f"Nessuna domanda generata. Dettaglio: {detail}"

    # Struttura JSON retrocompatibile (HTML/parser legacy):
    # id, question, choices(string con §§§[OK]), chapter, explanation, img_domanda
    output_items = _to_legacy_output_items(all_items)

    _progress("PDF: salvataggio JSON… (95%)")
    _write_json_snapshot(out_json, output_items)
    logger.info("JSON salvato: '%s'", out_json)

    _progress("PDF: caricamento dataset in app… (98%)")

    ok, msg = backend.load_from_payload(output_items, pdf_path.stem)
    if ok:
        backend.current_source_mode = "local"
        backend.current_json_file_path = out_json if out_json.exists() else None
        backend.current_json_url = ""
        backend._cache_current_as_base()

        parts = [
            f"{len(output_items)} domande",
            f"target {target_questions}",
            f"pagine {pages_for_budget}",
            f"max {MAX_QUESTIONS_PER_PDF}",
            "quiz senza immagini",
        ]
        if no_extractable_text:
            parts.append("analisi scansione")
        if quick_mode:
            parts.append("quick-check")
        else:
            parts.append("complete")
        if errors:
            parts.append(f"{len(errors)} chunk non elaborati")
        parts.append(f"JSON: {out_json.name}")
        _progress("PDF: completato (100%)")
        return True, "PDF elaborato: " + " · ".join(parts)

    return ok, msg


# ═══════════════════════════════════════════════════════════════════════════════
# DIFF DA APPLICARE AI FILE ESISTENTI
# ═══════════════════════════════════════════════════════════════════════════════

# ── quiznova_backend.py  ── (5 modifiche) ─────────────────────────────────────
#
# 1. QuizItem dataclass — aggiungi campo image_url:
#
#    @dataclass
#    class QuizItem:
#        id:            str
#        question:      str
#        choices:       list[str]
#        correct_index: int
#        chapter:       str
#        explanation:   str = ""
#  →    image_url:     str = ""      ← AGGIUNGI questa riga
#
#
# 2. _normalize_item() — leggi image_url dal raw dict:
#
#    return QuizItem(
#        id          = ...,
#        question    = q,
#        choices     = choices,
#        correct_index = ci,
#        chapter     = ...,
#        explanation = ...,
#  →    image_url   = str(row.get("image_url", "") or "").strip(),  ← AGGIUNGI
#    )
#
#
# 3. _as_raw_question() — includi image_url nell'output:
#
#    return {
#        "id":           ...,
#        "question":     ...,
#        "chapter":      ...,
#        "choices":      [...],
#        "correctIndex": ...,
#        "explanation":  ...,
#  →    "image_url":    str(it.image_url or ""),   ← AGGIUNGI
#    }
#
#
# 4. current_quiz_payload() — passa image_url al QML:
#
#    out.append({
#        "index":        idx,
#        "chapter":      it.chapter,
#        "question":     it.question,
#        "choices":      it.choices,
#        "correctIndex": it.correct_index,
#  →    "image_url":    it.image_url,   ← AGGIUNGI
#    })
#
#
# 5. correct_all() — includi image_url nel detail (per la vista correzione):
#
#    detail.append({
#        "index":        i,
#        "selected":     sel,
#        "correctIndex": it.correct_index,
#        "chapter":      it.chapter,
#        "question":     it.question,
#        "correctText":  it.choices[it.correct_index],
#        "isCorrect":    is_ok,
#  →    "image_url":    it.image_url,   ← AGGIUNGI
#        "choices":      [...],
#    })
#
#
# 6. Metodo load_from_pdf() — aggiungi dopo load_from_paste():
#
#    def load_from_pdf(self, path: str) -> tuple[bool, str]:
#        """Genera quiz da PDF dispense tramite GPT-4o-mini vision."""
#        from pdf_quiz_generator import load_from_pdf_module
#        return load_from_pdf_module(self, path)
#
#
# ── main_quiznova.py  ── (2 modifiche) ────────────────────────────────────────
#
# 1. Worker — aggiungi dopo LoadUrlWorker:
#
#    class LoadPdfWorker(_NetWorker):
#        def __init__(self, backend: QuizNovaBackend, path: str) -> None:
#            super().__init__()
#            self._backend = backend
#            self._path = path
#
#        def run(self) -> None:
#            ok, msg = self._backend.load_from_pdf(self._path)
#            self._emit(ok, msg)
#
#
# 2. Slot bridge — aggiungi dopo loadFromPaste():
#
#    @Slot(str)
#    def loadFromPdf(self, path: str) -> None:
#        self._set_status("Generazione quiz dal PDF in corso… (1-2 min)")
#        worker = LoadPdfWorker(self.backend, path)
#        def _done(ok: bool, msg: str) -> None:
#            self._set_loading(False)
#            self._sync_dataset()
#            self._set_status(msg)
#        self._start_worker(worker, _done)
#
#
# ── Main.qml  ── (5 modifiche) ────────────────────────────────────────────────
#
# 1. FileDialog — aggiungi subito dopo il fileDialog esistente:
#
#    FileDialog {
#        id: pdfFileDialog
#        title: "Seleziona PDF dispense"
#        nameFilters: ["PDF files (*.pdf)"]
#        onAccepted: {
#            const p = selectedFile.toString().replace("file://", "")
#            if (!p || p.length === 0) { isLoading = false; return }
#            userInitiatedLoad = true
#            datasetSelectionInProgress = true
#            showMainArea = false
#            isLoading = true
#            quizBridge.loadFromPdf(decodeURIComponent(p))
#        }
#        onRejected: {
#            isLoading = false
#            datasetSelectionInProgress = false
#            showMainArea = (inQuizMode || (hasDataset && sessionDatasetChosen))
#        }
#    }
#
#
# 2. Pulsante toolbar — aggiungi dopo il pulsante "Scegli file":
#
#    FancyButton {
#        id: loadPdfBtn
#        text: "PDF \u2192 Quiz"
#        Layout.fillWidth: true
#        Layout.minimumWidth: 170
#        Layout.preferredHeight: 48
#        font.pixelSize: 19
#        font.bold: true
#        enabled: quizBridge.aiKey !== ""
#        opacity: enabled ? 1.0 : 0.5
#        ToolTip.visible: hovered && !enabled
#        ToolTip.text: "Richiede chiave OpenAI (Easter Egg)"
#        onClicked: {
#            showUrlBox = false; showPasteBox = false
#            datasetSelectionInProgress = true
#            showMainArea = false; isLoading = true
#            pdfFileDialog.open()
#        }
#    }
#
#
# 3. Quiz card — immagine domanda (modalità quiz attivo):
#    Aggiungi DOPO il Rectangle{} del titolo domanda e PRIMA di Text{rowChapter}
#    (circa riga 956):
#
#    Image {
#        Layout.fillWidth: true
#        Layout.maximumHeight: 260
#        fillMode: Image.PreserveAspectFit
#        source: (!corrected && modelData.image_url) ? modelData.image_url : ""
#        visible: source !== ""
#        smooth: true; mipmap: true
#        layer.enabled: true
#        layer.effect: null
#    }
#
#
# 4. Quiz card — immagine domanda (modalità correzione, corrected == true):
#    Aggiungi NELLO STESSO POSTO ma con sorgente da resultRow:
#    (sostituisce o affianca il blocco precedente con logica unificata)
#
#    Image {
#        Layout.fillWidth: true
#        Layout.maximumHeight: 260
#        fillMode: Image.PreserveAspectFit
#        source: corrected
#                ? (resultRow.image_url || "")
#                : (modelData.image_url || "")
#        visible: source !== ""
#        smooth: true; mipmap: true
#    }
#    // Questo blocco UNICO sostituisce i due sopra.
#
#
# 5. Snapshot dialog — immagine nel riquadro domanda:
#    Aggiungi DENTRO il ColumnLayout dello snapshot (snapCol),
#    DOPO il Rectangle{} con il testo della domanda (~riga 1775):
#
#    Image {
#        Layout.fillWidth: true
#        Layout.maximumHeight: 220
#        fillMode: Image.PreserveAspectFit
#        source: (modelData.image_url || "")
#        visible: source !== ""
#        smooth: true; mipmap: true
#    }
#
#
# ── PegasoQuiz.spec  ── (1 modifica) ──────────────────────────────────────────
#
# Aggiungi in hiddenimports:
#    "pdf_quiz_generator",
#    "pdfminer", "pdfminer.high_level", "pdfminer.layout",
#    "pdfminer.pdfpage", "pdfminer.pdfinterp", "pdfminer.converter",
#    "pypdf", "pypdf._page", "pypdf.filters",
#    "PIL", "PIL.Image",
#
# ═══════════════════════════════════════════════════════════════════════════════
