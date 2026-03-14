# -*- mode: python ; coding: utf-8 -*-
#
# PegasoQuiz — PyInstaller spec (app QML + PySide6 + PDF→Quiz)
#
# Build:
#   pyinstaller --noconfirm PegasoQuiz.spec 2>&1 | tee build.log
#
# Prerequisiti:
#   pip install -r requirements.txt
#
# Variabili d'ambiente richieste a runtime (NON a build-time):
#   QUIZNOVA_SUPABASE_URL        URL progetto Supabase
#   QUIZNOVA_SUPABASE_ANON_KEY   chiave anon Supabase
#   QUIZNOVA_ENC_KEY             chiave cifratura asset (hex 64 car.)
#   QUIZNOVA_MAC_KEY             chiave HMAC manifest (hex 64 car.)
#   In alternativa impostarle in  ~/.quiznova/.env
#
# Struttura attesa nella directory sorgente:
#   main_quiznova.py
#   quiznova_backend.py
#   copyright_crypto.py
#   pdf_quiz_generator.py       <- NUOVO
#   Main.qml
#   images/
#       logo.jpg  logo.png  algo.jpg  algo.png
#   copyright_secure/           <- opzionale (Easter Egg)
#       manifest.json  *.enc
#
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

PROJECT_DIR = Path(SPECPATH).resolve()

block_cipher = None


# -- datas --------------------------------------------------------------------

datas = [
    (str(PROJECT_DIR / "Main.qml"), "."),
    (str(PROJECT_DIR.parent / "images"), "images"),
]

_json_dir = PROJECT_DIR.parent / "JSON"
if _json_dir.is_dir():
    datas.append((str(_json_dir), "JSON"))

_secure_dir = PROJECT_DIR / "copyright_secure"
if _secure_dir.is_dir():
    datas.append((str(_secure_dir), "copyright_secure"))


# -- Analysis -----------------------------------------------------------------

a = Analysis(
    [str(PROJECT_DIR / "main_quiznova.py")],
    pathex=[str(PROJECT_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # include pure-python charset_normalizer modules
        *collect_submodules("charset_normalizer"),
        # core app
        "quiznova_backend",
        "copyright_crypto",
        "pdf_quiz_generator",

        # PySide6 / Qt
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickControls2",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtPrintSupport",

        # rete / SSL
        "certifi",


        # pypdf (estrazione immagini dal PDF)
        "pypdf",
        "pypdf._page",
        "pypdf.filters",
        "pypdf.generic",
        "pypdf._reader",

        # Pillow (resize e conversione immagini)
        # NOTA: PIL non deve stare in excludes — serve a pdf_quiz_generator
        "PIL",
        "PIL.Image",
        "PIL.ImageFile",
        "PIL.JpegImagePlugin",
        "PIL.PngImagePlugin",
        "PIL.BmpImagePlugin",
        "PIL.TiffImagePlugin",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "pytest",
        # PIL rimosso dagli excludes: ora serve per pdf_quiz_generator
    ],
    noarchive=False,
    optimize=0,
)

# Force pure-python charset_normalizer in frozen app to avoid __mypyc missing module crashes.
a.binaries = [
    b for b in a.binaries
    if "charset_normalizer/md." not in str(b)
    and "charset_normalizer/cd." not in str(b)
    and "__mypyc" not in str(b)
]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)


# -- EXE ----------------------------------------------------------------------

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PegasoQuiz",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,       # "universal2" per fat binary Intel+Apple Silicon
    codesign_identity=None,
    entitlements_file=None,
    icon=str(_secure_dir / "icon.icns") if (_secure_dir / "icon.icns").exists() else None,
)


# -- COLLECT ------------------------------------------------------------------

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[
        "libPySide6*",      # UPX puo corrompere alcune .so PySide6
        "Qt*",
    ],
    name="PegasoQuiz",
)


# -- BUNDLE (macOS .app) ------------------------------------------------------

app = BUNDLE(
    coll,
    name="PegasoQuiz.app",
    icon=str(_secure_dir / "icon.icns") if (_secure_dir / "icon.icns").exists() else None,
    bundle_identifier="com.algoteam.pegasoquiz",
    info_plist={
        "CFBundleShortVersionString":     "3.1",
        "CFBundleVersion":                "3.1.0",
        "NSHighResolutionCapable":        True,
        "LSMinimumSystemVersion":         "12.0",
        "NSRequiresAquaSystemAppearance": False,
    },
)
