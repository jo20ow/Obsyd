"""Power-zone registry for the ENERGY vertical.

POWER_ZONES maps zone key → metadata. EIC codes are sourced from
backend.gas.entsoe.EU27_BIDDING_ZONES — do NOT invent or duplicate them.

Supported zones:
  DE_LU — German-Luxembourg bidding zone (lead zone, default)
  FR    — France (RTE)
  NL    — Netherlands (TenneT NL)

Out of scope (left DE-only, intentional):
  SparkSpreadHistory — no zone column; always uses POWER_DE (DE-LU) price.
  Scorecard signals (power_residual, spark_spread) — target-aware but zoned
    at DE-LU only; extending them is a future sprint.

Cross-border flows:
  Previously sourced from ENTSO-E A11 (entsoe_flows.py) with a hardcoded
  POWER_BORDERS list.  Since 2026-06-24 the source is Fraunhofer Energy-Charts
  /cbpf (energy_charts_flows.py, CC BY 4.0).  The border set is now derived
  from the live API response — POWER_BORDERS is kept only for the /flows route
  summary query and now reflects the real borders of DE-LU/FR/NL.  The
  fictitious FR↔NL border (no real interconnector exists) has been removed.

  Zones for neighbouring countries (BE, CH, AT, etc.) are not in POWER_ZONES
  because we don't collect their day-ahead prices or grid data; they appear
  only as flow endpoints in PowerFlow rows.
"""

from __future__ import annotations

# EIC codes from backend.gas.entsoe.EU27_BIDDING_ZONES (verified against that map):
#   "DE-LU": "10Y1001A1001A82H"
#   "FR":    "10YFR-RTE------C"
#   "NL":    "10YNL----------L"

POWER_ZONES: dict[str, dict] = {
    "DE_LU": {
        "eic": "10Y1001A1001A82H",
        "price_symbol": "POWER_DE",
        "label": "DE-LU",
    },
    "FR": {
        "eic": "10YFR-RTE------C",
        "price_symbol": "POWER_FR",
        "label": "FR",
    },
    "NL": {
        "eic": "10YNL----------L",
        "price_symbol": "POWER_NL",
        "label": "NL",
    },
}

DEFAULT_ZONE = "DE_LU"

# Real cross-border pairs for the /flows route summary.
# Source: Energy-Charts /cbpf (CC BY 4.0).  Borders are all REAL interconnectors
# of DE-LU, FR, and NL — the fictitious FR↔NL border (no physical interconnector)
# has been removed.
#
# Note: many more borders are stored in the PowerFlow table (e.g. DE_LU↔BE,
# DE_LU↔AT, FR↔ES, etc.) — this list is only used by the /flows route to
# enumerate the "primary" borders for the borders[] summary.  All borders
# present in the PowerFlow table are also returned in the data/latest fields.
POWER_BORDERS: list[tuple[str, str]] = [
    ("BE", "DE_LU"),
    ("BE", "NL"),
    ("CH", "DE_LU"),
    ("CH", "FR"),
    ("DE_LU", "FR"),
    ("DE_LU", "NL"),
    ("FR", "GB"),
]
