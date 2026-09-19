from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.materials_18233 import kit_sources
from app.services.profile_18233 import parse_bundle, reconcile_18233


def _kit_paths(kit: str) -> list[Path]:
    src = kit_sources(kit)
    return [src["invoice"], src["packing"], src["specification"]]


def test_6261_yields_13_positions() -> None:
    bundle = parse_bundle(_kit_paths("626-1"))
    items = reconcile_18233(bundle)
    articles = [i for i in items if i.normalized_article and not i.normalized_article.startswith("SOFA")]
    # orphan PL-only groups should not inflate acceptance count
    product_items = [i for i in items if "invoice" in i.source_traces or "specification" in i.source_traces]
    assert len(product_items) == 13
    noble = next(i for i in product_items if i.article == "Noble 110")
    assert noble.packing_data.get("rolls") == 15
    assert abs(float(noble.packing_data.get("meters")) - 611.5) < 0.01
    assert abs(float(noble.packing_data.get("net_weight")) - 580.92) < 0.05


def test_6262_yields_17_positions() -> None:
    bundle = parse_bundle(_kit_paths("626-2"))
    items = reconcile_18233(bundle)
    product_items = [i for i in items if "invoice" in i.source_traces or "specification" in i.source_traces]
    assert len(product_items) == 17


def test_invoice_spec_rolls_match_for_melange() -> None:
    bundle = parse_bundle(_kit_paths("626-1"))
    items = reconcile_18233(bundle)
    melange = next(i for i in items if i.article == "Melange 928")
    reds = [f for f in melange.flags if f.severity.value == "RED" and f.field_name == "rolls"]
    assert not reds
