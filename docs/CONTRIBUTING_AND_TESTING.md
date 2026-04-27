# Contributing & Testing

Guida per chi vuole estendere il framework: aggiungere strategie, scheduler
job, page dashboard, Pine script. Convenzioni di test, patterns di mock, code
review checklist.

---

## Setup dev environment

```bash
git clone <repo-url> propicks-ai-framework
cd propicks-ai-framework
python3.10 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,dashboard,telegram]"
pre-commit install                      # opzionale ma consigliato
pytest                                  # smoke test, ~544 test in <5s
```

Tool di lint/type:

```bash
ruff check src/                         # lint
ruff format src/ tests/                 # format
mypy src/propicks                       # type check (best-effort, non strict)
```

---

## Architettura — regole non negoziabili

Vedi [ARCHITECTURE_OVERVIEW](ARCHITECTURE_OVERVIEW.md) per il diagramma.
Riassunto delle invarianti:

1. **`domain/` è puro**: no I/O, no rete. Riceve numeri / `pd.Series` / `pd.DataFrame` in input, ritorna numeri / dict.
2. **`market/` è l'unico modulo che parla con yfinance**. Cambiare provider tocca solo qui.
3. **`ai/` è l'unico modulo che parla con SDK Anthropic**. Stesso principio.
4. **`io/` è l'unica scrittura su DB**. CLI/dashboard/scheduler chiamano io/, mai SQL inline.
5. **`cli/` e `dashboard/` sono thin**: parsing input → chiamata domain/io/ai → formatting output. Nessuna logica di business.

Violazioni vanno respinte in code review.

---

## Aggiungere una nuova strategia

Pattern: la strategia X (es. "MeanRev_15m") deve avere:

1. **Scoring engine puro** in `domain/X_scoring.py`
2. **AI validator + prompts** in `ai/X_validator.py` + `ai/X_prompts.py`
3. **CLI thin wrapper** in `cli/X.py` + entry in `pyproject.toml`
4. **Pine script mirror** in `tradingview/X_signal_engine.pine`
5. **Doc strategy** in `docs/X_STRATEGY.md`
6. **Test puri** in `tests/unit/test_X_scoring.py`

Step-by-step:

### 1. Scoring engine (puro)

```python
# src/propicks/domain/X_scoring.py
"""Scoring engine per strategia X."""

from __future__ import annotations
import pandas as pd
from propicks.config import (
    X_WEIGHT_A, X_WEIGHT_B, X_WEIGHT_C,
    X_THRESHOLD_A, X_THRESHOLD_B,
)
from propicks.domain.indicators import compute_ema, compute_rsi


def score_factor_a(...) -> float:
    """Sub-score A (peso N%)."""
    # NO I/O qui. Solo numeri in/out.
    ...


def score_factor_b(...) -> float:
    """Sub-score B."""
    ...


def classify_X(score: float) -> str:
    if score >= X_THRESHOLD_A:
        return "A — ..."
    if score >= X_THRESHOLD_B:
        return "B — ..."
    return "D — SKIP"


def analyze_X_ticker(ticker: str, ...) -> dict | None:
    """Orchestratore — può chiamare market/ per i dati grezzi."""
    # Eccezione documentata: l'orchestrator è autorizzato a chiamare market/
    # ma le funzioni score_* sopra sono pure.
    ...
```

### 2. AI validator (se serve validation Claude)

```python
# src/propicks/ai/X_prompts.py
X_SYSTEM_PROMPT = """...statico per cache-friendly..."""
X_USER_PROMPT_TEMPLATE = "...dinamico per ticker..."

# src/propicks/ai/X_validator.py
def validate_X_thesis(analysis: dict, *, force=False, gate=True) -> dict | None:
    # Cache lookup con TTL
    # Budget check
    # call_X_validation()
    # Sanity layer (R/R floor, etc.)
    # Save to cache
    ...
```

Aggiungi anche schema pydantic in `ai/claude_client.py::XVerdict`.

### 3. CLI thin wrapper

```python
# src/propicks/cli/X.py
def main() -> int:
    parser = argparse.ArgumentParser(...)
    parser.add_argument("tickers", nargs="+")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()

    for t in args.tickers:
        r = analyze_X_ticker(t)
        if args.validate:
            r["ai_verdict"] = validate_X_thesis(r)
        print_X_analysis(r)  # tabulate-based
    return 0
```

Entry in `pyproject.toml`:
```toml
[project.scripts]
propicks-X = "propicks.cli.X:main"
```

### 4. Pine mirror

Crea `tradingview/X_signal_engine.pine`. Replica byte-byte i sub-score Python
con i defaults di `config.py`. Vedi [PINE_SCRIPTS_REFERENCE](PINE_SCRIPTS_REFERENCE.md)
per esempi e pattern (request.security, runtime.error timeframe guard).

### 5. Doc strategy

`docs/X_STRATEGY.md` con tesi, sub-score, soglie, gate logic, examples CLI,
esempi Pine.

### 6. Test puri

```python
# tests/unit/test_X_scoring.py
def test_score_factor_a_sweet_spot():
    assert score_factor_a(...) == 100.0

def test_classify_X():
    assert classify_X(80).startswith("A")
```

I test su `domain/` non devono toccare rete né disco. Per tester I/O usa la
fixture autouse `_isolate_db` in `conftest.py` (DB temp per ogni test).

### 7. Aggiorna doc index

- `WIKI.md` → aggiungi alla mappa CLI ↔ Dashboard ↔ Pine
- `CLAUDE.md` → aggiungi entry nella tabella strategie
- `CLI_REFERENCE.md` → sezione `propicks-X`
- `DASHBOARD_GUIDE.md` (se aggiungi page) → sezione page X

---

## Aggiungere uno scheduler job

```python
# src/propicks/scheduler/jobs.py
def my_new_job() -> JobResult:
    """Eseguito ogni X minuti / cron Y."""
    started = time.monotonic()
    try:
        # logica
        return JobResult(status="success", n_items=N)
    except Exception as e:
        return JobResult(status="error", error=str(e))
```

Registra in `scheduler/scheduler.py::_register_jobs`:

```python
scheduler.add_job(
    my_new_job,
    CronTrigger(hour=22, minute=30, timezone="Europe/Rome"),
    id="my_new_job",
)
```

E rendilo invocabile da CLI in `cli/scheduler.py::main`:

```python
JOBS = {..., "my_new_job": my_new_job}
```

Test: invoca via `propicks-scheduler job my_new_job`.

---

## Aggiungere una page dashboard

```python
# src/propicks/dashboard/pages/12_MyPage.py
import streamlit as st
from propicks.domain.X_scoring import analyze_X_ticker
from propicks.dashboard._shared import cached_analyze

st.title("My Page")

ticker = st.text_input("Ticker")
if st.button("Run"):
    r = cached_analyze(ticker)
    st.dataframe(r["scores"])
```

Usa sempre `cached_analyze` (o equivalente) da `_shared.py` per cache TTL
allineata. Mai chiamare `analyze_X_ticker` direttamente — ogni rerun re-scarica
yfinance.

Aggiorna `DASHBOARD_GUIDE.md`.

---

## Convenzioni di test

### Naming

```
tests/unit/test_<modulo>.py
```

Una classe di test per ogni concept, function name `test_<scenario>_<expected>`.

```python
def test_score_oversold_full_capitulation_returns_100():
    ...

def test_quality_gate_below_ema200w_returns_zero():
    ...
```

### Mocking

| Layer da testare | Cosa moccare |
|------------------|--------------|
| `domain/` | Niente — è puro |
| `io/` | Fixture `_isolate_db` (autouse in conftest.py) |
| `ai/` | `unittest.mock.patch` su `call_validation`, `call_etf_validation`, ecc. |
| `market/` | `monkeypatch.setattr` su `download_history` con DataFrame predefinito |
| `cli/` | Cattura stdout/stderr con `capsys`; valida output formattato |
| `scheduler/` | Mocka i singoli job; testa la registry separatamente |

### Esempio mock AI

```python
from unittest.mock import patch

@pytest.fixture
def mock_verdict():
    return ThesisVerdict(verdict="CONFIRM", conviction_score=8, ...)

def test_validate_thesis_caches(sample_analysis, mock_verdict):
    with patch("propicks.ai.thesis_validator.call_validation",
               return_value=mock_verdict):
        result = validate_thesis(sample_analysis)
    assert result["verdict"] == "CONFIRM"
    assert result["_cache_hit"] is False
```

### Esempio mock yfinance

```python
def test_analyze_ticker(monkeypatch):
    fake_df = pd.DataFrame({
        "Open": [...], "High": [...], "Low": [...],
        "Close": [...], "Volume": [...],
    })
    monkeypatch.setattr(
        "propicks.market.yfinance_client.download_history",
        lambda t: fake_df,
    )
    r = analyze_ticker("AAPL")
    assert r["score_composite"] > 0
```

### Coverage target

- `domain/`: ≥ 80% (è puro, dovrebbe essere facile)
- `ai/`: ≥ 60% (mocking pesante)
- `cli/`, `dashboard/`: ≥ 30% (smoke test sufficiente)

```bash
pytest --cov=src/propicks --cov-report=term-missing
```

---

## Code review checklist

Per ogni PR, verifica:

### Architettura
- [ ] `domain/` non importa da `io/`, `market/`, `cli/`, `reports/`, `ai/`
- [ ] Nuovi default sono in `config.py` (NO numeri magici nel codice)
- [ ] Thin wrappers (cli/dashboard) NON contengono logica di business

### Sicurezza
- [ ] Nessuna API key, token, password committata
- [ ] `.env` non modificato (o se sì, solo `.env.example`)
- [ ] Logging non stampa secrets (vedi [SECURITY_AND_SECRETS](SECURITY_AND_SECRETS.md))

### Testing
- [ ] Nuove function pubbliche hanno test
- [ ] `pytest` verde (544+ test)
- [ ] `ruff check` clean
- [ ] Nessun `print()` di debug lasciato

### Doc
- [ ] Function pubbliche hanno docstring (one-line minimo, multi-line se ha invarianti non ovvie)
- [ ] Se cambia comportamento user-facing, aggiornare la doc rilevante
- [ ] Se cambia il contratto Pine ↔ Python, aggiornare entrambi i lati

### Performance
- [ ] Nuovi fetch yfinance usano la cache (`market_cache_store`)
- [ ] Nessun loop O(N²) su universi >100 ticker
- [ ] Nuove query SQL hanno indice corrispondente se filter su >1k row

---

## Pre-commit hooks (consigliati)

`.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.4.0
    hooks:
      - id: ruff
      - id: ruff-format
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: detect-private-key
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
        args: [--maxkb=1024]
```

```bash
pre-commit install
git commit                              # ora gira automaticamente
```

---

## Versioning & release

Schema semver `MAJOR.MINOR.PATCH`:
- **MAJOR**: rotture di contratto user-facing (CLI args rimossi/rinominati, DB schema breaking)
- **MINOR**: feature aggiunte (nuove strategie, nuovi job)
- **PATCH**: bug fix, doc, refactor interno

Tag git per release:

```bash
git tag -a v1.2.0 -m "v1.2.0 — added contrarian discovery STOXX 600"
git push --tags
```

Aggiorna `pyproject.toml::version` di pari passo.

Schema migration: ogni breaking schema change → bump MAJOR + migration in
`db.py::_apply_migrations` + entry in `schema_version` table.

---

## Performance profiling

Per profilare un comando:

```bash
python -X importtime -m propicks.cli.scanner AAPL 2>&1 | grep "import time"
python -m cProfile -o profile.out -m propicks.cli.scanner AAPL MSFT NVDA
python -m pstats profile.out
```

Per profilare query SQL:

```bash
sqlite3 data/propicks.db
sqlite> .timer on
sqlite> EXPLAIN QUERY PLAN <query>;
sqlite> <query>;
```

---

## Where things live (cheatsheet)

| Voglio... | File |
|-----------|------|
| Cambiare un default tecnico | `config.py` |
| Aggiungere un sub-score | `domain/<strategia>_scoring.py` |
| Cambiare un prompt Claude | `ai/<strategia>_prompts.py` |
| Aggiungere un comando CLI | `cli/<X>.py` + `pyproject.toml` |
| Aggiungere una page dashboard | `dashboard/pages/<NN>_<Name>.py` |
| Aggiungere un alert type | `scheduler/alerts.py` + `notifications/formatter.py` |
| Aggiungere un job EOD | `scheduler/jobs.py` + `scheduler/scheduler.py` |
| Cambiare schema DB | `io/schema.sql` + `io/db.py::_apply_migrations` |
| Aggiungere un test mock yfinance | `tests/unit/conftest.py` (fixture comune) |
| Cambiare il regime classifier | `domain/regime.py` (e Pine `weekly_regime_engine.pine`) |

---

## Issue/PR templates

Per ogni issue includi:
- Comando esatto che riproduce il bug (con cwd e env vars rilevanti)
- Output completo (stderr + stdout)
- `git log -1 --pretty=format:"%h %s"` per pinpoint la versione
- Se relativo a Pine: screenshot pannello + output JSON Python equivalente

Per ogni PR:
- Riferimento all'issue (se esiste)
- Test aggiunti/modificati
- Doc update se rilevante
- Note di breaking change in checklist
