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
