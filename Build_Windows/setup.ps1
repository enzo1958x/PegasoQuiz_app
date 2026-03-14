# =============================================================================
# PegasoQuiz - Setup: prerequisiti e variabili d'ambiente
# =============================================================================

function ok   { param($m) Write-Host "  [OK]   $m" -ForegroundColor Green  }
function warn { param($m) Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function err  { param($m) Write-Host "  [ERR]  $m" -ForegroundColor Red; Read-Host "Premi INVIO per uscire"; exit 1 }
function hdr  { param($m) Write-Host "`n-- $m" -ForegroundColor Cyan }
function ask  {
    param($m)
    Write-Host "`n  $m [S/N] " -ForegroundColor Yellow -NoNewline
    $r = Read-Host
    return ($r -match "^[Ss]")
}

Write-Host ""
Write-Host "  =============================================" -ForegroundColor Cyan
Write-Host "  PegasoQuiz - Setup" -ForegroundColor Cyan
Write-Host "  =============================================" -ForegroundColor Cyan


# -----------------------------------------------------------------------------
# 1. PYTHON
# -----------------------------------------------------------------------------
hdr "1. Python"

$pythonOk = $false
try {
    $pyOut = & python --version 2>&1
    if ($pyOut -match "Python (\d+)\.(\d+)") {
        $maj = [int]$Matches[1]; $min = [int]$Matches[2]
        if ($maj -gt 3 -or ($maj -eq 3 -and $min -ge 11)) {
            ok "Python $maj.$min trovato"
            $pythonOk = $true
        } else {
            warn "Python $maj.$min trovato, ma serve >= 3.11"
        }
    }
} catch {
    warn "Python non trovato nel PATH"
}

if (-not $pythonOk) {
    if (ask "Python non e' installato. Vuoi scaricarlo e installarlo adesso?") {
        $wingetOk = $false
        try {
            $wg = & winget --version 2>&1
            if ($LASTEXITCODE -eq 0) { $wingetOk = $true }
        } catch {}

        if ($wingetOk) {
            Write-Host "  Installazione Python 3.13 via winget..." -ForegroundColor DarkGray
            & winget install --id Python.Python.3.13 --source winget --silent `
                --accept-source-agreements --accept-package-agreements
        } else {
            $pyUrl = "https://www.python.org/ftp/python/3.13.0/python-3.13.0-amd64.exe"
            $pyTmp = Join-Path $env:TEMP "python-3.13.0-amd64.exe"
            Write-Host "  Download Python 3.13 da python.org (~25 MB)..." -ForegroundColor DarkGray
            Invoke-WebRequest -Uri $pyUrl -OutFile $pyTmp -UseBasicParsing
            $p = Start-Process $pyTmp -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0" -Wait -PassThru
            if ($p.ExitCode -ne 0) { err "Installazione Python fallita (exit $($p.ExitCode))" }
            Remove-Item $pyTmp -Force
        }

        $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("PATH","User")

        $pythonCandidates = @(
            "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
            "C:\Python313\python.exe",
            "C:\Python312\python.exe",
            "C:\Python311\python.exe"
        )
        $foundPython = $null
        foreach ($candidate in $pythonCandidates) {
            if (Test-Path $candidate) { $foundPython = $candidate; break }
        }
        if ($foundPython) {
            $dir = Split-Path $foundPython
            if ($env:PATH -notlike "*$dir*") { $env:PATH = "$dir;$dir\Scripts;$env:PATH" }
        }

        $pyOut2 = & python --version 2>&1
        if ($pyOut2 -match "Python") { ok "Python installato: $pyOut2" }
        else { err "Python non trovato nel PATH. Riapri PowerShell e rilancia questo script." }
    } else {
        err "Python e' obbligatorio. Installalo da https://python.org e riprova."
    }
}


# -----------------------------------------------------------------------------
# 2. DIPENDENZE PYTHON
# -----------------------------------------------------------------------------
hdr "2. Dipendenze Python"

$packages = @(
    @{ import = "PySide6";     name = "PySide6>=6.6";     label = "PySide6 (UI Qt)"      },
    @{ import = "PyInstaller"; name = "PyInstaller>=6.18"; label = "PyInstaller (build)"  },
    @{ import = "certifi";     name = "certifi";           label = "certifi (SSL)"        },
    @{ import = "pdfminer";    name = "pdfminer.six";      label = "pdfminer.six (PDF)"   },
    @{ import = "pypdf";       name = "pypdf>=4.0";        label = "pypdf (PDF immagini)" },
    @{ import = "PIL";         name = "Pillow>=10.0";      label = "Pillow (immagini)"    }
)

$missing = @()
foreach ($pkg in $packages) {
    $res = & python -c "import $($pkg.import)" 2>&1
    $ec  = $LASTEXITCODE
    if ($ec -eq 0) {
        ok "$($pkg.label) - gia' installato"
    } else {
        warn "$($pkg.label) - NON trovato"
        $missing += $pkg
    }
}

if ($missing.Count -gt 0) {
    Write-Host ""
    Write-Host "  Pacchetti mancanti:" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host "    - $($_.label)" -ForegroundColor Yellow }

    if (ask "Vuoi installare i pacchetti mancanti adesso?") {
        Write-Host "  Installazione in corso..." -ForegroundColor DarkGray
        & python -m pip install --upgrade pip --quiet
        $toInstall = $missing | ForEach-Object { $_.name }
        & python -m pip install @toInstall
        if ($LASTEXITCODE -ne 0) { err "Installazione dipendenze fallita. Controlla la connessione." }

        $failed = @()
        foreach ($pkg in $missing) {
            $res = & python -c "import $($pkg.import)" 2>&1
            $ec  = $LASTEXITCODE
            if ($ec -eq 0) { ok "$($pkg.label) - installato" }
            else            { $failed += $pkg.label }
        }
        if ($failed.Count -gt 0) {
            err "Pacchetti non installati: $($failed -join ', ')"
        }
    } else {
        err "I pacchetti sono obbligatori. Installali e riprova."
    }
} else {
    ok "Tutti i pacchetti sono gia' installati"
}


# -----------------------------------------------------------------------------
# 3. VARIABILI D'AMBIENTE
# -----------------------------------------------------------------------------
hdr "3. Variabili d'ambiente"

$vars = @{
    "QUIZNOVA_SUPABASE_URL"      = "https://dkkuchcmifcogxiuefzq.supabase.co"
    "QUIZNOVA_SUPABASE_ANON_KEY" = "sb_publishable_QXqKvaZS_PNCcNI6gBpb4A_2p3TEsHE"
    "QUIZNOVA_ENC_KEY"           = "3e4c7a1fa129a4f6dc88950a2375d98a481cbe6e412dbca86cfa6d0f3d1ad45c"
    "QUIZNOVA_MAC_KEY"           = "a6c35f77e3d9041f0bbda40d4ae58d527f95cbf9b76103bde2cc0d1f8418d2a3"
}

foreach ($kv in $vars.GetEnumerator()) {
    [System.Environment]::SetEnvironmentVariable($kv.Key, $kv.Value, "User")
    $env:($kv.Key) = $kv.Value
    ok "$($kv.Key) impostata"
}

# Salva anche nel file .env
$EnvDir  = Join-Path $env:USERPROFILE ".quiznova"
$EnvFile = Join-Path $EnvDir ".env"
if (-not (Test-Path $EnvDir)) { New-Item -ItemType Directory -Path $EnvDir | Out-Null }

@"
# PegasoQuiz - variabili d'ambiente
# Generato il $(Get-Date -Format 'yyyy-MM-dd HH:mm')

QUIZNOVA_SUPABASE_URL=https://dkkuchcmifcogxiuefzq.supabase.co
QUIZNOVA_SUPABASE_ANON_KEY=sb_publishable_QXqKvaZS_PNCcNI6gBpb4A_2p3TEsHE
QUIZNOVA_ENC_KEY=3e4c7a1fa129a4f6dc88950a2375d98a481cbe6e412dbca86cfa6d0f3d1ad45c
QUIZNOVA_MAC_KEY=a6c35f77e3d9041f0bbda40d4ae58d527f95cbf9b76103bde2cc0d1f8418d2a3
"@ | Set-Content -Path $EnvFile -Encoding UTF8
ok "Salvate in $EnvFile"


# -----------------------------------------------------------------------------
# Fine
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "  =============================================" -ForegroundColor Green
Write-Host "  Setup completato!" -ForegroundColor Green
Write-Host "  Ora esegui la build con: python -m PyInstaller --noconfirm PegasoQuiz.spec" -ForegroundColor Green
Write-Host "  =============================================" -ForegroundColor Green
Write-Host ""
Read-Host "  Premi INVIO per uscire"
