"""Test trade management: trailing stop e time stop."""

from __future__ import annotations

from datetime import date

from propicks.domain.trade_mgmt import (
    check_time_stop,
    compute_trailing_stop,
    suggest_stop_update,
)

# ---------------------------------------------------------------------------
# compute_trailing_stop
# ---------------------------------------------------------------------------


def test_trailing_stop_inactive_below_1r():
    """Sotto entry+1R lo stop iniziale resta invariato."""
    # entry 100, stop 90 → 1R = 10 → activation a 110. highest=105 < activation
    new_stop = compute_trailing_stop(
        entry_price=100.0,
        highest_price_since_entry=105.0,
        current_atr=2.0,
        current_stop=90.0,
    )
    assert new_stop == 90.0


def test_trailing_stop_activates_at_1r_and_ratchets_up():
    """Sopra entry+1R, lo stop si muove a highest - k*ATR."""
    new_stop = compute_trailing_stop(
        entry_price=100.0,
        highest_price_since_entry=120.0,
        current_atr=2.0,
        current_stop=90.0,
        atr_mult=2.0,
    )
    # proposed = 120 - 2*2 = 116, > current 90
    assert new_stop == 116.0


def test_trailing_stop_never_descends():
    """Se proposed < current_stop, lo stop resta fermo (ratchet-up only)."""
    # highest 115, atr_mult 5*5=25 → proposed=90, current=100 → resta 100
    new_stop = compute_trailing_stop(
        entry_price=100.0,
        highest_price_since_entry=115.0,
        current_atr=5.0,
        current_stop=100.0,
        atr_mult=5.0,
    )
    assert new_stop == 100.0


def test_trailing_stop_degenerate_inverted_stop():
    """Se stop iniziale >= entry (degenere) ritorna current_stop senza toccare."""
    new_stop = compute_trailing_stop(
        entry_price=100.0,
        highest_price_since_entry=120.0,
        current_atr=2.0,
        current_stop=105.0,  # già sopra entry
    )
    assert new_stop == 105.0


# ---------------------------------------------------------------------------
# check_time_stop
# ---------------------------------------------------------------------------


def test_time_stop_triggers_on_flat_old_trade():
    """Trade vecchio 40gg con P&L -1% (sotto soglia 2%) → True."""
    triggered = check_time_stop(
        entry_date_str="2026-01-01",
        entry_price=100.0,
        current_date=date(2026, 2, 10),  # 40 gg
        current_price=99.0,  # -1%
        max_days_flat=30,
        flat_threshold_pct=0.02,
    )
    assert triggered is True


def test_time_stop_skips_winner_even_if_old():
    """Trade vecchio ma in guadagno >2% → False (lascia correre)."""
    triggered = check_time_stop(
        entry_date_str="2026-01-01",
        entry_price=100.0,
        current_date=date(2026, 2, 10),
        current_price=108.0,  # +8%
    )
    assert triggered is False


def test_time_stop_skips_recent_trade():
    """Trade < max_days non scatta a prescindere dal P&L."""
    triggered = check_time_stop(
        entry_date_str="2026-01-01",
        entry_price=100.0,
        current_date=date(2026, 1, 15),  # 14 gg
        current_price=99.5,
        max_days_flat=30,
    )
    assert triggered is False


def test_time_stop_invalid_date_returns_false():
    """Date malformate non crashano: ritornano False (no signal spurious)."""
    triggered = check_time_stop(
        entry_date_str="not-a-date",
        entry_price=100.0,
        current_date=date(2026, 2, 10),
        current_price=99.0,
    )
    assert triggered is False


# ---------------------------------------------------------------------------
# suggest_stop_update (orchestrazione)
# ---------------------------------------------------------------------------


def _base_position(**overrides) -> dict:
    pos = {
        "entry_price": 100.0,
        "entry_date": "2026-01-01",
        "shares": 10,
        "stop_loss": 90.0,
        "target": 120.0,
        "trailing_enabled": False,
    }
    pos.update(overrides)
    return pos


def test_suggest_no_change_when_trailing_disabled():
    pos = _base_position()
    result = suggest_stop_update(
        position=pos,
        current_price=120.0,
        current_atr=2.0,
        current_date=date(2026, 1, 20),
    )
    assert result["new_stop"] is None
    assert result["stop_changed"] is False
    assert result["highest_price"] == 120.0  # comunque tracciato


def test_suggest_trailing_active_proposes_new_stop():
    pos = _base_position(trailing_enabled=True, highest_price_since_entry=115.0)
    result = suggest_stop_update(
        position=pos,
        current_price=120.0,  # nuovo highest = 120
        current_atr=2.0,
        current_date=date(2026, 1, 20),
        atr_mult=2.0,
    )
    # proposed = 120 - 2*2 = 116, > current 90
    assert result["stop_changed"] is True
    assert result["new_stop"] == 116.0
    assert result["highest_price"] == 120.0
    assert any("Trailing" in r for r in result["rationale"])


def test_suggest_time_stop_triggered():
    pos = _base_position(entry_date="2026-01-01")
    result = suggest_stop_update(
        position=pos,
        current_price=99.5,  # -0.5% flat
        current_atr=2.0,
        current_date=date(2026, 2, 15),  # 45 gg
        max_days_flat=30,
    )
    assert result["time_stop_triggered"] is True
    assert any("Time stop" in r for r in result["rationale"])


def test_suggest_initializes_highest_when_missing():
    """Se highest_price_since_entry manca, viene inizializzato."""
    pos = _base_position()  # no highest_price_since_entry
    pos.pop("trailing_enabled", None)
    result = suggest_stop_update(
        position=pos,
        current_price=110.0,
        current_atr=2.0,
        current_date=date(2026, 1, 10),
    )
    assert result["highest_price"] == 110.0  # max(entry=100, current=110)


def test_suggest_atr_zero_skips_trailing():
    """ATR=0 (degenere) non deve crashare né muovere lo stop."""
    pos = _base_position(trailing_enabled=True)
    result = suggest_stop_update(
        position=pos,
        current_price=120.0,
        current_atr=0.0,
        current_date=date(2026, 1, 20),
    )
    assert result["stop_changed"] is False


def test_suggest_default_current_date():
    """current_date=None usa date.today() senza errori."""
    pos = _base_position()
    result = suggest_stop_update(
        position=pos,
        current_price=110.0,
        current_atr=2.0,
        current_date=None,
    )
    assert "highest_price" in result


# ---------------------------------------------------------------------------
# Target hit detection + dynamic target (Phase contrarian fix)
# ---------------------------------------------------------------------------
def test_target_hit_triggered_when_price_reaches_target():
    """Posizione con target=120 e prezzo 121 → target_hit_triggered=True."""
    pos = _base_position(target=120.0)
    result = suggest_stop_update(
        position=pos,
        current_price=121.0,
        current_atr=2.0,
        current_date=date(2026, 1, 10),
    )
    assert result["target_hit_triggered"] is True
    assert any("Target hit" in r for r in result["rationale"])


def test_target_hit_skipped_when_below_target():
    """Prezzo sotto target → target_hit_triggered=False."""
    pos = _base_position(target=120.0)
    result = suggest_stop_update(
        position=pos,
        current_price=115.0,
        current_atr=2.0,
        current_date=date(2026, 1, 10),
    )
    assert result["target_hit_triggered"] is False


def test_target_skipped_when_trailing_active():
    """Trailing enabled → target hit non valutato (il trailing manage il TP)."""
    pos = _base_position(target=120.0, trailing_enabled=True)
    result = suggest_stop_update(
        position=pos,
        current_price=125.0,
        current_atr=2.0,
        current_date=date(2026, 1, 10),
    )
    assert result["target_hit_triggered"] is False


def test_dynamic_target_updates_for_contrarian():
    """Posizione contrarian: dynamic_target sostituisce target statico (drift > 0.5%)."""
    pos = _base_position(target=120.0, strategy="Contrarian")
    result = suggest_stop_update(
        position=pos,
        current_price=110.0,
        current_atr=2.0,
        current_date=date(2026, 1, 10),
        dynamic_target=125.0,  # EMA50 corrente è salito
    )
    assert result["target_changed"] is True
    assert result["new_target"] == 125.0
    assert any("Target dinamico" in r for r in result["rationale"])


def test_dynamic_target_ignored_for_momentum():
    """Posizione non-contrarian: dynamic_target non applicato anche se passato."""
    pos = _base_position(target=120.0, strategy="TechTitans")
    result = suggest_stop_update(
        position=pos,
        current_price=110.0,
        current_atr=2.0,
        current_date=date(2026, 1, 10),
        dynamic_target=125.0,
    )
    assert result["target_changed"] is False
    assert result["new_target"] is None


def test_dynamic_target_jitter_below_threshold_ignored():
    """Drift dinamico < 0.5% non triggera update (anti-jitter)."""
    pos = _base_position(target=120.0, strategy="Contrarian")
    result = suggest_stop_update(
        position=pos,
        current_price=110.0,
        current_atr=2.0,
        current_date=date(2026, 1, 10),
        dynamic_target=120.30,  # +0.25% drift, sotto 0.5% threshold
    )
    assert result["target_changed"] is False


def test_contrarian_uses_15d_time_stop_by_default():
    """Posizione contrarian con default max_days_flat (30) → effettivo 15gg."""
    pos = _base_position(entry_date="2026-01-01", strategy="Contrarian")
    # 16gg flat: scatta per contrarian (15), non per momentum (30)
    result = suggest_stop_update(
        position=pos,
        current_price=99.5,  # flat
        current_atr=2.0,
        current_date=date(2026, 1, 17),
    )
    assert result["time_stop_triggered"] is True


def test_momentum_uses_30d_time_stop_by_default():
    """Posizione momentum con stesso scenario → time stop NON scatta a 16gg."""
    pos = _base_position(entry_date="2026-01-01", strategy="TechTitans")
    result = suggest_stop_update(
        position=pos,
        current_price=99.5,
        current_atr=2.0,
        current_date=date(2026, 1, 17),
    )
    assert result["time_stop_triggered"] is False


def test_explicit_max_days_flat_overrides_bucket_default():
    """Caller passa max_days_flat=20 esplicitamente → usato anche per contrarian."""
    pos = _base_position(entry_date="2026-01-01", strategy="Contrarian")
    result = suggest_stop_update(
        position=pos,
        current_price=99.5,
        current_atr=2.0,
        current_date=date(2026, 1, 17),  # 16gg
        max_days_flat=20,  # override esplicito
    )
    # 16gg < 20 → no trigger
    assert result["time_stop_triggered"] is False
