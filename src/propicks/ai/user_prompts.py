"""Template dei prompt che l'utente copia-incolla in Perplexity / Claude.

Distinti dai ``ai.prompts`` e ``ai.etf_prompts`` che contengono i **system
prompt** delle chiamate SDK (``--validate``). Questi template invece sono
materiale operativo: prompt pronti da incollare in Perplexity o nella web app
di Claude quando serve un cross-check indipendente a ``--validate`` oppure
un'analisi post-trade.

Fonte: ``docs/Trading_System_Playbook.md`` §2 (Perplexity) e §3 (Claude).
Il markdown resta la reference narrativa; qui vivono le versioni parametrizzate
consumate dalla dashboard per incollare il ticker corrente senza edit manuale.
"""

from __future__ import annotations


def perplexity_2a(ticker: str, company_name: str = "", strategy: str = "") -> str:
    """Prompt 2A — analisi news/catalyst per NUOVI ingressi nel basket."""
    name = company_name or ticker
    strat = strategy or "Pro Picks"
    return f"""Sono un trader che valuta se entrare su {ticker} ({name}).
Il titolo è appena stato inserito nella strategia AI "{strat}"
di Investing Pro Picks.

Ho bisogno di un'analisi rapida e fattuale:

1. CATALYST RECENTI (ultimi 30 giorni):
   - Ci sono stati earnings recenti? Se sì, beat o miss vs consensus?
   - Ci sono upgrade/downgrade degli analisti nelle ultime 2 settimane?
   - Annunci aziendali rilevanti (buyback, M&A, nuovi prodotti, guidance)?

2. RISCHI IMMINENTI:
   - Quando sono le prossime earnings? (data esatta)
   - Ci sono indagini, cause legali, o rischi regolatori pendenti?
   - Il settore è sotto pressione per motivi macro?

3. SENTIMENT:
   - Qual è il consensus degli analisti (buy/hold/sell) e il target price medio?
   - C'è short interest significativo (>5%)?
   - Ci sono insider buying o selling recenti?

4. CONTESTO SETTORIALE:
   - Il settore di appartenenza è in momentum positivo o negativo?
   - Ci sono competitor diretti che hanno riportato risultati
     che possono influenzare {ticker}?

Rispondi in modo conciso con dati e date specifiche.
Non mi servono opinioni generiche, solo fatti verificabili."""


def perplexity_2b(ticker: str, company_name: str = "") -> str:
    """Prompt 2B — analisi per titoli italiani (FTSE MIB)."""
    name = company_name or ticker
    return f"""Sto valutando {ticker} ({name}) per il mio portafoglio
di azioni italiane.

Analisi specifica per il mercato italiano:

1. FONDAMENTALI RAPIDI:
   - P/E attuale vs media settore e vs media storica 5 anni
   - Dividend yield e prossima data stacco dividendo
   - Debito netto / EBITDA
   - Ultima guidance del management

2. CATALYST ITALIA-SPECIFICI:
   - Impatto PNRR o incentivi governativi sul settore?
   - Esposizione a mercati emergenti o rischio geopolitico?
   - Posizione nell'indice FTSE MIB (entrata/uscita recente)?

3. RISCHI SPECIFICI:
   - Concentrazione azionariato (patto parasociale, fondazioni, stato)?
   - Liquidità media giornaliera (volume medio 30gg)?
   - Prossimi eventi: assemblea, aumento capitale, OPA?

4. NEWS RECENTI:
   - Ultimi 3 articoli rilevanti da Sole 24 Ore, MF, Reuters Italia
   - Commenti recenti di analisti italiani (Mediobanca, Intesa, Equita)

Rispondi con dati numerici precisi e fonti."""


def perplexity_2c(ticker: str) -> str:
    """Prompt 2C — check rapido pre-entry (red flag ultime 24h)."""
    return f"""Check rapido su {ticker} prima di entrare in posizione oggi.

Rispondimi SOLO con:
1. C'è qualche news delle ultime 24 ore che cambia il quadro?
2. Earnings nelle prossime 2 settimane? Se sì, data esatta.
3. Il pre-market/after-hours mostra movimenti anomali?
4. Volume di oggi vs media 30 giorni: normale o anomalo?

Solo fatti, risposte brevi. Se non c'è nulla di rilevante, dimmi
"Nessun red flag nelle ultime 24h"."""


def perplexity_contrarian(ticker: str, company_name: str = "") -> str:
    """Prompt contrarian — discriminante FLUSH vs BREAK sulla causa del selloff.

    Mirror del system prompt di ``contrarian_validator``: l'engine ha già
    confermato l'oversold tecnico (RSI < 30, stretch ATR sotto EMA50, sopra
    EMA200 weekly). Quello che il trader deve verificare a mano è la **causa
    del selloff**: è un flush macro/tecnico (tradable) o una frattura
    fondamentale (non tradable)?
    """
    name = company_name or ticker
    return f"""Sto valutando un setup CONTRARIAN su {ticker} ({name}).

Il titolo è oversold (RSI < 30, stretched multi-ATR sotto EMA50) ma il trend
strutturale di lungo periodo (sopra EMA200 weekly) è ancora intatto. La mia
domanda è una sola: **il selloff recente è un FLUSH tradable o un BREAK
fondamentale che sconsiglia l'entry?**

Cerca specificamente la CAUSA del selloff degli ultimi 5-15 giorni:

1. CATALYST DEL SELLOFF (la domanda principale):
   - Cosa ha causato il calo? Earnings miss, guidance cut, news macro,
     rotazione settoriale, o nessun catalyst evidente (technical_only)?
   - Se earnings: beat/miss vs consensus, guidance change, management commentary
     su drivers transitori (weather, FX, channel disruption) vs strutturali.

2. DISCRIMINANTE FLUSH vs BREAK:
   - **FLUSH (tradable)**: macro_flush risk-off, sector_rotation, technical_only
     senza news materiali, earnings beat ma sell-the-news.
   - **BREAK (NON tradable)**: earnings_miss_fundamental con deterioramento real,
     guidance_cut con stime in revisione al ribasso, fraud/SEC inquiry/restatement.
   - **MIXED**: flush con weakening fondamentale marginale.

3. ANALYST REACTION (cruciale per BREAK detection):
   - Le stime di consensus sono state TAGLIATE sharply nelle ultime 2 settimane?
     (bad sign — multiple needs to re-rate down, mean reversion non basta).
   - O reggono / sono state alzate? (supportive per FLUSH).
   - Target price medio: rivisto al ribasso o stabile?

4. PEER ACTION:
   - I peer del settore sono giù dello stesso ammontare? → sector_rotation/macro_flush
   - {ticker} è giù molto più dei peer? → name-specific (più rischio break)

5. RED FLAGS HARD-REJECT:
   - Indagini SEC, restatement, dimissioni auditor o CFO?
   - Whistleblower, allegations di accounting?
   - Se SÌ a uno qualsiasi → REJECT con prejudice, mai mean-revertare frode.

6. EARNINGS CALENDAR:
   - Quando sono le PROSSIME earnings? (data esatta)
   - Se < 2 settimane: il setup è ad alto rischio gap. Specifica la data.

Rispondi con dati precisi, date, citazioni di analyst notes se disponibili.
**Se la causa del selloff non è chiara dopo la ricerca, dillo esplicitamente:
"causa unknown"** invece di assumere flush. Assenza di evidenza di break NON
è evidenza di assenza."""


def claude_3d_post_trade(trade: dict) -> str:
    """Prompt Claude 3D — analisi post-trade per learning.

    ``trade`` è un record chiuso del journal. Usiamo solo i campi essenziali;
    i campi mancanti vengono sostituiti da ``?``.
    """
    def _fmt(v, fmt: str = "", default: str = "?") -> str:
        if v is None or v == "":
            return default
        if fmt == "price":
            return f"{float(v):.2f}"
        if fmt == "pct":
            return f"{float(v):+.2f}%"
        return str(v)

    ticker = _fmt(trade.get("ticker"))
    direction = _fmt(trade.get("direction"), default="LONG").upper()
    entry_price = _fmt(trade.get("entry_price"), "price")
    entry_date = _fmt(trade.get("entry_date"))
    exit_price = _fmt(trade.get("exit_price"), "price")
    exit_date = _fmt(trade.get("exit_date"))
    pnl_pct = _fmt(trade.get("pnl_pct"), "pct")
    catalyst = _fmt(trade.get("catalyst"), default="(non registrato)")
    exit_reason = _fmt(trade.get("exit_reason"), default="(non registrato)")

    return f"""Analisi post-trade per il mio journal di apprendimento.

TRADE COMPLETATO:
- Ticker: {ticker}
- Direzione: {direction}
- Entry: {entry_price} il {entry_date}
- Exit: {exit_price} il {exit_date}
- P/L: {pnl_pct}
- Motivo entry (catalyst): {catalyst}
- Motivo exit: {exit_reason}

Basandoti su questo trade, aiutami a identificare:

1. La TESI era corretta? (il catalyst si è materializzato?)
2. Il TIMING era giusto? (avrei fatto meglio ad aspettare o entrare prima?)
3. Lo STOP LOSS era posizionato correttamente?
4. Il TARGET era realistico?
5. Cosa avrei potuto fare DIVERSAMENTE?
6. Questo trade mi insegna qualcosa che posso sistematizzare
   come regola per il futuro?

Sii brutalmente onesto. Non mi interessa sentirmi meglio,
mi interessa migliorare."""


def is_italian_ticker(ticker: str) -> bool:
    """Ticker con suffisso ``.MI`` → usa il prompt 2B (italiano) invece del 2A."""
    return ticker.upper().endswith(".MI")


# ---------------------------------------------------------------------------
# Claude --validate fallback: prompt completo da incollare in un altro LLM
# ---------------------------------------------------------------------------
# Ricostruisce byte-per-byte il payload che ``thesis_validator.validate_thesis``
# (stock) o ``etf_validator.validate_rotation`` (ETF) manda all'API Anthropic.
# Serve come piano B quando l'API Anthropic è down, la chiave è esaurita o
# vuoi un secondo parere da un LLM alternativo (ChatGPT, Gemini, ecc).
#
# NB: lo schema JSON è incluso come istruzione finale perché Claude SDK lo
# enforce-a server-side; con un LLM generico bisogna chiederglielo in prompt.

_SCHEMA_INSTRUCTION_STOCK = """

---

Rispondi esclusivamente con un oggetto JSON valido che rispetti QUESTO schema (nessun testo prima o dopo):

```json
{schema}
```
"""


def _format_schema_block(schema: dict) -> str:
    import json
    return json.dumps(schema, indent=2, ensure_ascii=False)


def claude_stock_validate_fallback(analysis: dict, as_of_date: str) -> str:
    """Prompt completo stock ``--validate`` — fallback per LLM alternativi.

    Concatena system prompt, user prompt e istruzione di schema JSON. Output
    pronto per ``st.code()`` o clipboard.
    """
    # Import lazy per evitare ciclo (claude_client importa pydantic/anthropic).
    from propicks.ai.claude_client import _JSON_SCHEMA
    from propicks.ai.prompts import SYSTEM_PROMPT, render_user_prompt

    user = render_user_prompt(analysis, as_of_date=as_of_date)
    schema_block = _SCHEMA_INSTRUCTION_STOCK.format(
        schema=_format_schema_block(_JSON_SCHEMA)
    )
    return (
        "# SYSTEM\n\n" + SYSTEM_PROMPT.rstrip() + "\n\n"
        "# USER\n\n" + user.rstrip() + schema_block
    )


def claude_contrarian_validate_fallback(analysis: dict, as_of_date: str) -> str:
    """Prompt completo contrarian ``--validate`` — fallback per LLM alternativi.

    Concatena ``CONTRA_SYSTEM_PROMPT`` (event-driven / mean-reversion PM
    persona) + user prompt parametrizzato + schema JSON ``_CONTRA_JSON_SCHEMA``.
    Output pronto per ``st.code()`` o clipboard. Stesso pattern di
    ``claude_stock_validate_fallback`` ma con prompt e schema contrarian.
    """
    from propicks.ai.claude_client import _CONTRA_JSON_SCHEMA
    from propicks.ai.contrarian_prompts import (
        CONTRA_SYSTEM_PROMPT,
        render_contrarian_user_prompt,
    )

    user = render_contrarian_user_prompt(analysis, as_of_date=as_of_date)
    schema_block = _SCHEMA_INSTRUCTION_STOCK.format(
        schema=_format_schema_block(_CONTRA_JSON_SCHEMA)
    )
    return (
        "# SYSTEM\n\n" + CONTRA_SYSTEM_PROMPT.rstrip() + "\n\n"
        "# USER\n\n" + user.rstrip() + schema_block
    )


def claude_etf_validate_fallback(
    ranked: list[dict],
    allocation: dict | None,
    as_of_date: str,
    region: str,
    benchmark: str,
    shown: int = 11,
) -> str:
    """Prompt completo ETF rotation ``--validate`` — fallback per LLM alternativi.

    Signature coincide con ``render_etf_user_prompt`` per minimizzare drift
    se quella cambia.
    """
    from propicks.ai.claude_client import _ETF_JSON_SCHEMA
    from propicks.ai.etf_prompts import ETF_SYSTEM_PROMPT, render_etf_user_prompt

    user = render_etf_user_prompt(
        ranked=ranked,
        allocation=allocation,
        as_of_date=as_of_date,
        region=region,
        benchmark=benchmark,
        shown=shown,
    )
    schema_block = _SCHEMA_INSTRUCTION_STOCK.format(
        schema=_format_schema_block(_ETF_JSON_SCHEMA)
    )
    return (
        "# SYSTEM\n\n" + ETF_SYSTEM_PROMPT.rstrip() + "\n\n"
        "# USER\n\n" + user.rstrip() + schema_block
    )
