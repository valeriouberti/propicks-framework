"""PDF weekly review report — print-friendly aggregate view.

Combina dati portfolio + journal in singolo PDF stampabile/condivisibile.
Sezioni:
1. Cover: data + portfolio value + cash% + posizioni count
2. Performance: realized P&L recente + win rate + best/worst trade settimana
3. Bucket allocation: stock/ETF/cash %
4. Sector concentration: tabella + top 3
5. Open positions: tabella per ticker con P&L unrealized
6. Recent closings: ultimi 10 trade chiusi
7. AI accuracy: se manual_ai_verdicts disponibili

Output: bytes + saved file `reports/pdf/weekly_review_YYYY-MM-DD.pdf`.

Usa reportlab (pure Python, no compile). Niente plot embed (text + tables only,
print-friendly compatto).
"""

from __future__ import annotations

import io
import os
import statistics
from datetime import date, datetime
from typing import Any

from propicks.config import (
    DATE_FMT,
    ETF_MAX_AGGREGATE_EXPOSURE_PCT,
    MAX_POSITIONS,
    MIN_CASH_RESERVE_PCT,
    REPORTS_DIR,
    STOCK_MAX_AGGREGATE_EXPOSURE_PCT,
)


def _build_data() -> dict[str, Any]:
    """Pull all data needed per PDF. Lazy import per dependency hygiene."""
    from propicks.domain.etf_universe import resolve_sector_key
    from propicks.domain.exposure import compute_sector_exposure
    from propicks.domain.sizing import (
        etf_aggregate_exposure,
        portfolio_market_value,
        portfolio_value,
        stock_aggregate_exposure,
    )
    from propicks.io.journal_store import load_journal
    from propicks.io.portfolio_store import load_portfolio
    from propicks.io.manual_verdicts_store import compute_accuracy
    from propicks.market.yfinance_client import (
        get_current_prices,
        get_ticker_sector,
    )

    portfolio = load_portfolio()
    positions = portfolio.get("positions", {})
    cash = float(portfolio.get("cash") or 0)
    total = portfolio_value(portfolio)

    tickers = sorted(positions.keys())
    prices: dict[str, float] = {}
    if tickers:
        try:
            prices = get_current_prices(tickers)
        except Exception:
            prices = {}

    total_market = portfolio_market_value(portfolio, prices) if prices else total

    # Bucket aggregates
    stock_pct = stock_aggregate_exposure(portfolio) * 100 if total > 0 else 0
    etf_pct = etf_aggregate_exposure(portfolio) * 100 if total > 0 else 0
    cash_pct = cash / total * 100 if total > 0 else 0

    # Sector exposure
    sector_yf = {t: get_ticker_sector(t) for t in tickers} if tickers else {}
    sector_map = {
        t: resolve_sector_key(t, yahoo_sector_raw=s) for t, s in sector_yf.items()
    }
    sector_exp = (
        compute_sector_exposure(positions, prices, sector_map, total_market)
        if positions else {}
    )

    # Per-position details
    pos_rows = []
    unrealized_total = 0.0
    for tk, p in sorted(positions.items()):
        cur = prices.get(tk)
        entry = float(p.get("entry_price", 0))
        shares = float(p.get("shares") or 0)
        mv = (cur or entry) * shares
        pnl = (cur - entry) * shares if cur else 0
        pnl_pct = (cur - entry) / entry if cur and entry > 0 else 0
        unrealized_total += pnl
        pos_rows.append({
            "ticker": tk,
            "shares": shares,
            "entry": entry,
            "current": cur or entry,
            "mv": mv,
            "pnl_eur": pnl,
            "pnl_pct": pnl_pct * 100,
            "stop": float(p.get("stop_loss") or 0),
            "target": float(p.get("target") or 0) if p.get("target") else None,
            "strategy": p.get("strategy") or "—",
            "sector": sector_map.get(tk) or "unknown",
        })

    # Journal — last 7 days closings
    journal = load_journal()
    closed = [t for t in journal if t.get("status") == "closed"]
    closed.sort(key=lambda t: t.get("exit_date") or "", reverse=True)

    # Last week closings
    today_d = date.today()
    week_closings = []
    for t in closed:
        ed = t.get("exit_date")
        if not ed:
            continue
        try:
            ed_dt = datetime.strptime(ed, DATE_FMT).date()
        except (ValueError, TypeError):
            continue
        if (today_d - ed_dt).days <= 7:
            week_closings.append(t)

    # Stats settimana
    week_pnls = [t["pnl_pct"] for t in week_closings if t.get("pnl_pct") is not None]
    week_wins = [p for p in week_pnls if p > 0]
    week_losses = [p for p in week_pnls if p <= 0]

    # Last 10 closings (any time)
    recent_closings = closed[:10]

    # AI accuracy
    accuracy = compute_accuracy()

    return {
        "as_of": today_d.isoformat(),
        "portfolio_value": total + unrealized_total,
        "cost_basis": total,
        "cash": cash,
        "cash_pct": cash_pct,
        "stock_pct": stock_pct,
        "etf_pct": etf_pct,
        "n_positions": len(positions),
        "max_positions": MAX_POSITIONS,
        "unrealized_eur": unrealized_total,
        "unrealized_pct": (
            unrealized_total / (total - cash) * 100
            if (total - cash) > 0 else 0
        ),
        "positions": pos_rows,
        "sector_exp": sector_exp,
        "week_closings": week_closings,
        "week_pnls": week_pnls,
        "week_wins": week_wins,
        "week_losses": week_losses,
        "week_avg_pnl": statistics.mean(week_pnls) if week_pnls else 0,
        "week_win_rate": (len(week_wins) / len(week_pnls)) if week_pnls else 0,
        "recent_closings": recent_closings,
        "accuracy": accuracy,
    }


def generate_weekly_pdf() -> tuple[bytes, str]:
    """Build PDF using reportlab. Returns (bytes, saved_path)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak,
    )

    data = _build_data()

    # Output paths
    pdf_dir = os.path.join(REPORTS_DIR, "pdf")
    os.makedirs(pdf_dir, exist_ok=True)
    fname = f"weekly_review_{data['as_of']}.pdf"
    fpath = os.path.join(pdf_dir, fname)

    # In-memory buffer per st.download_button
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=2 * cm, leftMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "Title2", parent=styles["Title"], fontSize=20, spaceAfter=14,
    )
    h2 = ParagraphStyle(
        "H2custom", parent=styles["Heading2"], fontSize=13,
        spaceBefore=12, spaceAfter=6, textColor=colors.HexColor("#1e40af"),
    )
    body = styles["BodyText"]
    small = ParagraphStyle("Small", parent=body, fontSize=9, leading=11)
    caption = ParagraphStyle(
        "Caption", parent=body, fontSize=8, leading=10,
        textColor=colors.HexColor("#64748b"),
    )

    story: list = []

    # Cover header
    story.append(Paragraph(
        f"📊 Propicks Weekly Review — {data['as_of']}", title_style,
    ))
    story.append(Paragraph(
        f"Portfolio snapshot · {data['n_positions']}/{data['max_positions']} posizioni aperte",
        caption,
    ))
    story.append(Spacer(1, 0.3 * cm))

    # KPI block
    kpi_rows = [
        ["Portfolio value (mtm)", f"€ {data['portfolio_value']:,.2f}"],
        ["Cost basis", f"€ {data['cost_basis']:,.2f}"],
        ["Unrealized P&L", f"€ {data['unrealized_eur']:+,.2f} ({data['unrealized_pct']:+.2f}%)"],
        ["Cash", f"€ {data['cash']:,.2f} ({data['cash_pct']:.1f}%)"],
        ["Bucket Stock", f"{data['stock_pct']:.1f}% / {STOCK_MAX_AGGREGATE_EXPOSURE_PCT * 100:.0f}%"],
        ["Bucket ETF", f"{data['etf_pct']:.1f}% / {ETF_MAX_AGGREGATE_EXPOSURE_PCT * 100:.0f}%"],
        ["Min cash reserve", f"{MIN_CASH_RESERVE_PCT * 100:.0f}% target"],
    ]
    t = Table(kpi_rows, colWidths=[6 * cm, 8 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.4 * cm))

    # Performance settimana
    story.append(Paragraph("📈 Performance settimanale (closed trades)", h2))
    if not data["week_pnls"]:
        story.append(Paragraph(
            "Nessun trade chiuso nell'ultima settimana.", body,
        ))
    else:
        week_rows = [
            ["Trade chiusi 7gg", str(len(data["week_pnls"]))],
            ["Win rate", f"{data['week_win_rate'] * 100:.1f}%"],
            ["Avg P&L", f"{data['week_avg_pnl']:+.2f}%"],
            ["Best", f"{max(data['week_pnls']):+.2f}%"],
            ["Worst", f"{min(data['week_pnls']):+.2f}%"],
        ]
        wt = Table(week_rows, colWidths=[6 * cm, 8 * cm])
        wt.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
        ]))
        story.append(wt)

    # Sector concentration
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph("🏢 Sector concentration", h2))
    if data["sector_exp"]:
        sec_data = [["Sector", "% Capitale"]]
        for sec, pct in sorted(data["sector_exp"].items(), key=lambda x: x[1], reverse=True):
            sec_data.append([sec, f"{pct * 100:.2f}%"])
        st_table = Table(sec_data, colWidths=[6 * cm, 4 * cm])
        st_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e40af")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
            ("ALIGN", (1, 1), (1, -1), "RIGHT"),
        ]))
        story.append(st_table)
    else:
        story.append(Paragraph("Nessuna posizione aperta per sector breakdown.", body))

    story.append(PageBreak())

    # Open positions
    story.append(Paragraph("📂 Posizioni aperte", h2))
    if not data["positions"]:
        story.append(Paragraph("Nessuna posizione aperta.", body))
    else:
        pos_data = [["Ticker", "Strategy", "Shares", "Entry", "Current", "P&L %", "Sector"]]
        for r in data["positions"]:
            pos_data.append([
                r["ticker"],
                r["strategy"][:18],
                f"{int(r['shares'])}" if r['shares'] == int(r['shares']) else f"{r['shares']:.2f}",
                f"{r['entry']:.2f}",
                f"{r['current']:.2f}",
                f"{r['pnl_pct']:+.2f}%",
                r["sector"][:14],
            ])
        pt = Table(pos_data, colWidths=[
            2.2 * cm, 3 * cm, 1.5 * cm, 1.8 * cm, 1.8 * cm, 2 * cm, 2.5 * cm,
        ])
        # Color rows by P&L sign
        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e40af")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
            ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ]
        for i, r in enumerate(data["positions"], 1):
            color = colors.HexColor("#dcfce7") if r["pnl_pct"] >= 0 else colors.HexColor("#fee2e2")
            style_cmds.append(("BACKGROUND", (5, i), (5, i), color))
        pt.setStyle(TableStyle(style_cmds))
        story.append(pt)

    # Recent closings
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph("💰 Ultime chiusure (top 10)", h2))
    if not data["recent_closings"]:
        story.append(Paragraph("Nessun trade chiuso nel journal.", body))
    else:
        c_data = [["Date", "Ticker", "Strategy", "Days", "P&L %", "Reason"]]
        for t in data["recent_closings"]:
            c_data.append([
                t.get("exit_date", "—"),
                t.get("ticker", "—"),
                (t.get("strategy") or "—")[:14],
                str(t.get("duration_days") or "—"),
                f"{t.get('pnl_pct', 0):+.2f}%",
                (t.get("exit_reason") or "—")[:25],
            ])
        ct = Table(c_data, colWidths=[
            2.5 * cm, 2 * cm, 2.5 * cm, 1.5 * cm, 2 * cm, 4 * cm,
        ])
        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e40af")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
            ("ALIGN", (3, 1), (4, -1), "RIGHT"),
        ]
        for i, t in enumerate(data["recent_closings"], 1):
            pnl = t.get("pnl_pct", 0) or 0
            color = colors.HexColor("#dcfce7") if pnl >= 0 else colors.HexColor("#fee2e2")
            style_cmds.append(("BACKGROUND", (4, i), (4, i), color))
        ct.setStyle(TableStyle(style_cmds))
        story.append(ct)

    # AI accuracy section
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph("🤖 AI accuracy (manual paste)", h2))
    acc = data["accuracy"]
    if acc["n_total"] == 0:
        story.append(Paragraph(
            "Nessun verdict manuale linkato a trade chiusi.",
            small,
        ))
    else:
        acc_rows = [
            ["Metric", "Value"],
            ["N directional (CONFIRM+REJECT)", str(acc["n_directional"])],
            ["N CAUTION (skipped)", str(acc["n_caution"])],
            [
                "Accuracy",
                f"{acc['accuracy'] * 100:.1f}%" if acc["accuracy"] is not None else "—",
            ],
            [
                "Brier score",
                f"{acc['brier_score']:.3f}" if acc["brier_score"] is not None else "—",
            ],
            ["Confusion: CONFIRM+WIN", str(acc["n_confirm_win"])],
            ["Confusion: CONFIRM+LOSS", str(acc["n_confirm_loss"])],
            ["Confusion: REJECT+LOSS", str(acc["n_reject_loss"])],
            ["Confusion: REJECT+WIN", str(acc["n_reject_win"])],
        ]
        at = Table(acc_rows, colWidths=[7 * cm, 4 * cm])
        at.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e40af")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
            ("ALIGN", (1, 1), (1, -1), "RIGHT"),
        ]))
        story.append(at)

    # Footer
    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph(
        f"Generato {datetime.now().strftime('%Y-%m-%d %H:%M')} · Propicks Trading Engine · "
        "data live yfinance + Anthropic Claude verdict cache",
        caption,
    ))

    # Build
    doc.build(story)
    pdf_bytes = buf.getvalue()
    buf.close()

    # Save to disk
    with open(fpath, "wb") as f:
        f.write(pdf_bytes)

    return pdf_bytes, fpath
