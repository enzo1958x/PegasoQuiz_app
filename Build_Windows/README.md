# PegasoQuiz — Build Windows

## Come iniziare

**Doppio clic su `avvia.bat`** e segui i 3 passi nell'ordine.

---

## Struttura

```
PegasoQuiz_Windows_Build\
├── avvia.bat                  ← PARTI DA QUI
├── 1_prerequisiti.ps1         ← verifica Python e dipendenze, chiede se installarli
├── 2_variabili.ps1            ← configura le variabili d'ambiente
├── 3_build.ps1                ← esegue la build
├── README.md
└── sorgenti\
    ├── main_quiznova.py       (con fix applicate)
    ├── quiznova_backend.py    (con fix applicate)
    ├── pdf_quiz_generator.py
    ├── copyright_crypto.py
    ├── Main.qml
    ├── PegasoQuiz.spec        (spec Windows)
    └── requirements.txt
```

**Cartelle da aggiungere manualmente se presenti:**
```
sorgenti\copyright_secure\    ← asset Easter Egg (manifest.json, *.enc, icon.ico)
images\                       ← immagini (logo.jpg, logo.png, algo.jpg, algo.png)
```

---

## Passo 1 — Prerequisiti (`1_prerequisiti.ps1`)

Controlla se sono già installati:
- **Python >= 3.11** — se manca, chiede conferma e lo installa via `winget` o scaricando direttamente da python.org
- **PySide6, PyInstaller, certifi, pdfminer.six, pypdf, Pillow** — se mancano, chiede conferma e li installa con `pip`

Nulla viene scaricato senza conferma esplicita.

---

## Passo 2 — Variabili (`2_variabili.ps1`)

Chiede 4 valori:

| Variabile | Dove trovarla |
|-----------|--------------|
| `QUIZNOVA_SUPABASE_URL` | [app.supabase.com](https://app.supabase.com) → progetto → **Settings → API → Project URL** |
| `QUIZNOVA_SUPABASE_ANON_KEY` | Stessa pagina → **anon public** |
| `QUIZNOVA_ENC_KEY` | Generata automaticamente, oppure copia quella del Mac |
| `QUIZNOVA_MAC_KEY` | Generata automaticamente, oppure copia quella del Mac |

> ⚠️ Se hai già asset Easter Egg cifrati sul Mac, usa le **stesse** ENC_KEY e MAC_KEY.

Salvate in `%USERPROFILE%\.quiznova\.env` e nel registro Windows.

---

## Passo 3 — Build (`3_build.ps1`)

Output: `sorgenti\dist\PegasoQuiz\PegasoQuiz.exe`

La cartella `dist\PegasoQuiz\` è autonoma e distribuibile.  
Per aggiornamenti futuri: sostituisci i sorgenti e rilancia solo il passo 3.

---

## Troubleshooting

| Problema | Soluzione |
|----------|-----------|
| Script bloccato da policy | Apri PowerShell come Admin: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `python` non trovato dopo installazione | Riapri PowerShell e rilancia lo script |
| Build fallisce | Apri opzione 5 del menu per leggere il log |
| Exe crasha al lancio | Da terminale: `cd sorgenti\dist\PegasoQuiz && PegasoQuiz.exe` |
| Antivirus blocca l'exe | Aggiungi `dist\PegasoQuiz\` alle esclusioni antivirus |
