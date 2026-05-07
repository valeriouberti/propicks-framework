"""Test sul mapping curato parent → sub-ETF (``domain/subetf_universe``).

Tutto offline — la mappa è hardcoded, nessuna rete.
"""

from __future__ import annotations

from propicks.domain.stock_rs import SECTOR_KEY_TO_US_ETF
from propicks.domain.subetf_universe import (
    PARENT_TO_SUB_ETFS,
    SUB_ETF_TO_PARENT,
    all_sub_etfs,
    parent_for_sub_etf,
    sub_etfs_for_parent,
)


# ---------------------------------------------------------------------------
# Coverage: ogni Select Sector SPDR ha sub-ETF mappati
# ---------------------------------------------------------------------------
def test_every_us_select_sector_etf_has_sub_etfs():
    """SECTOR_KEY_TO_US_ETF lista i parent XL* — ognuno deve avere sub-ETF."""
    parents = set(SECTOR_KEY_TO_US_ETF.values())
    mapped = set(PARENT_TO_SUB_ETFS.keys())
    assert parents == mapped, (
        f"Disallineamento parent set: SECTOR_KEY_TO_US_ETF={sorted(parents)} "
        f"vs PARENT_TO_SUB_ETFS={sorted(mapped)}"
    )


def test_each_parent_has_at_least_3_sub_etfs():
    """Curation gate: minimo 3 sub-ETF per parent (sotto = top-N rumore)."""
    for parent, subs in PARENT_TO_SUB_ETFS.items():
        assert len(subs) >= 3, f"{parent} ha solo {len(subs)} sub-ETF"


def test_no_duplicate_sub_etfs_across_parents():
    """Un sub-ETF non può essere mappato a 2 parent (viola sub-industry semantics)."""
    seen: dict[str, str] = {}
    for parent, subs in PARENT_TO_SUB_ETFS.items():
        for s in subs:
            assert s not in seen, f"{s} mappato sia a {seen[s]} sia a {parent}"
            seen[s] = parent


def test_sub_etfs_are_uppercase_strings():
    for parent, subs in PARENT_TO_SUB_ETFS.items():
        assert isinstance(parent, str) and parent.isupper()
        for s in subs:
            assert isinstance(s, str) and s.isupper() and s == s.strip()


# ---------------------------------------------------------------------------
# Reverse index
# ---------------------------------------------------------------------------
def test_reverse_index_consistent_with_forward():
    for parent, subs in PARENT_TO_SUB_ETFS.items():
        for s in subs:
            assert SUB_ETF_TO_PARENT[s] == parent


def test_reverse_index_size_equals_total_sub_etfs():
    total = sum(len(v) for v in PARENT_TO_SUB_ETFS.values())
    assert len(SUB_ETF_TO_PARENT) == total


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def test_sub_etfs_for_parent_returns_curated_list():
    out = sub_etfs_for_parent("XLK")
    assert "SOXX" in out
    assert "IGV" in out
    assert all(isinstance(t, str) for t in out)


def test_sub_etfs_for_parent_case_insensitive():
    upper = sub_etfs_for_parent("XLK")
    lower = sub_etfs_for_parent("xlk")
    mixed = sub_etfs_for_parent("Xlk")
    assert upper == lower == mixed


def test_sub_etfs_for_parent_unknown_returns_empty():
    assert sub_etfs_for_parent("UNKNOWN") == []
    assert sub_etfs_for_parent("") == []
    assert sub_etfs_for_parent(None) == []  # type: ignore[arg-type]


def test_sub_etfs_for_parent_returns_copy_not_reference():
    """Mutate result non deve modificare la mappa interna."""
    out = sub_etfs_for_parent("XLK")
    out.append("CONTAMINATED")
    assert "CONTAMINATED" not in PARENT_TO_SUB_ETFS["XLK"]


def test_parent_for_sub_etf_round_trip():
    assert parent_for_sub_etf("SOXX") == "XLK"
    assert parent_for_sub_etf("KRE") == "XLF"
    assert parent_for_sub_etf("IBB") == "XLV"


def test_parent_for_sub_etf_case_insensitive():
    assert parent_for_sub_etf("soxx") == "XLK"
    assert parent_for_sub_etf("SoXx") == "XLK"


def test_parent_for_sub_etf_unknown_returns_none():
    assert parent_for_sub_etf("FAKEETF") is None
    assert parent_for_sub_etf("") is None
    assert parent_for_sub_etf(None) is None  # type: ignore[arg-type]


def test_all_sub_etfs_returns_sorted_unique():
    out = all_sub_etfs()
    assert out == sorted(set(out))
    assert len(out) == len(SUB_ETF_TO_PARENT)
