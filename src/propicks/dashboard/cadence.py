"""Cadenza operativa settimanale — dato strutturato alla base del pannello
"Cadenza della settimana" sulla home della dashboard.

Fonte: ``docs/Weekly_Operating_Framework.md``. Il markdown resta la reference
narrativa (il perché + le tabelle); questo modulo è la source-of-truth
operativa consumata dalla UI (il cosa faccio oggi).

Se aggiorni l'uno, aggiorna anche l'altro — sono intenzionalmente duplicati
per evitare parsing fragile del markdown.
"""

from __future__ import annotations

DayBlock = tuple[str, str, list[str]]
"""(nome blocco, durata, bullet list azioni)."""

DayCadence = dict[str, str | list[DayBlock]]
"""``name``: label giorno · ``blocks``: lista di DayBlock."""


WEEKLY_CADENCE: dict[int, DayCadence] = {
    0: {  # Monday
        "name": "Setup Week",
        "duration": "~75min",
        "blocks": [
            (
                "Macro + ETF",
                "~30min",
                [
                    "Rotate ETF WORLD + allocate",
                    "Rotate US come cross-check leadership",
                    "Portfolio status + risk (esposizione aggregata)",
                    "Se regime change o size rilevante → validate macro (Claude)",
                ],
            ),
            (
                "Stock watchlist health-check",
                "~30min",
                [
                    "Re-scan --brief di tutta la watchlist",
                    "Score sceso <60 → rimuovi + disabilita Pine alert",
                    "Earnings entro 5gg → flag 'no new entry' su quel ticker",
                    "Regime weekly BEAR → freeze nuove entry stock",
                ],
            ),
            (
                "Update stop su posizioni aperte",
                "~15min",
                [
                    "P&L ≥ +5% → stop a break-even + 1%",
                    "P&L ≥ +10% → stop a +3% (locked profit)",
                    "P&L ≥ +20% → stop a +10% o chiusura parziale 50%",
                    "ETF: stop fisso -5% (meno whipsaw)",
                ],
            ),
        ],
    },
    1: {  # Tuesday — Execution
        "name": "Execution (alert-driven)",
        "duration": "10-30min × alert",
        "blocks": [
            (
                "Stock entry workflow",
                "solo su alert Pine",
                [
                    "Alert push → 15min per decidere (altrimenti salta il trade)",
                    "Scan TICKER --validate → serve CONFIRM + conviction ≥ 7",
                    "Perplexity 2C (red flag 24h) — sempre, anche se Claude CONFIRM",
                    "Size → Portfolio add → Journal add (catalyst breve)",
                ],
            ),
            (
                "ETF tranche",
                "se rebalance deciso lun",
                [
                    "Esegui tranche con ordini LIMITE (non market)",
                    "Rotazioni settoriali non sono mai urgenti — spalma su 2-3 giorni",
                ],
            ),
        ],
    },
    2: {  # Wednesday
        "name": "Execution (alert-driven)",
        "duration": "10-30min × alert",
        "blocks": [
            (
                "Stesso flow di martedì",
                "—",
                [
                    "Non aprire il terminale senza alert Pine",
                    "FOMO su singoli trigger = causa #1 di violazione regole rischio",
                ],
            ),
        ],
    },
    3: {  # Thursday
        "name": "Execution (alert-driven)",
        "duration": "10-30min × alert",
        "blocks": [
            (
                "Stesso flow + preview venerdì",
                "—",
                [
                    "Continua con alert-driven",
                    "Pre-check earnings calendar della settimana successiva",
                ],
            ),
        ],
    },
    4: {  # Friday — Close & Check
        "name": "Close & Check",
        "duration": "~20min EOD",
        "blocks": [
            (
                "Verifiche settimana",
                "—",
                [
                    "Portfolio status + risk",
                    "Journal list --open",
                    "Loss settimanale > 5% → stop trading lunedì prossimo",
                    "Qualche stop toccato? → verifica chiusura + update journal",
                    "Ultimo venerdì del mese → report monthly",
                ],
            ),
            (
                "Stock-specifici",
                "—",
                [
                    "Earnings settimana successiva? Chiudi ½ posizione se P&L ≥ +10%",
                    "Hold full solo se posizione piccola o conviction alta",
                    "Niente nuove entry su ticker con earnings imminenti",
                ],
            ),
        ],
    },
    5: {  # Saturday — Review
        "name": "Review & Reflect",
        "duration": "~45min",
        "blocks": [
            (
                "Report + stats",
                "—",
                [
                    "Report weekly + stats per strategy (TechTitans / DominaDow / …)",
                    "Journal list --closed (chiusure della settimana)",
                ],
            ),
            (
                "Quattro domande fisse (scrivile)",
                "—",
                [
                    "Quale strategy ha performato meglio questa settimana?",
                    "Violazioni di framework (size > 15%, skip validate, ignorato earnings)?",
                    "Stock vs ETF: dove sta il tuo edge? Tilt da fare?",
                    "Regime macro sta cambiando? Confronta rotate vs lunedì scorso",
                ],
            ),
            (
                "Claude 3D (post-trade)",
                "per ogni chiusura",
                [
                    "Setup 2 settimane dopo — pattern ricorrenti?",
                    "Tesi corretta? Timing? Stop/target ben posizionati?",
                ],
            ),
        ],
    },
    6: {  # Sunday — Prep
        "name": "Prep Next Week",
        "duration": "~15min",
        "blocks": [
            (
                "Calendar prep",
                "—",
                [
                    "Calendar macro: CPI, FOMC, ECB, payrolls → blocca 24h prima",
                    "Earnings calendar: chi reporta della watchlist?",
                    "Se primo del mese in arrivo → prepara nuovo basket Pro Picks",
                    "Niente grafici: ricarica capacità mentale per lunedì",
                ],
            ),
        ],
    },
}


DAY_NAMES_IT = [
    "Lunedì",
    "Martedì",
    "Mercoledì",
    "Giovedì",
    "Venerdì",
    "Sabato",
    "Domenica",
]


def today_block(weekday: int) -> tuple[str, DayCadence]:
    """Restituisce (nome giorno italiano, cadence) per ``weekday`` in [0-6]."""
    weekday = weekday % 7
    return DAY_NAMES_IT[weekday], WEEKLY_CADENCE[weekday]
