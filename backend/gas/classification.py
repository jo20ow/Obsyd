"""Classify ENTSOG registry points into flow classes by counterparty.

Data-driven (keys off structural registry fields, not point IDs, so it
survives operator renames) with a small documented override list for points
the structural rules miss. Verified against a live registry sample
(backend/tests/fixtures/gas/entsog_pointdirections.json).

Classes:
  import_pipeline   — EU entry from a non-EU pipeline supplier (NO/DZ/TN/LY/AZ/TR/RS...)
  interconnector_uk — Bacton IUK + BBL + Zeebrugge IZT (adjacent UK), bidirectional → net
  export_ua         — EU exit toward Ukraine
  lng_entry         — LNG terminal entry (VALIDATION ONLY; ALSI is the canonical LNG source)
  production_entry  — domestic EU production (best-effort; off the Bruegel-imports critical path)
  None              — out of scope (in-country transit, EU-EU interconnectors, non-EU/non-EU)

Reality notes from the live registry:
  - A physical import point is reported by BOTH the EU TSO (tSOCountry∈EU,
    adjacentCountry=supplier) and the supplier's operator (tSOCountry=supplier).
    We keep only the EU-side row (tSOCountry∈EU); the supplier-side is dropped.
  - TAP's registered zone is "CH"; gas there is Azerbaijani. adj=CH at a TAP
    point → counterparty Azerbaijan.
  - Algeria arrives via Transmed (adj=TN, Tunisia) and via Medgaz (Almería),
    the latter modeled as In-country EU → caught by the override list.
"""

from __future__ import annotations

from dataclasses import dataclass

EU27 = frozenset(
    {
        "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
        "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
        "SI", "ES", "SE",
    }
)

# Non-EU pipeline suppliers, by ENTSOG adjacentCountry code → readable counterparty.
SUPPLIER_LABELS = {
    "NO": "Norway",
    "DZ": "Algeria",
    "TN": "Algeria (Transmed/TN)",
    "LY": "Libya",
    "AZ": "Azerbaijan",
    "CH": "Azerbaijan (TAP/CH)",  # TAP's registered zone is CH
    "TR": "Turkey (TurkStream)",
    "RU": "Russia",
    "RS": "Serbia",
}

UK_CODES = frozenset({"UK", "GB"})

# Points the structural rules miss, by a substring of pointLabel (case-insensitive).
# Each maps to (class, counterparty). Keep this list short and documented.
NAME_OVERRIDES = (
    ("almería", ("import_pipeline", "Algeria")),       # Medgaz, modeled In-country EU
    ("almeria", ("import_pipeline", "Algeria")),
    ("medgaz", ("import_pipeline", "Algeria")),
)


@dataclass(frozen=True)
class PointClass:
    point_class: str
    counterparty: str


def _xb(row: dict) -> str:
    return (row.get("crossBorderPointType") or "").strip()


def classify_point(row: dict) -> PointClass | None:
    """Return the class+counterparty for an ENTSOG operatorpointdirections row,
    or None if the point is out of scope for the EU27 supply balance."""
    direction = (row.get("directionKey") or "").lower()
    tso = (row.get("tSOCountry") or "").upper()
    adj = (row.get("adjacentCountry") or "").upper()
    label = (row.get("pointLabel") or "")
    label_l = label.lower()
    xb = _xb(row)

    # 1. Named overrides (e.g. Medgaz/Almería) win — but only on the EU side.
    if tso in EU27:
        for needle, (cls, cp) in NAME_OVERRIDES:
            if needle in label_l:
                return PointClass(cls, cp)

    # Everything below requires the EU side of the point.
    if tso not in EU27:
        return None

    # 2. UK interconnectors (Bacton IUK/BBL, Zeebrugge IZT) — both directions.
    if adj in UK_CODES:
        return PointClass("interconnector_uk", "United Kingdom")

    # 3. EU → Ukraine export.
    if adj == "UA" and direction == "exit":
        return PointClass("export_ua", "Ukraine")

    # 4. LNG terminal entries (validation only; ALSI is canonical). Detect by
    #    label, since LNG terminals are modeled as In-country entries.
    if "lng" in label_l and direction == "entry":
        return PointClass("lng_entry", "LNG terminal")

    # 5. Domestic production: ENTSOG has a dedicated point type for it
    #    ("Aggregated production point - TP"; the ExtEU variant is already
    #    excluded by the EU-side guard above).
    if direction == "entry" and (row.get("pointType") or "").startswith("Aggregated production point"):
        return PointClass("production_entry", f"Domestic {tso}")

    # 6. Pipeline imports: EU entry from a non-EU supplier on a Non-EU border.
    if direction == "entry" and "Non-EU" in xb and adj in SUPPLIER_LABELS:
        return PointClass("import_pipeline", SUPPLIER_LABELS[adj])

    # 7. Out of scope (in-country transit, EU-EU interconnectors).
    return None
