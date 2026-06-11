"""Classification tests — synthetic rows mirroring the live ENTSOG registry
(field values captured from operatorpointdirections)."""

from __future__ import annotations

from backend.gas.classification import classify_point


def _row(**kw):
    base = {
        "operatorKey": "X-TSO-0001",
        "pointKey": "ITP-00000",
        "pointLabel": "",
        "operatorLabel": "",
        "directionKey": "entry",
        "tSOCountry": "DE",
        "adjacentCountry": "NO",
        "crossBorderPointType": "Cross-Border EU|Non-EU",
    }
    base.update(kw)
    return base


def test_norway_entry_is_import():
    c = classify_point(_row(pointLabel="Emden (EPT1) (OGE)", tSOCountry="DE", adjacentCountry="NO"))
    assert c and c.point_class == "import_pipeline" and c.counterparty == "Norway"


def test_libya_gela_is_import():
    c = classify_point(_row(pointLabel="Gela", tSOCountry="IT", adjacentCountry="LY"))
    assert c and c.point_class == "import_pipeline" and c.counterparty == "Libya"


def test_turkstream_strandzha_is_import():
    c = classify_point(_row(pointLabel="Strandzha 2 (BG) / Malkoclar (TR)", tSOCountry="BG", adjacentCountry="TR"))
    assert c and c.point_class == "import_pipeline" and "Turkey" in c.counterparty


def test_transmed_mazara_is_import_via_tn():
    c = classify_point(_row(pointLabel="Mazara del Vallo", tSOCountry="IT", adjacentCountry="TN"))
    assert c and c.point_class == "import_pipeline" and "Algeria" in c.counterparty


def test_tap_melendugno_ch_is_azerbaijan():
    c = classify_point(_row(pointLabel="Melendugno - IT / TAP", operatorLabel="Snam Rete Gas", tSOCountry="IT", adjacentCountry="CH"))
    assert c and c.point_class == "import_pipeline" and "Azerbaijan" in c.counterparty


def test_medgaz_almeria_override_is_algeria():
    # Modeled In-country EU, so the structural rule misses it — override catches it.
    c = classify_point(_row(pointLabel="Almería", operatorLabel="Enagas", tSOCountry="ES", adjacentCountry="ES", crossBorderPointType="In-country EU"))
    assert c and c.point_class == "import_pipeline" and c.counterparty == "Algeria"


def test_supplier_side_row_is_dropped():
    # Norway's own operator (Gassco) reporting tSOCountry=NO must NOT be an import —
    # otherwise the point is double-counted.
    c = classify_point(_row(pointLabel="Emden (EPT1)", operatorLabel="Gassco", tSOCountry="NO", adjacentCountry="DE"))
    assert c is None


def test_bacton_bbl_is_uk_interconnector():
    c = classify_point(_row(pointLabel="Bacton (BBL)", tSOCountry="NL", adjacentCountry="UK", crossBorderPointType="Cross-Border EU|EU", directionKey="entry"))
    assert c and c.point_class == "interconnector_uk" and c.counterparty == "United Kingdom"


def test_eu_to_ukraine_export():
    c = classify_point(_row(pointLabel="Velke Kapusany", tSOCountry="SK", adjacentCountry="UA", crossBorderPointType="Cross-Border EU|Non-EU", directionKey="exit"))
    assert c and c.point_class == "export_ua" and c.counterparty == "Ukraine"


def test_lng_terminal_entry_is_lng_class():
    c = classify_point(_row(pointLabel="Zeebrugge LNG", tSOCountry="BE", adjacentCountry="BE", crossBorderPointType="In-country EU", directionKey="entry"))
    assert c and c.point_class == "lng_entry"


def test_domestic_production_via_point_type():
    c = classify_point(_row(pointLabel="Production (NL)", tSOCountry="NL", adjacentCountry="NL", crossBorderPointType="In-country EU", directionKey="entry", pointType="Aggregated production point - TP"))
    assert c and c.point_class == "production_entry" and c.counterparty == "Domestic NL"


def test_ext_eu_production_is_out_of_scope():
    # Non-EU production (ExtEU) must not be classified as EU domestic.
    c = classify_point(_row(pointLabel="Production (RS)", tSOCountry="RS", adjacentCountry="RS", crossBorderPointType="In-country Non-EU", directionKey="entry", pointType="Aggregated production point - TP ExtEU"))
    assert c is None


def test_in_country_transit_is_out_of_scope():
    c = classify_point(_row(pointLabel="Some VTP", tSOCountry="DE", adjacentCountry="DE", crossBorderPointType="In-country EU", directionKey="entry"))
    assert c is None


def test_eu_eu_interconnector_is_out_of_scope():
    c = classify_point(_row(pointLabel="Oltingue", tSOCountry="FR", adjacentCountry="DE", crossBorderPointType="Cross-Border EU|EU", directionKey="entry"))
    assert c is None
