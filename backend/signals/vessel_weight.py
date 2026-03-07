"""
Vessel Weight — Ship Class Heuristic for Weighted Vessel Counting.

AIS ship_type ranges for tankers: 80-89
Sub-classification uses ship_type codes + name-based heuristics
to estimate vessel class when LOA/DWT are unavailable.

Weight factors reflect approximate cargo capacity relative to Aframax:
  VLCC     ~2M bbl   → 3.0x
  Suezmax  ~1M bbl   → 2.0x
  Aframax  ~700k bbl → 1.0x  (baseline)
  Product  ~350k bbl → 0.5x
  Other    unknown   → 0.5x
"""

from __future__ import annotations

# Name prefixes/substrings commonly associated with VLCC operators/naming
VLCC_PATTERNS = [
    "FRONT ", "DHT ", "EURONAV", "VLCC", "HUNTER", "EAGLE",
    "RIDGEBURY", "NEW FORTUNE", "NEW PROSPERITY", "OLYMPIC",
    "NISSOS", "MARAN ", "GENER8", "NAVE ",
]

# Name patterns for Suezmax-class vessels
SUEZMAX_PATTERNS = [
    "SUEZMAX", "NORDIC ", "MINERVA ", "OKEANIS", "ALFA ",
    "STEALTH ", "ELANDRA",
]


def classify_vessel(ship_name: str, ship_type: int) -> tuple[str, float]:
    """Classify a vessel by AIS ship_type and name heuristics.

    Returns:
        (class_name, weight_factor) — e.g. ("VLCC", 3.0)
    """
    # Not a tanker — weight zero
    if not (80 <= ship_type <= 89):
        return ("non-tanker", 0.0)

    name_upper = (ship_name or "").upper()

    # VLCC detection (ship_type 84 or name match)
    if ship_type == 84 or any(p in name_upper for p in VLCC_PATTERNS):
        return ("VLCC", 3.0)

    # Suezmax detection (ship_type 83 or name match)
    if ship_type == 83 or any(p in name_upper for p in SUEZMAX_PATTERNS):
        return ("Suezmax", 2.0)

    # Aframax (ship_type 82)
    if ship_type == 82:
        return ("Aframax", 1.0)

    # Product tanker (ship_type 81)
    if ship_type == 81:
        return ("Product", 0.5)

    # Catch-all tanker (ship_type 80, 85-89)
    return ("Tanker", 0.5)


def compute_weighted_count(vessels: list[dict]) -> dict:
    """Compute weighted vessel count from a list of vessel dicts.

    Each vessel dict must have at least:
      - ship_name (str)
      - ship_type (int)

    Returns:
        {
            "raw_count": int,
            "weighted_count": float,
            "by_class": {"VLCC": n, "Suezmax": n, "Aframax": n, "Product": n, "Tanker": n},
        }
    """
    by_class: dict[str, int] = {}
    weighted = 0.0
    raw = 0

    for v in vessels:
        ship_name = v.get("ship_name", "")
        ship_type = v.get("ship_type", 0)

        cls_name, weight = classify_vessel(ship_name, ship_type)

        if cls_name == "non-tanker":
            continue

        raw += 1
        weighted += weight
        by_class[cls_name] = by_class.get(cls_name, 0) + 1

    return {
        "raw_count": raw,
        "weighted_count": round(weighted, 1),
        "by_class": by_class,
    }
