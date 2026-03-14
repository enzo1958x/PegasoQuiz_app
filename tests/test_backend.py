"""test_backend.py — unit test per QuizNovaBackend (funzioni pure).

Copertura:
  - _extract_choices_and_correct: formati choices (dict/string/legacy §§§[OK])
  - _normalize_item: campi obbligatori, alias, valori mancanti
  - _hash / _qid: formato e stabilità
  - _norm_stats_name: normalizzazione nomi dataset

I test non richiedono rete, Qt, PySide6 né filesystem reale.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Chiavi minime per copyright_crypto (importato transitivamente)
os.environ.setdefault("QUIZNOVA_ENC_KEY", "a" * 64)
os.environ.setdefault("QUIZNOVA_MAC_KEY", "b" * 64)
# Evita connessioni Supabase nei test
os.environ.setdefault("QUIZNOVA_SUPABASE_URL", "")
os.environ.setdefault("QUIZNOVA_SUPABASE_ANON_KEY", "")

from quiznova_backend import QuizNovaBackend, QuizItem  # noqa: E402


@pytest.fixture(scope="module")
def backend(tmp_path_factory):
    """Istanza backend con home temporanea per evitare side-effect su ~/.quiznova."""
    tmp = tmp_path_factory.mktemp("quiznova")
    os.environ["HOME"] = str(tmp)
    return QuizNovaBackend()


# ── _extract_choices_and_correct ──────────────────────────────────────────────

class TestExtractChoices:
    def test_dict_choices_correct_flag(self, backend):
        row = {
            "question": "Q?",
            "choices": [
                {"text": "A", "correct": False},
                {"text": "B", "correct": True},
                {"text": "C", "correct": False},
            ],
        }
        choices, ci = backend._extract_choices_and_correct(row)
        assert choices == ["A", "B", "C"]
        assert ci == 1

    def test_legacy_marker(self, backend):
        """Formato legacy: stringa con §§§[OK]."""
        row = {
            "question": "Q?",
            "choices": ["Sbagliata", "Corretta §§§[OK]", "Altra"],
        }
        choices, ci = backend._extract_choices_and_correct(row)
        assert ci == 1
        assert "[OK]" not in choices[1]
        assert "§§§" not in choices[1]

    def test_correct_index_field(self, backend):
        row = {
            "question": "Q?",
            "choices": ["X", "Y", "Z"],
            "correctIndex": 2,
        }
        _, ci = backend._extract_choices_and_correct(row)
        assert ci == 2

    def test_correct_answer_letter(self, backend):
        row = {
            "question": "Q?",
            "choices": ["X", "Y", "Z"],
            "correctAnswer": "B",
        }
        _, ci = backend._extract_choices_and_correct(row)
        assert ci == 1

    def test_correct_answer_text_match(self, backend):
        row = {
            "question": "Q?",
            "choices": ["prima", "seconda", "terza"],
            "correctAnswer": "terza",
        }
        _, ci = backend._extract_choices_and_correct(row)
        assert ci == 2

    def test_no_correct_returns_none(self, backend):
        row = {"question": "Q?", "choices": ["A", "B"]}
        _, ci = backend._extract_choices_and_correct(row)
        assert ci is None

    def test_alias_answers(self, backend):
        """Campo 'answers' come alias di 'choices'."""
        row = {
            "question": "Q?",
            "answers": [
                {"text": "Sì", "correct": True},
                {"text": "No", "correct": False},
            ],
        }
        choices, ci = backend._extract_choices_and_correct(row)
        assert ci == 0
        assert choices[0] == "Sì"


# ── _normalize_item ────────────────────────────────────────────────────────────

class TestNormalizeItem:
    def _valid_row(self):
        return {
            "id": "Q001",
            "question": "Qual è la risposta?",
            "chapter": "Cap 1",
            "choices": [
                {"text": "42", "correct": True},
                {"text": "43", "correct": False},
            ],
            "explanation": "Perché sì.",
        }

    def test_valid_row(self, backend):
        item = backend._normalize_item(self._valid_row())
        assert isinstance(item, QuizItem)
        assert item.question == "Qual è la risposta?"
        assert item.correct_index == 0
        assert item.chapter == "Cap 1"
        assert item.explanation == "Perché sì."

    def test_missing_question_returns_none(self, backend):
        row = self._valid_row()
        del row["question"]
        assert backend._normalize_item(row) is None

    def test_too_few_choices_returns_none(self, backend):
        row = self._valid_row()
        row["choices"] = [{"text": "Solo una", "correct": True}]
        assert backend._normalize_item(row) is None

    def test_no_correct_returns_none(self, backend):
        row = self._valid_row()
        row["choices"] = [
            {"text": "A", "correct": False},
            {"text": "B", "correct": False},
        ]
        assert backend._normalize_item(row) is None

    def test_alias_domanda(self, backend):
        row = self._valid_row()
        row["domanda"] = row.pop("question")
        item = backend._normalize_item(row)
        assert item is not None
        assert item.question == "Qual è la risposta?"

    def test_default_chapter(self, backend):
        row = self._valid_row()
        del row["chapter"]
        item = backend._normalize_item(row)
        assert item is not None
        assert item.chapter == "Generale"

    def test_image_url_field(self, backend):
        row = self._valid_row()
        row["image_url"] = "file:///tmp/img.png"
        item = backend._normalize_item(row)
        assert item is not None
        assert item.image_url == "file:///tmp/img.png"

    def test_non_dict_input(self, backend):
        assert backend._normalize_item("not a dict") is None
        assert backend._normalize_item(None) is None
        assert backend._normalize_item([]) is None


# ── _hash / _qid ──────────────────────────────────────────────────────────────

class TestHashAndQid:
    def test_hash_length(self, backend):
        assert len(backend._hash("qualsiasi testo")) == 16

    def test_hash_stable(self, backend):
        assert backend._hash("test") == backend._hash("test")

    def test_hash_different_inputs(self, backend):
        assert backend._hash("a") != backend._hash("b")

    def test_qid_custom_id(self, backend):
        item = QuizItem(
            id="CUSTOM-123", question="Q?", choices=["A", "B"],
            correct_index=0, chapter="C",
        )
        assert backend._qid(item) == "CUSTOM-123"

    def test_qid_fallback_to_hash(self, backend):
        """ID auto-generati (Q[0-9]+) fanno fallback a hash della domanda."""
        item = QuizItem(
            id="Q001", question="Domanda di test", choices=["A", "B"],
            correct_index=0, chapter="C",
        )
        qid = backend._qid(item)
        assert qid.startswith("H")
        assert len(qid) == 17   # "H" + 16 caratteri hash

    def test_qid_empty_id(self, backend):
        item = QuizItem(
            id="", question="Altra domanda", choices=["A", "B"],
            correct_index=0, chapter="C",
        )
        qid = backend._qid(item)
        assert qid.startswith("H")


# ── _norm_stats_name ──────────────────────────────────────────────────────────

class TestNormStatsName:
    def test_lowercase(self, backend):
        assert backend._norm_stats_name("Quiz AVANZATO") == "quiz avanzato"

    def test_strip(self, backend):
        assert backend._norm_stats_name("  spazi  ") == "spazi"

    def test_empty(self, backend):
        assert backend._norm_stats_name("") == ""

    def test_unicode(self, backend):
        result = backend._norm_stats_name("Capitolo Più")
        assert result == "capitolo più"
