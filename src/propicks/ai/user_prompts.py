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

import re
from datetime import date as _date

# Pattern per rimuovere la sezione "# Web search usage" dal SYSTEM_PROMPT nel
# build Perplexity. La sezione descrive il tool web_search server-side
# Anthropic (budget 2-5 calls, query economiche) — in Perplexity la search è
# built-in e sempre attiva, quindi il blocco è rumore che può confondere
# alcuni modelli (es. Sonar che cerca di "rispettare il budget" inesistente,
# o Claude via Pro che si lamenta di non avere il tool).
#
# La regex matcha da `\n# Web search usage\n` fino al prossimo `\n# `
# (lookahead non-consumato → preserva l'header della sezione successiva).
# DOTALL per matchare anche newline interni alla sezione.
_WEB_SEARCH_SECTION_RE = re.compile(
    r"\n# Web search usage\n.*?(?=\n# )",
    re.DOTALL,
)


def _strip_web_search_section(system_prompt: str) -> str:
    """Rimuove la sezione ``# Web search usage`` dal system prompt fornito.

    Usato solo nel build del fallback Perplexity. Il SYSTEM_PROMPT canonico
    (in ``ai/*_prompts.py``) resta intatto byte-per-byte per preservare la
    cache Anthropic via SDK — lo strip avviene su una copia in memoria.
    """
    return _WEB_SEARCH_SECTION_RE.sub("", system_prompt)


def is_italian_ticker(ticker: str) -> bool:
    """Ticker con suffisso ``.MI`` → Borsa Italiana (FTSE MIB e dintorni).

    Usata dai prompt builders per scegliere la lingua: italiano per nomi
    ``.MI`` (fonti Sole 24 Ore / MF / Equita), inglese per il resto del
    mondo (Bloomberg / Reuters / SEC / sell-side notes).
    """
    return ticker.upper().endswith(".MI")


def _today_iso(as_of_date: str | None = None) -> str:
    """Date anchor per i prompt Perplexity.

    Sonar (Sonar / Sonar Pro / Sonar Reasoning) interpreta i riferimenti
    temporali relativi (``last 30 days``, ``next 2 weeks``) usando il proprio
    cutoff di training se manca un anchor esplicito. Il risultato è stale
    info ~30% delle volte. Anteporre ``Today is YYYY-MM-DD.`` ai prompt
    forza Sonar a calcolare le finestre rispetto a oggi.

    Restituisce la stringa ISO della data fornita, o oggi se ``None``.
    """
    return as_of_date or _date.today().isoformat()


def perplexity_2a(
    ticker: str,
    company_name: str = "",
    strategy: str = "",
    *,
    as_of_date: str | None = None,
) -> str:
    """Prompt 2A — catalyst/news analysis for NEW basket entries (non-IT stocks).

    English by design: per `is_italian_ticker` this prompt is invoked only for
    non-`.MI` tickers (US/UK/DE/FR/etc.). The fact base for these names lives
    in English-language sources (Bloomberg, Reuters, SEC filings, earnings
    call transcripts, sell-side analyst notes), and Perplexity Sonar / Sonar
    Pro / Sonar Reasoning produce richer citations and reasoning in English
    than in Italian. Italian-language variant for FTSE MIB names is in
    ``perplexity_2b``.

    ``as_of_date`` (ISO YYYY-MM-DD, default oggi) viene anteposto per
    ancorare le finestre temporali — vedi ``_today_iso`` per il razionale.
    """
    name = company_name or ticker
    strat = strategy or "Pro Picks"
    today = _today_iso(as_of_date)
    return f"""Today is {today}.

I'm a trader evaluating whether to enter a position in {ticker} ({name}).
The name has just been added to the "{strat}" AI strategy on Investing Pro Picks.

I need a quick, fact-based briefing — no opinions, only verifiable facts with
dates and citations.

1. RECENT CATALYSTS (last 30 days):
   - Latest earnings: beat or miss vs consensus? Magnitude of the surprise on
     EPS and revenue? Guidance change (raised, reaffirmed, cut)?
   - Sell-side rating actions in the last 2 weeks: upgrades/downgrades, target
     price revisions (which firms, which direction)?
   - Material corporate actions: buybacks announced, M&A, product launches,
     management changes, capital raises?

2. IMMINENT RISKS:
   - Next earnings date (exact date, BMO/AMC if known)?
   - Pending litigation, SEC/regulatory inquiries, FTC/antitrust matters,
     restatements?
   - Sector- or macro-level pressure: rates, FX, commodity exposure that
     could re-rate the multiple?

3. POSITIONING & SENTIMENT:
   - Sell-side consensus rating distribution (Buy/Hold/Sell) and median
     12-month price target?
   - Short interest as % of float and days-to-cover (>5% = elevated)?
   - Recent insider activity (buys/sells in the last 90 days, named insiders
     if material)?

4. SECTOR CONTEXT:
   - Is the GICS sector outperforming or lagging the broad market over the
     last 1M / 3M?
   - Direct peers that recently reported and whose prints could move {ticker}
     (peer name + result direction)?

Respond concisely with hard numbers, dates, and source citations where
possible. If a search returns inconclusive or contradictory data, flag it
explicitly rather than producing a synthesized opinion. No generic outlook,
no disclaimers, no "consult an advisor" — only verifiable facts."""


def perplexity_2b(
    ticker: str,
    company_name: str = "",
    *,
    as_of_date: str | None = None,
) -> str:
    """Prompt 2B — analisi per titoli italiani (FTSE MIB).

    Versione Sonar-aware:
    - Anchor data esplicito (anti-stale).
    - P/E storico 5y: chiede fonte specifica e ammette esplicitamente
      "non disponibile" come risposta valida (Sonar tende a confabulare
      medie storiche se non vede il dato).
    - Articoli recenti: titolo + data + URL distinti (no aggregazione).
    - Fonti italiane in priorità decrescente per indirizzare il retrieval.
    """
    name = company_name or ticker
    today = _today_iso(as_of_date)
    return f"""Oggi è {today}.

Sto valutando {ticker} ({name}) per il mio portafoglio di azioni italiane.
Analisi fattuale, no opinioni, no consensus aggregato.

Fonti preferite in ordine: Borsa Italiana (borsaitaliana.it), Sole 24 Ore,
Mediobanca Securities, Equita SIM, Intesa Sanpaolo Research, Reuters Italia,
MF/Milano Finanza, Bloomberg.

1. FONDAMENTALI RAPIDI (ultimo trimestre disponibile):
   - P/E attuale: dammi il valore + fonte + data di calcolo.
   - P/E medio 5 anni: cita il dato SOLO se trovi una serie storica
     verificabile (es. su Borsa Italiana, Mediobanca Securities,
     Bloomberg). Se NON disponibile pubblicamente, scrivi
     "P/E medio 5y: non disponibile su fonti pubbliche" — non stimare.
   - Dividend yield corrente e prossima data stacco (se annunciata).
   - Debito netto / EBITDA all'ultima trimestrale (cita il valore + il
     trimestre di riferimento, es. "1.8x al Q4 2025").
   - Ultima guidance management: data del comunicato + direzione
     (alzata / confermata / tagliata) + key numbers.

2. CATALYST ITALIA-SPECIFICI (ultimi 90 giorni):
   - Impatto PNRR / incentivi governativi sul settore: notizia specifica
     con data, non commento generico.
   - Esposizione mercati emergenti / rischio geopolitico (es. Russia,
     Cina, MENA): % fatturato esterno se nota.
   - Index events: entrata/uscita FTSE MIB, modifiche peso, riapertura
     copertura sell-side.

3. RISCHI SPECIFICI:
   - Concentrazione azionariato: patti parasociali noti, presenza CDP /
     fondazioni / Stato / famiglia di controllo, % flottante.
   - Liquidità: ADV (controvalore medio) ultimi 30gg in EUR.
   - Eventi imminenti annunciati: assemblea, aumento capitale, OPA,
     spin-off, scadenze covenant.

4. NEWS RECENTI (ultimi 30 giorni — formato richiesto):
   Dammi ESATTAMENTE 3 notizie distinte, ognuna in questo formato:
   - Titolo: "<titolo originale>"
     Fonte: <nome testata>
     Data: YYYY-MM-DD
     URL: <link diretto se disponibile>
     Sintesi: <una riga, max 25 parole>

   Se non trovi 3 notizie materiali distinte, dammene meno e dichiaralo:
   "trovate solo N notizie materiali nel periodo".

5. ANALYST COVERAGE (se disponibile):
   - Rating più recenti da Mediobanca / Equita / Intesa: data + rating +
     target price. Solo se trovi il dato verificato; altrimenti
     "coverage non reperibile".

Output: dati numerici precisi, date in formato YYYY-MM-DD, URL espliciti
quando possibile. Se un dato non è verificabile su fonte pubblica, dichiaralo
esplicitamente — preferisco un "non disponibile" a una stima sintetica."""


def perplexity_2c(ticker: str, *, as_of_date: str | None = None) -> str:
    """Prompt 2C — quick pre-entry check (last 24h red flags).

    Auto-language: Italian for `.MI` tickers (FTSE MIB), English otherwise.
    Same fact-base focus across both versions, only the wording adapts to the
    most likely source language for the name being checked.

    ``as_of_date`` (ISO YYYY-MM-DD, default oggi): ancora "ultime 24h" /
    "prossime 2 settimane" alla data corretta. Senza anchor Sonar usa il
    suo cutoff training → "ultime 24h" diventa stale.
    """
    today = _today_iso(as_of_date)
    if is_italian_ticker(ticker):
        return f"""Oggi è {today}.

Check rapido su {ticker} prima di entrare in posizione oggi.

Rispondimi SOLO con:
1. C'è qualche news delle ultime 24 ore che cambia il quadro?
2. Earnings nelle prossime 2 settimane? Se sì, data esatta.
3. Il pre-market/after-hours mostra movimenti anomali?
4. Volume di oggi vs media 30 giorni: normale o anomalo?

Solo fatti, risposte brevi. Se non c'è nulla di rilevante, dimmi
"Nessun red flag nelle ultime 24h"."""

    return f"""Today is {today}.

Quick pre-entry check on {ticker} before opening a position today.

Answer ONLY:
1. Any material news in the last 24 hours that changes the picture?
2. Earnings in the next 2 weeks? If yes, exact date and BMO/AMC if known.
3. Pre-market / after-hours showing anomalous moves vs prior close?
4. Today's volume vs 30-day average: normal or anomalous?

Facts only, short answers. If nothing material, just say
"No red flags in the last 24h"."""


def perplexity_contrarian(
    ticker: str,
    company_name: str = "",
    *,
    as_of_date: str | None = None,
) -> str:
    """Prompt contrarian — FLUSH vs BREAK discriminator on the selloff cause.

    Mirror del system prompt di ``contrarian_validator``: l'engine ha già
    confermato l'oversold tecnico (RSI < 30, stretch ATR sotto EMA50, sopra
    EMA200 weekly). Quello che il trader deve verificare a mano è la **causa
    del selloff**.

    Auto-language: italiano per ticker ``.MI`` (FTSE MIB), inglese per il
    resto. I nomi US/EU/UK hanno fonti primarie in inglese (analyst notes,
    SEC filings, earnings call) mentre per i .MI il contesto Equita /
    Mediobanca / Sole 24 Ore è meglio leggibile in italiano.

    ``as_of_date`` (ISO YYYY-MM-DD, default oggi): ancora "ultimi 5-15
    giorni" / "ultime 2 settimane" alla data corretta. Critico per Sonar
    su query event-driven dove il selloff è recente per definizione.
    """
    name = company_name or ticker
    today = _today_iso(as_of_date)

    if is_italian_ticker(ticker):
        return f"""Oggi è {today}.

Sto valutando un setup CONTRARIAN su {ticker} ({name}).

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
   - Indagini Consob/SEC, restatement, dimissioni auditor o CFO?
   - Whistleblower, allegations di accounting?
   - Se SÌ a uno qualsiasi → REJECT con prejudice, mai mean-revertare frode.

6. EARNINGS CALENDAR:
   - Quando sono le PROSSIME earnings? (data esatta)
   - Se < 2 settimane: il setup è ad alto rischio gap. Specifica la data.

Rispondi con dati precisi, date, citazioni di analyst notes se disponibili.
**Se la causa del selloff non è chiara dopo la ricerca, dillo esplicitamente:
"causa unknown"** invece di assumere flush. Assenza di evidenza di break NON
è evidenza di assenza."""

    return f"""Today is {today}.

I'm evaluating a CONTRARIAN long setup on {ticker} ({name}).

The name is oversold (RSI < 30, multi-ATR stretched below the 50-EMA) but the
long-term structural trend (above the 200-week EMA) is still intact. My one
question: **is the recent selloff a tradable FLUSH or a structural BREAK that
should kill the entry?**

Search specifically for the CAUSE of the selloff over the last 5-15 sessions:

1. SELLOFF CATALYST (the primary question):
   - What drove the decline? Earnings miss, guidance cut, macro news, sector
     rotation, or no clear catalyst (technical_only)?
   - If earnings-driven: EPS/revenue beat or miss vs consensus, guidance
     direction (raised/reaffirmed/cut), management commentary on transitory
     drivers (weather, FX, channel disruption) vs structural deterioration.

2. FLUSH vs BREAK DISCRIMINATOR:
   - **FLUSH (tradable)**: risk-off macro_flush, sector_rotation,
     technical_only without material news, earnings beat but "sell the news".
   - **BREAK (NOT tradable)**: earnings_miss_fundamental with real
     deterioration, guidance_cut with consensus estimates being marked down,
     fraud / SEC inquiry / restatement.
   - **MIXED**: flush with marginal fundamental weakening.

3. ANALYST REACTION (critical for BREAK detection):
   - Have consensus estimates been CUT sharply in the last 2 weeks?
     (bad sign — multiple needs to re-rate lower, mean reversion isn't enough)
   - Or are estimates holding / being raised? (supportive of FLUSH)
   - Median price target: revised down or stable?

4. PEER ACTION:
   - Are sector peers down by a similar magnitude? → sector_rotation / macro_flush
   - Is {ticker} down materially more than peers? → name-specific (break risk)

5. HARD-REJECT RED FLAGS:
   - SEC inquiry / DOJ probe, restatement, auditor or CFO resignation?
   - Whistleblower complaints, accounting irregularities?
   - If YES on any → REJECT with prejudice. Never mean-revert into fraud.

6. EARNINGS CALENDAR:
   - When is the NEXT earnings release? (exact date, BMO/AMC)
   - If < 2 weeks: gap risk is high — flag the date explicitly.

Respond with hard numbers, dates, and analyst-note citations where available.
**If the cause of the selloff isn't clear after the search, say so
explicitly: "cause unknown"** — do NOT assume a flush by default. Absence
of evidence of a break is NOT evidence of absence."""


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


# ---------------------------------------------------------------------------
# Validate fallback: prompt completi per LLM alternativi (Perplexity primary)
# ---------------------------------------------------------------------------
# Ricostruisce il payload che ``thesis_validator.validate_thesis`` (stock),
# ``contrarian_validator.validate_contrarian_thesis`` (contrarian) o
# ``etf_validator.validate_rotation`` (ETF) manda all'API Anthropic. Serve
# come piano B quando l'API Anthropic è down, la chiave è esaurita, il budget
# giornaliero è saturo, o vuoi un secondo parere da un LLM alternativo.
#
# **Target principale: Perplexity multi-modello** (Sonar / Sonar Pro / Sonar
# Reasoning / Claude Opus via Pro / GPT-5 via Pro / Gemini via Pro). Perplexity
# ha web search built-in sempre attiva → la sezione "Web search usage" del
# system prompt va contestualizzata, non rimossa (i SYSTEM_PROMPT in
# ``ai/*_prompts.py`` sono frozen per cache Anthropic — qualsiasi byte cambiato
# invalida il prompt cache lato SDK).
#
# Pattern: model-guidance header + system prompt INTATTO + user prompt
# parametrizzato + schema JSON come istruzione finale tollerante.
#
# **Compatibilità API Claude/Anthropic**: il payload ricostruito è
# byte-equivalente a quello inviato dall'SDK (``claude_client.py``). Funziona
# pari pari incollato in Claude web app, console.anthropic.com, o qualsiasi
# integrazione che riceve il system+user concatenati. Il model-guidance header
# è prefisso prosaico, non interferisce con la logica del system prompt.

_PERPLEXITY_HEADER = """# MODEL GUIDANCE — Perplexity multi-model

Stai operando in Perplexity con web search built-in sempre attiva. Usa la
search liberamente per produrre dati verificabili (spot prices, earnings
date, recent news, analyst notes, peer performance, fund flows). Cita le
fonti.

Adattati al modello che sta rispondendo:

- **Sonar / Sonar Pro / Sonar Reasoning**: search live + citazioni. Schema
  JSON enforce-friendly → rispondi SOLO col JSON, una sola response.
- **Claude / GPT / Gemini via Perplexity Pro**: persona "senior PM" del
  system prompt qui sotto. Schema JSON enforce-friendly.
- **Modelli senza JSON mode strict**: se non riesci a produrre JSON valido,
  usa il fallback `---JSON---` separator descritto in "OUTPUT SCHEMA".

---

"""

_LLM_GENERIC_HEADER = """# MODEL GUIDANCE — generic LLM (Claude.ai / ChatGPT / Gemini direct)

Stai ricevendo questo prompt come fallback dell'integrazione SDK Anthropic
quando l'API Claude non è raggiungibile (chiave esaurita, budget giornaliero
saturo, rete down, region restriction). Il system prompt qui sotto è
**byte-equivalente** a quello inviato dall'integrazione → su Claude.ai e
sull'SDK Anthropic funziona pari pari, segui le istruzioni come scritte.

Adattati al tuo ambiente:

- **Claude.ai (web app) / console.anthropic.com / Anthropic SDK diretto**:
  segui il system prompt integralmente. Se hai accesso al tool `web_search`
  (Workbench beta o configurato nel SDK), abilitalo come da sezione "# Web
  search usage" — budget 2-4 ricerche per ticker, max 5. Senza web search
  disponibile, scrivi "unknown — search unavailable" nei campi che
  richiedono dati real-time (spot prices, earnings date, recent news).
- **ChatGPT (gpt-5, gpt-4o, o-series) con web browsing/search abilitato**:
  cerca online quando il system prompt te lo richiede esplicitamente.
  Senza browsing → dichiara esplicitamente il gap nel campo rilevante.
- **Gemini (2.5 Pro, 2.5 Flash) con grounding Google Search**: stesso
  comportamento — usa la search se disponibile, altrimenti dichiara il gap.

Lo schema JSON finale è enforce-friendly su tutti i modelli citati.
Rispondi **SOLO** con un oggetto JSON valido, nessun testo prima o dopo.

---

"""

_SCHEMA_INSTRUCTION_PERPLEXITY = """

---

# OUTPUT SCHEMA

Rispondi con un oggetto JSON valido che rispetti questo schema. **Preferito**:
solo il JSON, nessun testo prima o dopo. **Fallback** (se il tuo modello non
supporta JSON mode strict): prosa breve di analisi (max 200 parole) +
separator `---JSON---` + JSON valido sotto.

```json
{schema}
```
"""

_SCHEMA_INSTRUCTION_STRICT = """

---

# OUTPUT SCHEMA

Rispondi esclusivamente con un oggetto JSON valido che rispetti QUESTO schema.
NESSUN testo prima o dopo, nessun markdown wrapping, nessun preambolo. Lo
schema è enforce-friendly su tutti i modelli moderni (Claude 3.5+, GPT-4+,
Gemini 1.5+):

```json
{schema}
```
"""


def _format_schema_block(schema: dict) -> str:
    import json
    return json.dumps(schema, indent=2, ensure_ascii=False)


def perplexity_stock_validate_full(analysis: dict, as_of_date: str) -> str:
    """Prompt completo stock ``--validate`` — fallback Perplexity multi-modello.

    Concatena: model-guidance Perplexity-specifico + system prompt Anthropic
    **senza la sezione `# Web search usage`** (rimossa via
    ``_strip_web_search_section`` perché Perplexity ha search built-in) +
    user prompt + schema JSON tollerante.

    Il SYSTEM_PROMPT canonico in ``ai/prompts.py`` resta intatto: lo strip
    avviene solo su una copia in memoria. Per la variante che mantiene la
    sezione web search vedi ``llm_generic_stock_validate_full``.
    """
    # Import lazy per evitare ciclo (claude_client importa pydantic/anthropic).
    from propicks.ai.claude_client import _JSON_SCHEMA
    from propicks.ai.prompts import SYSTEM_PROMPT, render_user_prompt

    system = _strip_web_search_section(SYSTEM_PROMPT)
    user = render_user_prompt(analysis, as_of_date=as_of_date)
    schema_block = _SCHEMA_INSTRUCTION_PERPLEXITY.format(
        schema=_format_schema_block(_JSON_SCHEMA)
    )
    return (
        _PERPLEXITY_HEADER
        + "# SYSTEM\n\n" + system.rstrip() + "\n\n"
        + "# USER\n\n" + user.rstrip() + schema_block
    )


def perplexity_contrarian_validate_full(analysis: dict, as_of_date: str) -> str:
    """Prompt completo contrarian ``--validate`` — fallback Perplexity multi-modello.

    Stesso pattern di ``perplexity_stock_validate_full``: rimuove la sezione
    ``# Web search usage`` dal CONTRA_SYSTEM_PROMPT in memoria. Il file
    canonico resta intatto.
    """
    from propicks.ai.claude_client import _CONTRA_JSON_SCHEMA
    from propicks.ai.contrarian_prompts import (
        CONTRA_SYSTEM_PROMPT,
        render_contrarian_user_prompt,
    )

    system = _strip_web_search_section(CONTRA_SYSTEM_PROMPT)
    user = render_contrarian_user_prompt(analysis, as_of_date=as_of_date)
    schema_block = _SCHEMA_INSTRUCTION_PERPLEXITY.format(
        schema=_format_schema_block(_CONTRA_JSON_SCHEMA)
    )
    return (
        _PERPLEXITY_HEADER
        + "# SYSTEM\n\n" + system.rstrip() + "\n\n"
        + "# USER\n\n" + user.rstrip() + schema_block
    )


def perplexity_etf_validate_full(
    ranked: list[dict],
    allocation: dict | None,
    as_of_date: str,
    region: str,
    benchmark: str,
    shown: int = 11,
) -> str:
    """Prompt completo ETF rotation ``--validate`` — fallback Perplexity multi-modello.

    Stesso pattern di ``perplexity_stock_validate_full``: rimuove la sezione
    ``# Web search usage`` dall'ETF_SYSTEM_PROMPT in memoria.
    """
    from propicks.ai.claude_client import _ETF_JSON_SCHEMA
    from propicks.ai.etf_prompts import ETF_SYSTEM_PROMPT, render_etf_user_prompt

    system = _strip_web_search_section(ETF_SYSTEM_PROMPT)
    user = render_etf_user_prompt(
        ranked=ranked,
        allocation=allocation,
        as_of_date=as_of_date,
        region=region,
        benchmark=benchmark,
        shown=shown,
    )
    schema_block = _SCHEMA_INSTRUCTION_PERPLEXITY.format(
        schema=_format_schema_block(_ETF_JSON_SCHEMA)
    )
    return (
        _PERPLEXITY_HEADER
        + "# SYSTEM\n\n" + system.rstrip() + "\n\n"
        + "# USER\n\n" + user.rstrip() + schema_block
    )


def llm_generic_stock_validate_full(analysis: dict, as_of_date: str) -> str:
    """Prompt completo stock ``--validate`` — fallback per LLM generici.

    Target: Claude.ai web app, console Anthropic, ChatGPT (gpt-5/gpt-4o),
    Gemini (2.5 Pro/Flash). System prompt Anthropic INTATTO byte-per-byte
    → compat piena con SDK Claude e claude.ai. Header dedicato a LLM
    generici (no Perplexity-specific guidance) e schema JSON strict.

    Differenza vs ``perplexity_stock_validate_full``:
    - Header: focus su Claude/ChatGPT/Gemini, niente menzione Sonar.
    - Schema: strict (no `---JSON---` fallback), perché tutti i modelli
      target supportano JSON mode in modo affidabile.
    - Web search: l'header chiede al modello di usare il proprio tool
      (Anthropic web_search tool, ChatGPT browsing, Gemini grounding).
    """
    from propicks.ai.claude_client import _JSON_SCHEMA
    from propicks.ai.prompts import SYSTEM_PROMPT, render_user_prompt

    user = render_user_prompt(analysis, as_of_date=as_of_date)
    schema_block = _SCHEMA_INSTRUCTION_STRICT.format(
        schema=_format_schema_block(_JSON_SCHEMA)
    )
    return (
        _LLM_GENERIC_HEADER
        + "# SYSTEM\n\n" + SYSTEM_PROMPT.rstrip() + "\n\n"
        + "# USER\n\n" + user.rstrip() + schema_block
    )


def llm_generic_contrarian_validate_full(analysis: dict, as_of_date: str) -> str:
    """Prompt completo contrarian ``--validate`` — fallback per LLM generici.

    Stesso pattern di ``llm_generic_stock_validate_full`` ma con
    ``CONTRA_SYSTEM_PROMPT`` (event-driven / mean-reversion PM persona) +
    schema ``_CONTRA_JSON_SCHEMA``.
    """
    from propicks.ai.claude_client import _CONTRA_JSON_SCHEMA
    from propicks.ai.contrarian_prompts import (
        CONTRA_SYSTEM_PROMPT,
        render_contrarian_user_prompt,
    )

    user = render_contrarian_user_prompt(analysis, as_of_date=as_of_date)
    schema_block = _SCHEMA_INSTRUCTION_STRICT.format(
        schema=_format_schema_block(_CONTRA_JSON_SCHEMA)
    )
    return (
        _LLM_GENERIC_HEADER
        + "# SYSTEM\n\n" + CONTRA_SYSTEM_PROMPT.rstrip() + "\n\n"
        + "# USER\n\n" + user.rstrip() + schema_block
    )


def llm_generic_etf_validate_full(
    ranked: list[dict],
    allocation: dict | None,
    as_of_date: str,
    region: str,
    benchmark: str,
    shown: int = 11,
) -> str:
    """Prompt completo ETF rotation ``--validate`` — fallback per LLM generici."""
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
    schema_block = _SCHEMA_INSTRUCTION_STRICT.format(
        schema=_format_schema_block(_ETF_JSON_SCHEMA)
    )
    return (
        _LLM_GENERIC_HEADER
        + "# SYSTEM\n\n" + ETF_SYSTEM_PROMPT.rstrip() + "\n\n"
        + "# USER\n\n" + user.rstrip() + schema_block
    )


def perplexity_etf_rotation(
    ranked: list[dict],
    region: str,
    *,
    as_of_date: str | None = None,
) -> str:
    """Synthetic Perplexity prompt for ETF rotation — equivalent of ``perplexity_2a``.

    English by design: the sector-rotation universe (SPDR Select Sector,
    Xtrackers MSCI World, UCITS wrappers) is documented and traded primarily
    via English-language sources (Reuters fund flows, ETF.com, Bloomberg ETF
    dashboard, sector-strategy notes). Italian translation would only narrow
    the citation pool.

    Args:
        ranked: output of ``rank_universe`` (sorted by score). Uses the
            top-3 tickers and sector keys to personalise the questions.
        region: ``US`` | ``EU`` | ``WORLD`` | ``ALL`` — included in prompt context.
        as_of_date: ISO YYYY-MM-DD anchor (default oggi). Necessario per
            "next 2-4 weeks" / "last 30 days" su Sonar.
    """
    top = ranked[:3] if ranked else []
    today = _today_iso(as_of_date)
    if not top:
        top_str = "(no top candidates available)"
    else:
        top_str = ", ".join(
            f"{r.get('ticker', '?')} ({r.get('sector_key', '?')})" for r in top
        )

    region_label = {
        "US": "US (SPDR Select Sector — XL*)",
        "EU": "EU (SPDR UCITS on Xetra — ZPD*.DE, identical Select Sector index)",
        "WORLD": "WORLD (Xtrackers MSCI World — XDW*/XWTS — plus IQQ6.DE as Real Estate proxy)",
        "ALL": "mixed US + WORLD",
    }.get(region.upper(), region)

    top_ticker = top[0].get("ticker", "?") if top else "?"

    # Vincolo alternative_sector: lista esplicita dei candidati ammessi
    # (universe completo passato dal ranker meno la top-3). Senza questa
    # lista Sonar tende a inventare "Industrials defensive" o ticker non
    # esistenti come ZPDA.DE. La lista è region-aware perché viene dal
    # ``ranked`` stesso, che il chiamante costruisce sull'universo corretto.
    alternative_pool = [r for r in ranked[3:] if r.get("ticker")]
    if alternative_pool:
        alt_list = ", ".join(
            f"{r['ticker']} ({r.get('sector_key', '?')})" for r in alternative_pool
        )
        alt_block = (
            f"   - Choose from THIS list ONLY (do NOT invent tickers): {alt_list}.\n"
            "   - Pick exactly one ticker as the \"next in line\" and explain "
            "the macro/flow case in 2-3 sentences. If none deserves the upgrade, "
            "say so explicitly: \"none of the alternatives outranks the top-3\"."
        )
    else:
        alt_block = (
            "   - The ranker's universe contains only the top-3 above. "
            "Skip this section."
        )

    return f"""Today is {today}.

I'm evaluating a sector ETF rotation — region {region_label}.
The top-3 candidates by composite score (RS vs benchmark + regime fit + abs
momentum + trend) are:

{top_str}

I need a macro/catalyst cross-check covering the next 4-12 weeks:

1. RECENT ROTATION FLOWS (last 30 days):
   - Which sectors are attracting net inflows (ETF AUM changes, fund-flow
     data from Reuters/Bloomberg/ETF.com)?
   - Is there a re-rating underway (multiples expanding or compressing in
     specific sectors)?
   - Sector breadth: how many constituents are printing fresh 52-week
     highs/lows in each top-3 sector?

2. IMMINENT MACRO (next 2-4 weeks):
   - Any FOMC, CPI, NFP, ECB releases in the next 2-4 weeks? Exact dates.
   - Plausible hawkish/dovish surprise in those prints — which sector
     benefits and which suffers under each scenario?
   - Yield curve: is 10Y-2Y steepening or flattening? Which sector
     correlates positively with the current move?

3. SECTOR-SPECIFIC NARRATIVE (one block per top-3 entry):
   - Is there a dominant narrative driving the sector (e.g. AI capex,
     energy security, healthcare M&A, financials NIM expansion)?
   - Relative peer dispersion: is {top_ticker} aligned with other ETFs in
     the same sector, or is there a tracking gap that signals concentration?
   - Earnings season: which sector heavyweights are reporting in the next
     2-4 weeks and on what dates?

4. RED FLAGS:
   - Any sector favoured by the regime but punished by recent catalysts
     (e.g. tech in BULL but with mega-cap guidance cuts)?
   - Policy/regulatory shifts that could invert the trend (tariffs,
     antitrust, ESG mandates, banking capital rules)?
   - Concentration risk: is the top-pick dominated by 3-5 mega-cap names
     that could move together?

5. ALTERNATIVE SECTOR (constrained):
{alt_block}

Respond with hard numbers, dates, and source citations. If the search
returns inconclusive evidence on a point, say so explicitly — an "unknown"
is more useful than a synthesized generic opinion. No disclaimers, no
"consult an advisor" — verifiable macro facts only."""


# ---------------------------------------------------------------------------
# Sonar-native validate prompts — variante distillata per Sonar / Sonar Pro /
# Sonar Reasoning (Perplexity nativi)
# ---------------------------------------------------------------------------
# I ``perplexity_*_validate_full`` riusano i SYSTEM_PROMPT Anthropic (lunghi,
# Claude-tuned, con dimensioni soggettive da analyst training). Su Sonar
# quel pattern degrada per tre ragioni:
#
# 1. **Context utilization peggiore**: Sonar dilui le istruzioni su system
#    prompt > 50 righe. Le 6 dimensioni di ``confidence_by_dimension``
#    vengono riempite meccanicamente con valori 5-7 → signal perso.
# 2. **Self-consistency check inaffidabile**: Sonar non fa chain-of-thought
#    interno. Le hard rule vanno trasformate in regole **computabili**
#    (es: ``if R/R < 2.0 then verdict = CAUTION``).
# 3. **JSON mode strict inaffidabile su Sonar Pro**: il fallback
#    ``---JSON---`` separator deve essere il **default consigliato**, non
#    il piano B.
#
# Differenze chiave vs ``perplexity_*_validate_full``:
# - Schema in cima (Sonar honoriza meglio gli schema visti per primi).
# - System prompt distillato (~30 righe vs ~70): persona + framework +
#   regole computabili + istruzioni retrieval-first.
# - ``confidence_by_dimension`` ridotto da 6 a 3 chiavi.
# - Output formato: prosa breve + ``---JSON---`` separator (default).
# - Le dimensioni soggettive ("moat", "capital allocation track record")
#   sono tradotte in retrieval queries esplicite.
#
# I ``perplexity_*_validate_full`` originali restano disponibili per quando
# l'utente sceglie un modello Pro (Claude/GPT/Gemini via Perplexity Pro)
# che gestisce bene system prompt lunghi.

_SONAR_HEADER = """# MODEL GUIDANCE — Sonar-native (Perplexity)

Stai operando in Sonar / Sonar Pro / Sonar Reasoning con web search built-in
sempre attiva. Linee guida:

- Cita ogni fact con [source.com, YYYY-MM-DD] inline o come footnote.
- Se un dato non è recuperabile via search, scrivi ``unknown — not found``.
  NON inferire, NON estrapolare dal training.
- Output format: prosa breve di analisi (max 200 parole) + separator
  ``---JSON---`` su riga propria + JSON valido sotto. Lo schema è in cima.

---

"""


_SONAR_STOCK_SCHEMA = {
    "verdict": "CONFIRM | CAUTION | REJECT",
    "conviction_score": "integer 0-10",
    "thesis_summary": "string, 2-3 sentences, falsifiable",
    "bull_case": "string, falsifiable, with at least one cited fact",
    "bear_case": "string, falsifiable, with at least one cited fact",
    "reward_risk_ratio": "float, computed as (target - current_price) / (current_price - stop), 2 decimals",
    "stop_suggested": "float, price level",
    "target_suggested": "float, price level",
    "stop_rationale": "string, one sentence, references a structural level (prior swing low, EMA, pivot)",
    "target_rationale": "string, one sentence, references a structural level",
    "time_horizon": "SHORT (1-4w) | MEDIUM (1-3m) | LONG (3-6m)",
    "invalidation_deadline": "YYYY-MM-DD date",
    "entry_tactic": "MARKET_NOW | LIMIT_PULLBACK | WAIT_VOLUME_CONFIRMATION | SCALE_IN",
    "alignment_with_technicals": "STRONG | MIXED | WEAK",
    "confidence_by_dimension": {
        "fundamentals": "integer 0-10 (business durability + balance sheet)",
        "catalyst_credibility": "integer 0-10 (3-6m catalyst path)",
        "risk_asymmetry": "integer 0-10 (R/R credibility)",
    },
    "key_risks": ["list of strings, 2-4 items, specific"],
    "invalidation_triggers": ["list of strings, 2-4 items, observable conditions"],
    "sources": ["list of [source.com, YYYY-MM-DD] tuples cited"],
}


_SONAR_STOCK_SYSTEM = """You are a senior long/short equity PM (15+ years, fundamental
fund). You are the qualitative validation layer for a momentum stock engine
that has already passed a quantitative technical screen. Your job: produce
an independent verdict.

# Retrieval queries (use web search):
- Most recent earnings: beat/miss vs consensus + guidance change.
- Next earnings date (exact YYYY-MM-DD).
- Short interest as % of float and days-to-cover.
- Sector ETF performance last 30 days (e.g. XLE, GDX, XLK).
- Material company-specific news last 30 days.

# Hard computable rules (apply mechanically, no interpretation):
- IF reward_risk_ratio < 2.0 → verdict MUST be CAUTION or REJECT (never CONFIRM).
- IF regime is BEAR or STRONG_BEAR → verdict MUST be REJECT unless bull_case
  cites a specific falsifiable catalyst (e.g. buyout, idiosyncratic re-rating).
- IF regime is STRONG_BEAR → verdict MUST be REJECT regardless.
- IF conviction_score >= 7 AND alignment_with_technicals != STRONG → downgrade
  conviction_score to 6.
- CONFIRM requires: reward_risk_ratio >= 2.0 AND regime in
  {STRONG_BULL, BULL, NEUTRAL} AND conviction_score >= 7.

# Anti-fabrication:
- Do NOT invent earnings dates, analyst targets, or short interest.
  Use web_search results or write "unknown — not found".
- Do NOT echo the engine's 0-100 technical scores. Your scale is 0-10.
- Be specific and falsifiable: "strong moat" = useless;
  "pricing power in ad auctions post-ATT" = useful.

# Output: see schema at top of message. Prosa breve + ---JSON--- + JSON."""


_SONAR_CONTRA_SCHEMA = {
    "verdict": "CONFIRM | CAUTION | REJECT",
    "conviction_score": "integer 0-10",
    "thesis_summary": "string, 2-3 sentences, names the selloff cause",
    "flush_vs_break": "FLUSH | BREAK | MIXED",
    "catalyst_type": "macro_flush | sector_rotation | earnings_miss_fundamental | guidance_cut | fraud_or_accounting | technical_only | other",
    "bull_case": "string, falsifiable",
    "bear_case": "string, falsifiable",
    "reversion_target": "float, price level (typically EMA50 daily)",
    "invalidation_price": "float, price level (HARD STOP)",
    "time_horizon_days": "integer 3-30 (typical 5-15 for clean setups)",
    "entry_tactic": "MARKET_NOW | LIMIT_BELOW | SCALE_IN_TRANCHES | WAIT_STABILIZATION",
    "confidence_by_dimension": {
        "quality_persistence": "integer 0-10 (still high-quality in 12 months?)",
        "catalyst_credibility": "integer 0-10 (confidence in FLUSH vs BREAK call)",
        "risk_asymmetry": "integer 0-10 (R/R to reversion target)",
    },
    "key_risks": ["list of strings, 2-4 items"],
    "invalidation_triggers": ["list of strings, 2-4 items"],
    "sources": ["list of [source.com, YYYY-MM-DD] tuples cited"],
}


_SONAR_CONTRA_SYSTEM = """You are a senior event-driven / mean-reversion PM
(15+ years, long/short fund). Your edge: separating quality names that got
flushed (tradable) from quality names that are actually breaking (not tradable).

The engine has already confirmed the technical oversold (RSI<30, multi-ATR
below EMA50, above EMA200 weekly). Your job: classify the SELLOFF CAUSE.

# Retrieval queries (priority order — selloff cause is recent by definition):
- "<TICKER> news last 14 days" — the specific reason for the decline.
- If earnings: beat/miss vs consensus + guidance change + management
  commentary on transitory vs structural drivers.
- Analyst reaction: are estimates being CUT sharply (BREAK signal) or
  holding (FLUSH signal)?
- Sector peer action: are peers down similar magnitude (sector/macro flush)
  or is <TICKER> down more (name-specific = break risk)?
- Regulatory/legal/accounting: SEC inquiry, restatement, auditor resignation.

# Hard computable rules:
- IF catalyst_type == "fraud_or_accounting" → verdict = REJECT (with prejudice).
- IF catalyst_type == "guidance_cut" AND consensus estimates being cut sharply
  → verdict = REJECT.
- IF flush_vs_break == "BREAK" → verdict = REJECT.
- IF regime == "STRONG_BULL" → verdict = REJECT (oversold edge collapses).
- IF regime == "STRONG_BEAR" → verdict = REJECT (falling knife regime).
- IF cause unknown after retrieval → catalyst_type = "technical_only" AND
  verdict = CAUTION at most (do NOT assume flush; absence of evidence is
  not evidence of absence).
- CONFIRM requires: flush_vs_break in {FLUSH, MIXED-leaning-flush} AND
  quality_persistence >= 7 AND catalyst_credibility >= 7 AND conviction >= 7.

# Anti-fabrication:
- Do NOT invent earnings dates, analyst revisions, or peer numbers.
- Use web_search or write "unknown — not found".

# Output: see schema at top of message. Prosa breve + ---JSON--- + JSON."""


_SONAR_ETF_SCHEMA = {
    "verdict": "CONFIRM | CAUTION | REJECT",
    "conviction_score": "integer 0-10",
    "thesis_summary": "string, 2-3 sentences",
    "top_sector_verdict": "ticker from the proposed slate, OR 'FLAT' if no exposure recommended",
    "alternative_sector": "ticker from the universe (NOT in top-3) OR null. MUST come from the constrained list provided.",
    "bull_case": "string, falsifiable, with cited macro/flow data",
    "bear_case": "string, falsifiable",
    "stage": "EARLY (1-2M) | MID (3-6M) | LATE (6M+) | UNKNOWN",
    "entry_tactic": "ALLOCATE_NOW | STAGGER_3_TRANCHES | WAIT_PULLBACK | WAIT_CONFIRMATION | HOLD_CASH",
    "rebalance_horizon_weeks": "integer 2-12",
    "confidence_by_dimension": {
        "macro_fit": "integer 0-10 (rates/USD/commodities confirm ranking?)",
        "breadth_and_flows": "integer 0-10 (broad participation + AUM flows)",
        "rotation_stage": "integer 0-10 (early=high, late=low)",
    },
    "invalidation_triggers": ["list of strings, 2-4 items, observable macro/breadth conditions"],
    "sources": ["list of [source.com, YYYY-MM-DD] tuples cited"],
}


_SONAR_ETF_SYSTEM = """You are a senior macro strategist / multi-asset PM
(15+ years, sector rotation books). The engine has produced a ranked sector
ETF slate based on RS, regime fit, abs momentum, and trend. Your job:
stress-test the rotation thesis with macro/flow context the engine cannot see.

# Retrieval queries:
- US10Y / DXY / oil / copper / gold spot levels + last 30d direction.
- Top-pick ETF: AUM flows last 30 days (Reuters fund flows, ETF.com).
- Sector breadth: % constituents above 50-day MA for the top sector.
- Cross-sector leadership: is the top sector actually leading on rolling 1M?
- Policy events next 4-8 weeks: FOMC, CPI, OPEC, earnings season starts.

# Hard computable rules:
- IF regime == "STRONG_BEAR" → verdict = REJECT (or top_sector_verdict = "FLAT").
- IF stage == "LATE" AND breadth_and_flows < 5 → verdict = CAUTION at most.
- IF regime in {BEAR} AND proposed top is NOT defensive (XLP/XLU/XLV) → verdict = REJECT.
- IF alternative_sector is NOT in the constrained list provided → set to null.
- CONFIRM requires: conviction >= 7 AND breadth_and_flows >= 6 AND
  stage in {EARLY, MID}.

# Anti-fabrication:
- Do NOT invent flow numbers, breadth %, commodity prices.
- Do NOT invent ETF tickers — use only those in the slate or in the
  alternative_sector constrained list provided in the user message.
- Use web_search or write "unknown — not found".

# Output: see schema at top of message. Prosa breve + ---JSON--- + JSON."""


def _sonar_schema_block(schema: dict) -> str:
    """Schema in cima al prompt — formato leggibile per Sonar.

    Sonar honoriza meglio gli schema dichiarati come **primo elemento** del
    messaggio (visione retrieval-first: "what do you want me to find/output").
    Json indented + ensure_ascii=False per leggibilità nei log.
    """
    import json
    return (
        "# OUTPUT SCHEMA (read first)\n\n"
        "```json\n"
        + json.dumps(schema, indent=2, ensure_ascii=False)
        + "\n```\n\n"
        "Output format: prosa breve di analisi (max 200 parole) + separator\n"
        "``---JSON---`` su riga propria + JSON valido sotto. Lo schema sopra\n"
        "è la specifica esatta dei campi richiesti.\n\n"
        "---\n\n"
    )


def sonar_stock_validate_full(
    analysis: dict, as_of_date: str | None = None
) -> str:
    """Variante Sonar-native dello stock validate (momentum).

    Pattern: SCHEMA in cima → SONAR_HEADER → SYSTEM distillato →
    USER (render_user_prompt + date anchor).

    Differenza vs ``perplexity_stock_validate_full``:
    - Schema in cima invece che in fondo (Sonar honoriza meglio).
    - System prompt ridotto a ~30 righe, retrieval-first.
    - confidence_by_dimension a 3 chiavi (no 6).
    - Hard rules computabili, no self-consistency check.
    - Output format: prosa + ``---JSON---`` separator (default, no fallback).
    """
    from propicks.ai.prompts import render_user_prompt

    today = _today_iso(as_of_date)
    user_body = render_user_prompt(analysis, as_of_date=today)
    return (
        _sonar_schema_block(_SONAR_STOCK_SCHEMA)
        + _SONAR_HEADER
        + "# SYSTEM\n\n" + _SONAR_STOCK_SYSTEM.rstrip() + "\n\n"
        + "---\n\n"
        + "# USER\n\n"
        + f"Today is {today}.\n\n"
        + user_body.rstrip() + "\n"
    )


def sonar_contrarian_validate_full(
    analysis: dict, as_of_date: str | None = None
) -> str:
    """Variante Sonar-native del contrarian validate.

    Stesso pattern di ``sonar_stock_validate_full`` ma con
    ``_SONAR_CONTRA_SCHEMA`` + ``_SONAR_CONTRA_SYSTEM`` (focus FLUSH/BREAK).
    """
    from propicks.ai.contrarian_prompts import render_contrarian_user_prompt

    today = _today_iso(as_of_date)
    user_body = render_contrarian_user_prompt(analysis, as_of_date=today)
    return (
        _sonar_schema_block(_SONAR_CONTRA_SCHEMA)
        + _SONAR_HEADER
        + "# SYSTEM\n\n" + _SONAR_CONTRA_SYSTEM.rstrip() + "\n\n"
        + "---\n\n"
        + "# USER\n\n"
        + f"Today is {today}.\n\n"
        + user_body.rstrip() + "\n"
    )


def sonar_etf_validate_full(
    ranked: list[dict],
    allocation: dict | None,
    region: str,
    benchmark: str,
    shown: int = 11,
    as_of_date: str | None = None,
) -> str:
    """Variante Sonar-native dell'ETF rotation validate.

    Aggiunge automaticamente il blocco di alternative_sector vincolato alla
    lista del ranker (universo - top-3) per evitare confabulazione di ticker
    da parte di Sonar.
    """
    from propicks.ai.etf_prompts import render_etf_user_prompt

    today = _today_iso(as_of_date)
    user_body = render_etf_user_prompt(
        ranked=ranked,
        allocation=allocation,
        as_of_date=today,
        region=region,
        benchmark=benchmark,
        shown=shown,
    )

    # Lista vincolata per alternative_sector — universo escluse le prime 3.
    # Se passata vuota, lascia che il modello metta null.
    alt_pool = [r for r in ranked[3:] if r.get("ticker")]
    if alt_pool:
        alt_constraint = (
            "\n\n# ALTERNATIVE_SECTOR — constrained list\n\n"
            "Per il campo ``alternative_sector`` nello schema, scegli "
            "ESCLUSIVAMENTE da questo elenco (o null):\n"
            + "\n".join(
                f"- {r['ticker']} ({r.get('sector_key', '?')})" for r in alt_pool
            )
            + "\n\nNON inventare ticker. Se nessuno merita l'upgrade, metti null.\n"
        )
    else:
        alt_constraint = (
            "\n\n# ALTERNATIVE_SECTOR\n\n"
            "Universe contiene solo la top-3 mostrata sopra. "
            "Imposta ``alternative_sector`` = null.\n"
        )

    return (
        _sonar_schema_block(_SONAR_ETF_SCHEMA)
        + _SONAR_HEADER
        + "# SYSTEM\n\n" + _SONAR_ETF_SYSTEM.rstrip() + "\n\n"
        + "---\n\n"
        + "# USER\n\n"
        + f"Today is {today}.\n\n"
        + user_body.rstrip()
        + alt_constraint
    )


# ---------------------------------------------------------------------------
# Backwards-compat aliases (deprecati, da rimuovere in prossima major release)
# ---------------------------------------------------------------------------
# Mantenuti per non rompere import esterni o altri tool che chiamano i vecchi
# nomi. I nomi nuovi (``perplexity_*_validate_full``) sono più onesti sul
# destinatario reale del prompt.

claude_stock_validate_fallback = perplexity_stock_validate_full
claude_contrarian_validate_fallback = perplexity_contrarian_validate_full
claude_etf_validate_fallback = perplexity_etf_validate_full
