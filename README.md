# PegasoQuiz_app1. Panoramica del Progetto
PegasoQuiz (nome di build: QuizNova) è un'applicazione desktop per la preparazione a test universitari tramite quiz a risposta multipla. Supporta caricamento da file JSON, URL remoti, PDF di dispense, e dataset cloud ospitati su GitHub/Supabase.
L'applicazione è distribuita come bundle nativo macOS (.app) e HTML (ospitata in cloud su Vercel.com) e directory eseguibile multipiattaforma, prodotti tramite PyInstaller.

1.1 Stack Tecnologico
Linguaggio frontend QML
Linguaggio backend	Python 3.13 (CPython)
Framework UI	PySide6 6.6+ (Qt 6) con QML / QtQuick
Bundler	PyInstaller 6.18+
Piattaforma primaria	macOS arm64 (Apple Silicon); compatibile Linux/Windows
Database cloud	Supabase (PostgreSQL + Auth REST)
AI/LLM	OpenAI GPT-4o-mini (vision) — feature opzionale
Repository cloud Github.com
Estrazione PDF	pypdf 4+, pdfminer.six, Pillow 10+

1.2 Struttura dei File Sorgenti
File	Ruolo
main_quiznova.py	Entry point, bridge QML↔Python, worker thread pool
quiznova_backend.py	Backend: logica quiz, storage, cloud Supabase, AI
pdf_quiz_generator.py	Generazione quiz da PDF via GPT-4o-mini vision
copyright_crypto.py	Cifratura XOR-stream asset Easter Egg, HMAC-SHA-256 manifest
Main.qml	UI dichiarativa: componenti, dialoghi, timer quiz, animazioni
PegasoQuiz.spec	Configurazione PyInstaller (datas, hiddenimports, bundle)
build.sh	Script di build bash: dipendenze, lint, PyInstaller, verifica output
requirements.txt	Dipendenze pip: PySide6, PyInstaller, certifi, pdfminer, pypdf, Pillow
pytest.ini	Configurazione test pytest (cartella tests/, pattern file)
 
