# ASR Solver API

API di ottimizzazione planning per **ASR** (Ambulances de la Sarine, Sécurité Riviera – C6).  
Genera automaticamente la migliore assegnazione Jour / Nuit / VM rispettando vincoli contrattuali, equità e preferenze.

> **Ultimo aggiornamento docs:** 2026-03-15

## Stack

| Layer | Tecnologia |
|-------|-----------|
| Framework | FastAPI (Python 3.11) |
| Solver | Google OR-Tools (CP-SAT) |
| Database | Supabase (PostgreSQL) |
| Deploy | Google Cloud Run (`europe-west1`) |

## Endpoint

| Metodo | Path | Auth | Descrizione |
|--------|------|:----:|-------------|
| `GET` | `/` | ✗ | Health check |
| `GET` | `/status` | ✗ | Stato del servizio |
| `GET` | `/config` | ✓ | Configurazione solver |
| `POST` | `/solve` | ✓ | Esegue il solver OR-Tools per un mese |
| `POST` | `/solve-gemini` | ✓ | Esegue il solver Gemini AI per un mese |
| `POST` | `/explain` | ✓ | Spiega le scelte del solver |
| `POST` | `/clear` | ✓ | Cancella il planning di un mese |
| `POST` | `/upload-pdf` | ✓ | Upload e parsing PDF planning |
| `POST` | `/upload-pdf-validate` | ✓ | Valida un PDF senza salvare |
| `POST` | `/upload-pdf-confirm` | ✓ | Conferma e salva un upload validato |

## Autenticazione

Gli endpoint protetti (✓) richiedono l'header `X-API-Key`:

```
X-API-Key: <valore-di-SOLVER_API_KEY>
```

Se la variabile d'ambiente `SOLVER_API_KEY` non è impostata, tutti gli endpoint sono accessibili liberamente (modalità sviluppo).

## Moduli

| File | Descrizione |
|------|-------------|
| `main.py` | FastAPI app — 10 endpoint (solver OR-Tools, solver Gemini, explain, clear, upload-pdf) |
| `extractor.py` | Estrazione planning da PDF: 3 priorità (text overlay → sun icon → pixel) |
| `gemini_solver.py` | Solver alternativo basato su Google Gemini 2.0 Flash (prompt strutturato JSON) |
| `batch_upload.py` | Upload batch di tutti i PDF di un semestre |
| `employees.py` | Mapping colonne PDF → dipendenti Supabase |
| `SHIFT_CODES_REFERENCE.md` | Documentazione completa dei codici shift, colori pixel, normalizzazione |
| `requirements.txt` | Dipendenze Python |
| `Dockerfile` | Deploy Cloud Run |

## Funzionalità Chiave

### Estrazione PDF (extractor.py)
- **Priorità 1 — Text overlay**: lettura diretta dei codici stampati sulle cellule (`6P1`, `AMJ1`, etc.)
- **Priorità 2 — Icona soleil**: rilevazione raster per C/C1/VA tramite ratio pixel caldi + colore fond
- **Priorità 3 — Pixel centrale**: classificazione nearest-neighbor vs 70+ colori noti
- **Normalizzazione shift**: `AMJ1/AMJ2→AMJP`, `AMN1/AMN2→AMNP`, `AMHR→AMHS`, `RS5-10→RS`, `C1→QC1`, `ML→M`

### Solver OR-Tools (main.py)
- **50h LTr** — limite settimanale proporzionale al FTE, con sottrazione minuti importati
- **Limite mensile** — 10400 min/mese × FTE × (1 + overshoot%), con budget residuo
- **Copertura giornaliera** — requisiti adattivi (ridotti se import blocca dipendenti)
- **Equità** — distribuzione equilibrata turni Jour/Nuit/Weekend
- **Regola domenicale** — 2 su 4 dimanches liberi (dérogatoire SECO)

### Solver Gemini AI (gemini_solver.py)
- **Generazione AI** — prompt strutturato JSON con le stesse regole LTr/SECO
- **Modello** — Google Gemini 2.0 Flash via google-generativeai SDK
- **Validazione** — parsing e verifica output prima di salvare in Supabase
- **Endpoint** — `POST /solve-gemini` con stessa interfaccia del solver OR-Tools

## Setup Locale

### Prerequisiti

- Python 3.11+
- pip

### Installazione

```bash
cd solver-api
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Variabili d'ambiente

```env
SUPABASE_URL=https://dcijgpmpysyfcjeerxqn.supabase.co
SUPABASE_KEY=<service-role-key>
SOLVER_API_KEY=<api-key-opzionale>
```

### Avvio

```bash
uvicorn main:app --reload --port 8000
```

Docs interattive su [http://localhost:8000/docs](http://localhost:8000/docs).

## Test

```bash
python -m pytest test_solver.py -v
```

## Deploy

Il servizio gira su **Google Cloud Run** (progetto `skilful-lock-486100-d5`).

### Deploy da source

```bash
gcloud run deploy asr-solver-api \
  --source . \
  --region europe-west1 \
  --platform managed \
  --allow-unauthenticated
```

### Aggiornare env var

```bash
gcloud run services update asr-solver-api \
  --region europe-west1 \
  --update-env-vars "SOLVER_API_KEY=<nuovo-valore>"
```

| Ambiente | URL |
|----------|-----|
| Produzione | https://asr-solver-api-218342602037.europe-west1.run.app |
| Docs | https://asr-solver-api-218342602037.europe-west1.run.app/docs |
