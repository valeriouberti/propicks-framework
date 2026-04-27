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


def perplexity_2a(ticker: str, company_name: str = "", strategy: str = "") -> str:
    """Prompt 2A — catalyst/news analysis for NEW basket entries (non-IT stocks).

    English by design: per `is_italian_ticker` this prompt is invoked only for
    non-`.MI` tickers (US/UK/DE/FR/etc.). The fact base for these names lives
    in English-language sources (Bloomberg, Reuters, SEC filings, earnings
    call transcripts, sell-side analyst notes), and Perplexity Sonar / Sonar
    Pro / Sonar Reasoning produce richer citations and reasoning in English
    than in Italian. Italian-language variant for FTSE MIB names is in
    ``perplexity_2b``.
    """
    name = company_name or ticker
    strat = strategy or "Pro Picks"
    return f"""I'm a trader evaluating whether to enter a position in {ticker} ({name}).
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
    """Prompt 2C — quick pre-entry check (last 24h red flags).

    Auto-language: Italian for `.MI` tickers (FTSE MIB), English otherwise.
    Same fact-base focus across both versions, only the wording adapts to the
    most likely source language for the name being checked.
    """
    if is_italian_ticker(ticker):
        return f"""Check rapido su {ticker} prima di entrare in posizione oggi.

Rispondimi SOLO con:
1. C'è qualche news delle ultime 24 ore che cambia il quadro?
2. Earnings nelle prossime 2 settimane? Se sì, data esatta.
3. Il pre-market/after-hours mostra movimenti anomali?
4. Volume di oggi vs media 30 giorni: normale o anomalo?

Solo fatti, risposte brevi. Se non c'è nulla di rilevante, dimmi
"Nessun red flag nelle ultime 24h"."""

    return f"""Quick pre-entry check on {ticker} before opening a position today.

Answer ONLY:
1. Any material news in the last 24 hours that changes the picture?
2. Earnings in the next 2 weeks? If yes, exact date and BMO/AMC if known.
3. Pre-market / after-hours showing anomalous moves vs prior close?
4. Today's volume vs 30-day average: normal or anomalous?

Facts only, short answers. If nothing material, just say
"No red flags in the last 24h"."""


def perplexity_contrarian(ticker: str, company_name: str = "") -> str:
    """Prompt contrarian — FLUSH vs BREAK discriminator on the selloff cause.

    Mirror del system prompt di ``contrarian_validator``: l'engine ha già
    confermato l'oversold tecnico (RSI < 30, stretch ATR sotto EMA50, sopra
    EMA200 weekly). Quello che il trader deve verificare a mano è la **causa
    del selloff**.

    Auto-language: italiano per ticker ``.MI`` (FTSE MIB), inglese per il
    resto. I nomi US/EU/UK hanno fonti primarie in inglese (analyst notes,
    SEC filings, earnings call) mentre per i .MI il contesto Equita /
    Mediobanca / Sole 24 Ore è meglio leggibile in italiano.
    """
    name = company_name or ticker

    if is_italian_ticker(ticker):
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

    return f"""I'm evaluating a CONTRARIAN long setup on {ticker} ({name}).

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


def perplexity_etf_rotation(ranked: list[dict], region: str) -> str:
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
    """
    top = ranked[:3] if ranked else []
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

    return f"""I'm evaluating a sector ETF rotation — region {region_label}.
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

5. ALTERNATIVE SECTOR:
   - Outside the top-3, which sector is the "next in line" that the
     framework might be underweighting? What's the case for it and which
     specific catalyst would put it on top?

Respond with hard numbers, dates, and source citations. If the search
returns inconclusive evidence on a point, say so explicitly — an "unknown"
is more useful than a synthesized generic opinion. No disclaimers, no
"consult an advisor" — verifiable macro facts only."""


# ---------------------------------------------------------------------------
# Backwards-compat aliases (deprecati, da rimuovere in prossima major release)
# ---------------------------------------------------------------------------
# Mantenuti per non rompere import esterni o altri tool che chiamano i vecchi
# nomi. I nomi nuovi (``perplexity_*_validate_full``) sono più onesti sul
# destinatario reale del prompt.

claude_stock_validate_fallback = perplexity_stock_validate_full
claude_contrarian_validate_fallback = perplexity_contrarian_validate_full
claude_etf_validate_fallback = perplexity_etf_validate_full
