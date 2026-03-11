# ASR Solver API

API di ottimizzazione planning per **ASR** (Ambulances de la Sarine).  
Genera automaticamente la migliore assegnazione Jour / Nuit / VM rispettando vincoli contrattuali, equità e preferenze.

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
| `POST` | `/solve` | ✓ | Esegue il solver per un mese |
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
