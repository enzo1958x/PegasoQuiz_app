#!/usr/bin/env bash
# =============================================================================
# PegasoQuiz — script di build completo
# =============================================================================
#
# Uso:
#   chmod +x build.sh
#   ./build.sh
#
# L'app viene prodotta in:
#   dist/PegasoQuiz.app   (macOS .app bundle)
#   dist/PegasoQuiz/      (directory onedir, usabile anche su Linux/Windows)
#
# Per un fat binary Intel + Apple Silicon:
#   ARCH=universal2 ./build.sh
#
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ARCH="${ARCH:-}"        # vuoto = architettura nativa; "universal2" per fat binary
LOG="build.log"

# -----------------------------------------------------------------------------
# Colori
# -----------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERR]${NC}  $*"; exit 1; }

echo "=============================================="
echo "  PegasoQuiz — build $(date '+%Y-%m-%d %H:%M')"
echo "=============================================="
echo

# -----------------------------------------------------------------------------
# 1. Variabili d'ambiente obbligatorie
# -----------------------------------------------------------------------------
echo "── 1. Verifica variabili d'ambiente ──────────────"

ENV_FILE="$HOME/.quiznova/.env"
if [[ -f "$ENV_FILE" ]]; then
    # Carica solo le variabili che non sono già impostate
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
        [[ -z "${!key:-}" ]] && export "$key=$value"
    done < "$ENV_FILE"
    ok "Variabili caricate da $ENV_FILE"
fi

MISSING=()
for VAR in QUIZNOVA_SUPABASE_URL QUIZNOVA_SUPABASE_ANON_KEY \
           QUIZNOVA_ENC_KEY QUIZNOVA_MAC_KEY; do
    if [[ -z "${!VAR:-}" ]]; then
        MISSING+=("$VAR")
    fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
    warn "Variabili mancanti (l'app le cercherà a runtime in ~/.quiznova/.env):"
    for v in "${MISSING[@]}"; do warn "  $v"; done
else
    ok "Tutte le variabili d'ambiente presenti"
fi
echo

# -----------------------------------------------------------------------------
# 2. Dipendenze Python
# -----------------------------------------------------------------------------
echo "── 2. Installazione dipendenze ───────────────────"

if ! command -v python3 &>/dev/null; then
    err "python3 non trovato. Installa Python 3.11+ e riprova."
fi
PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
ok "Python $PYTHON_VER"

pip install -q --upgrade pip
pip install -q -r requirements.txt
ok "Dipendenze installate"
echo

# -----------------------------------------------------------------------------
# 3. Verifica file sorgente
# -----------------------------------------------------------------------------
echo "── 3. Verifica file sorgente ─────────────────────"

REQUIRED_FILES=(
    "main_quiznova.py"
    "quiznova_backend.py"
    "copyright_crypto.py"
    "pdf_quiz_generator.py"
    "Main.qml"
    "PegasoQuiz.spec"
)
for f in "${REQUIRED_FILES[@]}"; do
    if [[ -f "$f" ]]; then
        ok "$f"
    else
        err "File mancante: $f"
    fi
done

# copyright_secure è opzionale
if [[ -d "copyright_secure" ]]; then
    ok "copyright_secure/ (Easter Egg incluso)"
else
    warn "copyright_secure/ non trovata — Easter Egg non incluso nel bundle"
fi
echo

# -----------------------------------------------------------------------------
# 4. Pulizia build precedente
# -----------------------------------------------------------------------------
echo "── 4. Pulizia ────────────────────────────────────"
rm -rf build/ dist/
ok "Cartelle build/ e dist/ rimosse"
echo

# -----------------------------------------------------------------------------
# 5. PyInstaller
# -----------------------------------------------------------------------------
echo "── 5. Build PyInstaller ──────────────────────────"

PYINSTALLER_ARGS=(
    "--noconfirm"
    "PegasoQuiz.spec"
)

# Sovrascrive target_arch se ARCH è impostato
if [[ -n "$ARCH" ]]; then
    PYINSTALLER_ARGS+=("--target-arch" "$ARCH")
    warn "Fat binary: target_arch=$ARCH"
fi

echo "  pyinstaller ${PYINSTALLER_ARGS[*]}"
echo "  Log: $LOG"
echo

pyinstaller "${PYINSTALLER_ARGS[@]}" 2>&1 | tee "$LOG"

# Controlla esito
if [[ ${PIPESTATUS[0]} -ne 0 ]]; then
    err "Build fallita. Controlla $LOG per i dettagli."
fi
echo

# -----------------------------------------------------------------------------
# 6. Verifica output
# -----------------------------------------------------------------------------
echo "── 6. Verifica output ────────────────────────────"

APP_BUNDLE="dist/PegasoQuiz.app"
APP_EXEC="dist/PegasoQuiz/PegasoQuiz"

if [[ -d "$APP_BUNDLE" ]]; then
    SIZE=$(du -sh "$APP_BUNDLE" | cut -f1)
    ok ".app bundle: $APP_BUNDLE ($SIZE)"
elif [[ -f "$APP_EXEC" ]]; then
    SIZE=$(du -sh "dist/PegasoQuiz" | cut -f1)
    ok "Onedir: dist/PegasoQuiz/ ($SIZE)"
else
    err "Output non trovato in dist/"
fi

# Verifica che pdf_quiz_generator sia nel bundle
if grep -r "pdf_quiz_generator" dist/ &>/dev/null 2>&1; then
    ok "pdf_quiz_generator trovato nel bundle"
else
    warn "pdf_quiz_generator non trovato nel bundle — verifica hiddenimports"
fi

# Verifica Pillow
if find dist/ -name "PIL" -type d &>/dev/null 2>&1; then
    ok "Pillow (PIL) incluso"
else
    warn "PIL non trovato nel bundle — pdf_quiz_generator potrebbe fallire"
fi

echo
echo "=============================================="
echo -e "  ${GREEN}Build completata con successo!${NC}"
[[ -d "$APP_BUNDLE" ]] && echo "  → $APP_BUNDLE"
echo "=============================================="
