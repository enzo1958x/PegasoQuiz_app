from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Property, Signal, Slot
from PySide6.QtWidgets import QApplication
from PySide6.QtQml import QQmlApplicationEngine

from quiznova_backend import QuizNovaBackend

logger = logging.getLogger("quiznova.bridge")


# ── Worker threads per operazioni di rete ────────────────────────────────────

class _NetWorker(QThread):
    """Thread base per operazioni di rete che restituiscono (ok, msg)."""
    finished = Signal(bool, str)

    def _emit(self, ok: bool, msg: str) -> None:
        self.finished.emit(ok, msg)


class LoadFileWorker(_NetWorker):
    def __init__(self, backend: QuizNovaBackend, path: str) -> None:
        super().__init__()
        self._backend = backend
        self._path = path

    def run(self) -> None:
        try:
            ok, msg = self._backend.load_from_file(self._path)
        except Exception as e:
            logger.exception("LoadFileWorker crash")
            ok, msg = False, f"Errore interno caricamento file: {e}"
        self._emit(ok, msg)


class LoadUrlWorker(_NetWorker):
    def __init__(self, backend: QuizNovaBackend, url: str) -> None:
        super().__init__()
        self._backend = backend
        self._url = url

    def run(self) -> None:
        try:
            ok, msg = self._backend.load_from_url(self._url)
        except Exception as e:
            logger.exception("LoadUrlWorker crash")
            ok, msg = False, f"Errore interno caricamento URL: {e}"
        self._emit(ok, msg)


class LoadPdfWorker(_NetWorker):
    """Estrae testo+immagini dal PDF e genera domande via GPT-4o-mini vision."""
    progress = Signal(str)

    def __init__(self, backend: QuizNovaBackend, path: str, run_mode: str = "complete") -> None:
        super().__init__()
        self._backend = backend
        self._path = path
        self._run_mode = str(run_mode or "complete")

    def _on_progress(self, msg: str) -> None:
        self.progress.emit(str(msg or ""))

    def run(self) -> None:
        try:
            ok, msg = self._backend.load_from_pdf(self._path, progress_cb=self._on_progress, run_mode=self._run_mode)
        except Exception as e:
            logger.exception("LoadPdfWorker crash")
            ok, msg = False, f"Errore interno elaborazione PDF: {e}"
        self._emit(ok, msg)


class CloudLoginWorker(_NetWorker):
    def __init__(self, backend: QuizNovaBackend, username: str, password: str) -> None:
        super().__init__()
        self._backend = backend
        self._username = username
        self._password = password

    def run(self) -> None:
        try:
            ok, msg = self._backend.supabase_sign_in_password(self._username, self._password)
        except Exception as e:
            logger.exception("CloudLoginWorker crash")
            ok, msg = False, f"Errore interno login cloud: {e}"
        self._emit(ok, msg)


class CloudEntriesWorker(_NetWorker):
    def __init__(self, backend: QuizNovaBackend, manifest_url: str) -> None:
        super().__init__()
        self._backend = backend
        self._manifest_url = manifest_url

    def run(self) -> None:
        try:
            ok, msg = self._backend.cloud_fetch_entries(self._manifest_url)
        except Exception as e:
            logger.exception("CloudEntriesWorker crash")
            ok, msg = False, f"Errore interno recupero cloud: {e}"
        self._emit(ok, msg)


class CloudLoadSelectedWorker(_NetWorker):
    def __init__(self, backend: QuizNovaBackend, url: str, name: str) -> None:
        super().__init__()
        self._backend = backend
        self._url = url
        self._name = name

    def run(self) -> None:
        try:
            ok, msg = self._backend.cloud_load_selected(self._url, self._name)
        except Exception as e:
            logger.exception("CloudLoadSelectedWorker crash")
            ok, msg = False, f"Errore interno caricamento dataset cloud: {e}"
        self._emit(ok, msg)


class AiWorker(_NetWorker):
    resultReady = Signal(str)   # testo risposta AI

    def __init__(self, backend: QuizNovaBackend, item: dict) -> None:
        super().__init__()
        self._backend = backend
        self._item = item

    def run(self) -> None:
        try:
            ok, msg, txt = self._backend.ask_ai_explain(self._item)
        except Exception as e:
            logger.exception("AiWorker crash")
            ok, msg, txt = False, f"Errore interno AI: {e}", ""
        self.resultReady.emit(txt or "")
        self._emit(ok, msg)


class CorrectAllWorker(_NetWorker):
    """Esegue la correzione (incluse scritture cloud) su un thread separato."""
    resultReady = Signal(str, str, str, int)  # result_json, stats_json, wrong_stats_json, wrong_count

    def __init__(self, backend: QuizNovaBackend, answers: list[int]) -> None:
        super().__init__()
        self._backend = backend
        self._answers = answers

    def run(self) -> None:
        try:
            result = self._backend.correct_all(self._answers)
            # Pre-calcola le stats sul thread worker così il main thread
            # non deve fare altre chiamate cloud bloccanti.
            stats_json = json.dumps(self._backend.stats(), ensure_ascii=False)
            wrong_stats_json = json.dumps(self._backend.wrong_stats(), ensure_ascii=False)
            wrong_count = int(self._backend.wrong_count())
            self.resultReady.emit(
                json.dumps(result, ensure_ascii=False),
                stats_json,
                wrong_stats_json,
                wrong_count,
            )
            self._emit(result.get("ok", False), str(result.get("message", "")))
        except Exception as e:
            logger.exception("CorrectAllWorker crash")
            self.resultReady.emit(json.dumps({"ok": False, "message": str(e)}), "[]", "[]", 0)
            self._emit(False, f"Errore correzione: {e}")


class GenerateWrongQuizWorker(_NetWorker):
    """Carica il bacino errori (fetch cloud) e genera il quiz su un thread separato."""
    quizReady = Signal(str)   # JSON quiz payload

    def __init__(self, backend: QuizNovaBackend, count: int) -> None:
        super().__init__()
        self._backend = backend
        self._count = count

    def run(self) -> None:
        try:
            if not self._backend.is_wrong_mode:
                ok_mode, msg_mode = self._backend.load_wrong_only()
                if not ok_mode:
                    self.quizReady.emit("[]")
                    self._emit(False, msg_mode)
                    return
            ok, msg = self._backend.generate_quiz(self._count)
            self.quizReady.emit(json.dumps(self._backend.current_quiz_payload(), ensure_ascii=False))
            self._emit(ok, msg)
        except Exception as e:
            logger.exception("GenerateWrongQuizWorker crash")
            self.quizReady.emit("[]")
            self._emit(False, f"Errore generazione quiz errori: {e}")


# ── Bridge principale ─────────────────────────────────────────────────────────

class QuizNovaBridge(QObject):
    statusChanged = Signal()
    datasetNameChanged = Signal()
    poolChanged = Signal()
    chaptersChanged = Signal()
    selectedChaptersChanged = Signal()
    quizChanged = Signal()
    statsChanged = Signal()
    wrongStatsChanged = Signal()
    cloudEntriesChanged = Signal()
    cloudAuthChanged = Signal()
    resultChanged = Signal()
    percentModeChanged = Signal()
    profilesChanged = Signal()
    snapshotChanged = Signal()
    easterChanged = Signal()
    aiResultChanged = Signal()
    wrongModeChanged = Signal()
    wrongPayloadChanged = Signal()
    isLoadingChanged = Signal()   # ← nuovo: feedback per BusyIndicator

    def __init__(self) -> None:
        super().__init__()
        self.backend = QuizNovaBackend()

        self._status = "Pronto"
        self._dataset_name = ""
        self._chapters_json = "[]"
        self._selected_chapters_json = "[]"
        self._quiz_json = "[]"
        self._stats_json = "[]"
        self._wrong_stats_json = "[]"
        self._pool_used = 0
        self._pool_total = 0
        self._cloud_entries_json = "[]"
        self._cloud_logged = False
        self._cloud_manifest_url = self.backend.get_pref("cloud_manifest_url", "")
        self._cloud_last_user = self.backend.get_pref("supabase_last_user", "")
        self._percent_mode = bool(self.backend.use_percent_mode)
        self._profiles_json = json.dumps(self.backend.percent_profiles(), ensure_ascii=False)
        self._percent_map_json = json.dumps(self.backend.get_percent_map(), ensure_ascii=False)
        self._last_result_json = "{}"
        self._snapshot_json = "[]"
        self._ai_key = self.backend.get_pref("openai_api_key", "")
        self._ai_result_text = ""
        self._wrong_mode = bool(self.backend.is_wrong_mode)
        self._wrong_count = int(self.backend.wrong_count())
        self._wrong_payload_json = "[]"
        self._is_loading = False

        # Riferimenti ai worker attivi (evita garbage collection prematura)
        self._active_workers: list[QThread] = []

        self._sync_all()

    # ── Helpers interni ───────────────────────────────────────────────────────

    def _set_loading(self, v: bool) -> None:
        # Se stiamo spegnendo il loading, lo facciamo solo se non ci sono
        # altri worker ancora attivi (evita di spegnere il BusyIndicator
        # mentre una seconda operazione è ancora in corso).
        if not v and self._active_workers:
            return
        if self._is_loading != v:
            self._is_loading = v
            self.isLoadingChanged.emit()

    def _start_worker(self, worker: _NetWorker, on_finished) -> None:
        """Registra e avvia un worker; lo rimuove dalla lista quando termina."""
        self._active_workers.append(worker)

        def _on_finished_wrapper(ok: bool, msg: str) -> None:
            # Rimuove il worker prima di chiamare on_finished, così
            # _set_loading(False) può controllare correttamente se ci sono
            # altri worker ancora attivi.
            if worker in self._active_workers:
                self._active_workers.remove(worker)
            on_finished(ok, msg)

        worker.finished.connect(_on_finished_wrapper)
        self._set_loading(True)
        worker.start()

    @Slot()
    def shutdown(self) -> None:
        """Termina i worker prima che Qt distrugga i QThread (evita SIGABRT in uscita)."""
        workers = list(self._active_workers)
        if not workers:
            return

        for worker in workers:
            try:
                worker.requestInterruption()
            except Exception:
                logger.debug("requestInterruption fallita", exc_info=True)

        for worker in workers:
            if not worker.isRunning():
                continue
            if worker.wait(1500):
                continue
            logger.warning(
                "Worker %s ancora attivo in chiusura: forzo terminate()",
                type(worker).__name__,
            )
            try:
                worker.terminate()
                worker.wait(1000)
            except Exception:
                logger.warning("terminate/wait fallita", exc_info=True)

        self._active_workers = [w for w in self._active_workers if w.isRunning()]
        self._set_loading(False)

    def _sync_all(self) -> None:
        """Aggiorna tutte le proprietà cache e notifica la UI."""
        self._dataset_name = self.backend.dataset_name
        self._chapters_json = json.dumps(self.backend.chapters(), ensure_ascii=False)
        self._selected_chapters_json = json.dumps(self.backend.selected_chapters, ensure_ascii=False)
        self._quiz_json = json.dumps(self.backend.current_quiz_payload(), ensure_ascii=False)
        self._stats_json = json.dumps(self.backend.stats(), ensure_ascii=False)
        self._wrong_stats_json = json.dumps(self.backend.wrong_stats(), ensure_ascii=False)
        self._pool_used = int(self.backend.last_pool_used)
        self._pool_total = int(self.backend.last_pool_total)
        self._cloud_entries_json = json.dumps(self.backend.cloud_entries_payload(), ensure_ascii=False)
        self._cloud_logged = bool(self.backend.current_user and self.backend.auth_access_token)
        self._cloud_manifest_url = self.backend.get_pref("cloud_manifest_url", "")
        self._cloud_last_user = self.backend.get_pref("supabase_last_user", "")
        self._percent_mode = bool(self.backend.use_percent_mode)
        self._profiles_json = json.dumps(self.backend.percent_profiles(), ensure_ascii=False)
        self._percent_map_json = json.dumps(self.backend.get_percent_map(), ensure_ascii=False)
        self._wrong_mode = bool(self.backend.is_wrong_mode)
        self._wrong_count = int(self.backend.wrong_count())

        self.datasetNameChanged.emit()
        self.chaptersChanged.emit()
        self.selectedChaptersChanged.emit()
        self.quizChanged.emit()
        self.statsChanged.emit()
        self.wrongStatsChanged.emit()
        self.poolChanged.emit()
        self.cloudEntriesChanged.emit()
        self.cloudAuthChanged.emit()
        self.percentModeChanged.emit()
        self.profilesChanged.emit()
        self.wrongModeChanged.emit()

    def _sync_dataset(self) -> None:
        """Aggiornamento mirato: solo dataset, capitoli e pool."""
        self._dataset_name = self.backend.dataset_name
        self._chapters_json = json.dumps(self.backend.chapters(), ensure_ascii=False)
        self._selected_chapters_json = json.dumps(self.backend.selected_chapters, ensure_ascii=False)
        self._pool_used = int(self.backend.last_pool_used)
        self._pool_total = int(self.backend.last_pool_total)
        self._wrong_count = int(self.backend.wrong_count())
        self._wrong_mode = bool(self.backend.is_wrong_mode)
        self.datasetNameChanged.emit()
        self.chaptersChanged.emit()
        self.selectedChaptersChanged.emit()
        self.poolChanged.emit()
        self.wrongModeChanged.emit()

    def _sync_stats(self) -> None:
        """Aggiornamento mirato: solo statistiche."""
        self._stats_json = json.dumps(self.backend.stats(), ensure_ascii=False)
        self._wrong_stats_json = json.dumps(self.backend.wrong_stats(), ensure_ascii=False)
        self._wrong_count = int(self.backend.wrong_count())
        self.statsChanged.emit()
        self.wrongStatsChanged.emit()
        self.wrongModeChanged.emit()

    def _sync_cloud_auth(self) -> None:
        """Aggiornamento mirato: solo stato autenticazione cloud."""
        self._cloud_logged = bool(self.backend.current_user and self.backend.auth_access_token)
        self._cloud_manifest_url = self.backend.get_pref("cloud_manifest_url", "")
        self._cloud_last_user = self.backend.get_pref("supabase_last_user", "")
        self.cloudAuthChanged.emit()

    def _set_status(self, msg: str) -> None:
        self._status = msg
        self.statusChanged.emit()

    # ── Proprietà esposte a QML ───────────────────────────────────────────────

    @Property(bool, notify=isLoadingChanged)
    def isLoading(self) -> bool:
        return self._is_loading

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(str, notify=datasetNameChanged)
    def datasetName(self) -> str:
        return self._dataset_name

    @Property(int, notify=poolChanged)
    def poolUsed(self) -> int:
        return self._pool_used

    @Property(int, notify=poolChanged)
    def poolTotal(self) -> int:
        return self._pool_total

    @Property(str, notify=chaptersChanged)
    def chaptersJson(self) -> str:
        return self._chapters_json

    @Property(str, notify=selectedChaptersChanged)
    def selectedChaptersJson(self) -> str:
        return self._selected_chapters_json

    @Property(str, notify=quizChanged)
    def quizJson(self) -> str:
        return self._quiz_json

    @Property(str, notify=statsChanged)
    def statsJson(self) -> str:
        return self._stats_json

    @Property(str, notify=wrongStatsChanged)
    def wrongStatsJson(self) -> str:
        return self._wrong_stats_json

    @Property(str, notify=cloudEntriesChanged)
    def cloudEntriesJson(self) -> str:
        return self._cloud_entries_json

    @Property(bool, notify=cloudAuthChanged)
    def cloudLogged(self) -> bool:
        return self._cloud_logged

    @Property(str, notify=cloudAuthChanged)
    def cloudManifestUrl(self) -> str:
        return self._cloud_manifest_url

    @Property(str, notify=cloudAuthChanged)
    def cloudLastUser(self) -> str:
        return self._cloud_last_user

    @Property(str, notify=resultChanged)
    def lastResultJson(self) -> str:
        return self._last_result_json

    @Property(bool, notify=percentModeChanged)
    def percentMode(self) -> bool:
        return self._percent_mode

    @Property(str, notify=profilesChanged)
    def profilesJson(self) -> str:
        return self._profiles_json

    @Property(str, notify=profilesChanged)
    def percentMapJson(self) -> str:
        return self._percent_map_json

    @Property(str, notify=snapshotChanged)
    def snapshotJson(self) -> str:
        return self._snapshot_json

    @Property(str, notify=easterChanged)
    def aiKey(self) -> str:
        return self._ai_key

    @Property(str, notify=aiResultChanged)
    def aiResultText(self) -> str:
        return self._ai_result_text

    @Property(bool, notify=wrongModeChanged)
    def wrongMode(self) -> bool:
        return self._wrong_mode

    @Property(int, notify=wrongModeChanged)
    def wrongCount(self) -> int:
        return self._wrong_count

    @Property(str, notify=wrongPayloadChanged)
    def wrongPayloadJson(self) -> str:
        return self._wrong_payload_json

    # ── Slot: caricamento dataset (async) ─────────────────────────────────────

    @Slot(str)
    def loadFromFile(self, path: str) -> None:
        self._set_status("Caricamento file in corso…")
        worker = LoadFileWorker(self.backend, path)

        def _done(ok: bool, msg: str) -> None:
            self._set_loading(False)
            self._sync_all()
            self._set_status(msg)

        self._start_worker(worker, _done)

    @Slot(str)
    def loadFromUrl(self, url: str) -> None:
        self._set_status("Caricamento URL in corso…")
        worker = LoadUrlWorker(self.backend, url)

        def _done(ok: bool, msg: str) -> None:
            self._set_loading(False)
            self._sync_all()
            self._set_status(msg)

        self._start_worker(worker, _done)

    @Slot(str)
    def loadFromPaste(self, text: str) -> None:
        # Il parsing da testo è istantaneo: nessun thread necessario
        _, msg = self.backend.load_from_paste(text)
        self._sync_all()
        self._set_status(msg)

    @Slot(str)
    def loadFromPdf(self, path: str) -> None:
        self.loadFromPdfMode(path, "complete")

    @Slot(str, str)
    def loadFromPdfMode(self, path: str, mode: str) -> None:
        run_mode = str(mode or "complete").strip().lower()
        if run_mode not in ("quick", "complete"):
            run_mode = "complete"
        self._set_status(f"PDF: avvio elaborazione… ({run_mode})")
        worker = LoadPdfWorker(self.backend, path, run_mode=run_mode)

        def _progress(msg: str) -> None:
            if msg:
                self._set_status(msg)

        def _done(ok: bool, msg: str) -> None:
            self._set_loading(False)
            self._sync_all()
            self._set_status(msg)

        worker.progress.connect(_progress)
        self._start_worker(worker, _done)

    # ── Slot: selezione capitoli e generazione quiz ───────────────────────────

    @Slot(str)
    def setSelectedChapters(self, chapters_json: str) -> None:
        try:
            chapters = json.loads(chapters_json)
            if not isinstance(chapters, list):
                chapters = []
        except Exception:
            logger.warning("setSelectedChapters: JSON non valido", exc_info=True)
            chapters = []
        self.backend.set_selected_chapters([str(c) for c in chapters])
        # Aggiornamento mirato: solo pool e capitoli selezionati
        self._selected_chapters_json = json.dumps(self.backend.selected_chapters, ensure_ascii=False)
        self._pool_used = int(self.backend.last_pool_used)
        self._pool_total = int(self.backend.last_pool_total)
        self.selectedChaptersChanged.emit()
        self.poolChanged.emit()
        self._set_status("Capitoli aggiornati")

    @Slot(int)
    def generateQuiz(self, count: int) -> None:
        if self.backend.is_wrong_mode:
            self.backend.load_base_mode()
        _, msg = self.backend.generate_quiz(count)
        self._quiz_json = json.dumps(self.backend.current_quiz_payload(), ensure_ascii=False)
        self._pool_used = int(self.backend.last_pool_used)
        self._pool_total = int(self.backend.last_pool_total)
        self._wrong_mode = bool(self.backend.is_wrong_mode)
        self.quizChanged.emit()
        self.poolChanged.emit()
        self.wrongModeChanged.emit()
        self._set_status(msg)

    @Slot(result=int)
    def prepareBasePoolForPopup(self) -> int:
        if self.backend.is_wrong_mode:
            self.backend.load_base_mode()
        self._sync_dataset()
        return int(self.backend.last_pool_total)

    @Slot(result=int)
    def prepareWrongPoolForPopup(self) -> int:
        self._wrong_count = int(self.backend.wrong_count())
        self.wrongModeChanged.emit()
        return self._wrong_count

    @Slot(int)
    def generateWrongQuiz(self, count: int) -> None:
        worker = GenerateWrongQuizWorker(self.backend, count)

        def _on_quiz(quiz_json: str) -> None:
            if worker in self._active_workers:
                self._active_workers.remove(worker)
            self._set_loading(False)
            self._quiz_json = quiz_json
            self._pool_used = int(self.backend.last_pool_used)
            self._pool_total = int(self.backend.last_pool_total)
            self._wrong_mode = bool(self.backend.is_wrong_mode)
            self.quizChanged.emit()
            self.poolChanged.emit()
            self.wrongModeChanged.emit()

        worker.quizReady.connect(_on_quiz)
        self._start_worker(worker, lambda ok, msg: self._set_status(msg))

    @Slot(str)
    def correctAll(self, answers_json: str) -> None:
        try:
            answers = json.loads(answers_json)
            if not isinstance(answers, list):
                answers = []
        except Exception:
            logger.warning("correctAll: JSON risposte non valido", exc_info=True)
            answers = []

        worker = CorrectAllWorker(self.backend, [int(x) for x in answers])

        def _on_result(result_json: str, stats_json: str, wrong_stats_json: str, wrong_count: int) -> None:
            # Spegne il loading subito, prima di mostrare il popup risultato
            if worker in self._active_workers:
                self._active_workers.remove(worker)
            self._set_loading(False)
            self._last_result_json = result_json
            self.resultChanged.emit()
            self._pool_used = int(self.backend.last_pool_used)
            self._pool_total = int(self.backend.last_pool_total)
            self.poolChanged.emit()
            self._stats_json = stats_json
            self._wrong_stats_json = wrong_stats_json
            self._wrong_count = wrong_count
            self.statsChanged.emit()
            self.wrongStatsChanged.emit()
            self.wrongModeChanged.emit()

        worker.resultReady.connect(_on_result)
        self._start_worker(worker, lambda ok, msg: self._set_status(msg or "Correzione terminata"))

    @Slot()
    def clearQuiz(self) -> None:
        _, msg = self.backend.clear_quiz()
        self._quiz_json = "[]"
        self.quizChanged.emit()
        self._set_status(msg)

    @Slot()
    def resetPool(self) -> None:
        _, msg = self.backend.reset_pool()
        self._pool_used = int(self.backend.last_pool_used)
        self._pool_total = int(self.backend.last_pool_total)
        self.poolChanged.emit()
        self._set_status(msg)

    # ── Slot: cloud (async) ───────────────────────────────────────────────────

    @Slot(str, str)
    def cloudLogin(self, username: str, password: str) -> None:
        self._set_status("Login in corso…")
        worker = CloudLoginWorker(self.backend, username, password)

        def _done(ok: bool, msg: str) -> None:
            self._set_loading(False)
            self._sync_cloud_auth()
            if ok:
                self._sync_stats()
            self._set_status(msg)

        self._start_worker(worker, _done)

    @Slot()
    def toggleContextMode(self) -> None:
        self._sync_dataset()
        self._set_status("Cambio contesto disattivato: usa 'Quiz' o 'Quiz errori'")

    @Slot()
    def clearWrongQuestions(self) -> None:
        _, msg = self.backend.clear_wrong_questions()
        self._wrong_count = 0
        self.wrongModeChanged.emit()
        self._set_status(msg)

    @Slot(str)
    def setCloudManifestUrl(self, manifest_url: str) -> None:
        self.backend.set_pref("cloud_manifest_url", str(manifest_url or "").strip())
        self._cloud_manifest_url = self.backend.get_pref("cloud_manifest_url", "")
        self.cloudAuthChanged.emit()
        self._set_status("Manifest cloud aggiornato")

    @Slot(str)
    def setAiKey(self, ai_key: str) -> None:
        self.backend.set_pref("openai_api_key", str(ai_key or "").strip())
        self._ai_key = self.backend.get_pref("openai_api_key", "")
        self._ai_result_text = ""
        self.easterChanged.emit()
        self._set_status("Chiave AI aggiornata")

    @Slot(str)
    def changePassword(self, new_password: str) -> None:
        _, msg = self.backend.supabase_change_password(new_password)
        self._set_status(msg)

    @Slot(str)
    def cloudLoadEntries(self, manifest_url: str) -> None:
        self._set_status("Recupero lista dataset dal cloud…")
        worker = CloudEntriesWorker(self.backend, manifest_url)

        def _done(ok: bool, msg: str) -> None:
            self._set_loading(False)
            self._cloud_entries_json = json.dumps(self.backend.cloud_entries_payload(), ensure_ascii=False)
            self.cloudEntriesChanged.emit()
            self._set_status(msg)

        self._start_worker(worker, _done)

    @Slot(str, str)
    def cloudLoadSelected(self, name: str, url: str) -> None:
        self._set_status(f"Caricamento '{name}' dal cloud…")
        worker = CloudLoadSelectedWorker(self.backend, url, name)

        def _done(ok: bool, msg: str) -> None:
            self._set_loading(False)
            self._sync_all()
            self._set_status(msg)

        self._start_worker(worker, _done)

    # ── Slot: modalità percentuale e profili ──────────────────────────────────

    @Slot(bool)
    def setPercentMode(self, enabled: bool) -> None:
        self.backend.set_percent_mode(bool(enabled))
        self._percent_mode = bool(enabled)
        self.percentModeChanged.emit()
        self._set_status("Modalità % " + ("attiva" if enabled else "disattiva"))

    @Slot(str)
    def savePercentProfile(self, name: str) -> None:
        _, msg = self.backend.save_percent_profile(name)
        self._profiles_json = json.dumps(self.backend.percent_profiles(), ensure_ascii=False)
        self.profilesChanged.emit()
        self._set_status(msg)

    @Slot(str)
    def loadPercentProfile(self, name: str) -> None:
        _, msg = self.backend.load_percent_profile(name)
        self._percent_mode = bool(self.backend.use_percent_mode)
        self._percent_map_json = json.dumps(self.backend.get_percent_map(), ensure_ascii=False)
        self._selected_chapters_json = json.dumps(self.backend.selected_chapters, ensure_ascii=False)
        self.percentModeChanged.emit()
        self.profilesChanged.emit()
        self.selectedChaptersChanged.emit()
        self._set_status(msg)

    @Slot(str)
    def deletePercentProfile(self, name: str) -> None:
        _, msg = self.backend.delete_percent_profile(name)
        self._profiles_json = json.dumps(self.backend.percent_profiles(), ensure_ascii=False)
        self.profilesChanged.emit()
        self._set_status(msg)

    @Slot(str)
    def setPercentMap(self, percent_json: str) -> None:
        try:
            obj = json.loads(percent_json)
            if not isinstance(obj, dict):
                obj = {}
        except Exception:
            logger.warning("setPercentMap: JSON non valido", exc_info=True)
            obj = {}
        safe = {}
        for k, v in obj.items():
            try:
                safe[str(k)] = float(v)
            except Exception:
                continue
        self.backend.set_percent_map(safe)
        self._percent_map_json = json.dumps(self.backend.get_percent_map(), ensure_ascii=False)
        self.profilesChanged.emit()
        self._set_status("Percentuali capitoli aggiornate")

    # ── Slot: PDF ─────────────────────────────────────────────────────────────

    @Slot(str, result=bool)
    def canOpenPdfForChapter(self, chapter: str) -> bool:
        try:
            return bool(self.backend.can_open_pdf_for_chapter(chapter))
        except Exception:
            logger.warning("canOpenPdfForChapter fallito per '%s'", chapter, exc_info=True)
            return False

    @Slot(str)
    def openPdfForChapter(self, chapter: str) -> None:
        _, msg = self.backend.open_pdf_for_chapter(chapter)
        self._set_status(msg)

    # ── Slot: AI (async) ──────────────────────────────────────────────────────

    @Slot(str)
    def askAiSnapshot(self, item_json: str) -> None:
        try:
            item = json.loads(item_json)
            if not isinstance(item, dict):
                item = {}
        except Exception:
            logger.warning("askAiSnapshot: JSON non valido", exc_info=True)
            item = {}

        self._set_status("Elaborazione AI in corso…")
        worker = AiWorker(self.backend, item)

        def _on_result(txt: str) -> None:
            self._ai_result_text = txt
            self.aiResultChanged.emit()

        def _done(ok: bool, msg: str) -> None:
            self._set_loading(False)
            self._set_status(msg)

        worker.resultReady.connect(_on_result)
        self._start_worker(worker, _done)

    # ── Slot: statistiche e snapshot ──────────────────────────────────────────

    @Slot(str)
    def openStatSnapshot(self, stat_id: str) -> None:
        snap, msg = self.backend.get_stat_snapshot(stat_id)
        self._snapshot_json = json.dumps(snap, ensure_ascii=False)
        self.snapshotChanged.emit()
        self._set_status(msg)

    @Slot(str)
    def deleteStat(self, stat_id: str) -> None:
        _, msg = self.backend.delete_stat(stat_id)
        self._sync_stats()
        self._set_status(msg)

    # ── Slot: stampa e anteprima errori ───────────────────────────────────────

    @Slot()
    def printWrongPool(self) -> None:
        _, msg = self.backend.print_wrong_pool_grouped()
        self._set_status(msg)

    @Slot()
    def previewWrongPoolPayload(self) -> None:
        rows = self.backend.wrong_pool_payload()
        self._wrong_payload_json = json.dumps(rows, ensure_ascii=False, indent=2)
        self.wrongPayloadChanged.emit()
        self._set_status(f"Anteprima JSON errori: {len(rows)} record")

    @Slot(str)
    def printErrorsByStat(self, stat_id: str) -> None:
        sid = str(stat_id or "").strip()
        if not sid:
            self._set_status("Seleziona prima una statistica errori")
            return
        snap, msg = self.backend.get_stat_snapshot(sid)
        if not snap:
            self._set_status(msg or "Snapshot errori non disponibile")
            return
        _, pmsg = self.backend.print_snapshot_errors(snap)
        self._set_status(pmsg)

    @Slot(str)
    def printSnapshotErrors(self, snapshot_json: str) -> None:
        try:
            rows = json.loads(snapshot_json)
            if not isinstance(rows, list):
                rows = []
        except Exception:
            logger.warning("printSnapshotErrors: JSON non valido", exc_info=True)
            rows = []
        _, msg = self.backend.print_snapshot_errors(rows)
        self._set_status(msg)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    app = QApplication(sys.argv)
    app.setApplicationName("QuizNova")
    app.setOrganizationName("AlgoTeam")

    engine = QQmlApplicationEngine()
    bridge = QuizNovaBridge()
    app.aboutToQuit.connect(bridge.shutdown)
    engine.rootContext().setContextProperty("quizBridge", bridge)

    base_dir = Path(__file__).resolve().parent
    candidates = [
        base_dir / "Main.qml",
        base_dir / "quiznova_qml" / "Main.qml",
        base_dir.parent / "Resources" / "Main.qml",
    ]
    qml_file = next((c for c in candidates if c.exists()), candidates[0])
    logger.warning("QML path in uso: %s", qml_file)
    engine.load(str(qml_file))

    if not engine.rootObjects():
        return 1
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
