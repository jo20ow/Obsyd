"""Power-zone registry for the ENERGY vertical.

ZONE_REGISTRY is the single canonical map of zone key → metadata for every European
bidding zone we can ingest (EIC codes sourced from ENTSO-E). POWER_ZONES is the subset
that is currently ENABLED (from settings.enabled_zones) — the app serves exactly these.

Adding a zone is **config-only**: add its key to `ENABLED_ZONES` (settings.enabled_zones)
once the zone-parameterized ingest/flows/freshness/coverage are in place (roadmap Block 2).
Until then the default enables the three live zones (DE-LU/FR/NL), byte-identical to before.

Per-zone metadata:
  eic          — ENTSO-E Energy Identification Code (do NOT invent; from the registry)
  price_symbol — EnergyPrice symbol for the zone's day-ahead price (POWER_DE/FR/NL kept
                 for the original three; POWER_<KEY> otherwise)
  label        — human-readable zone label
  ec_country   — Fraunhofer Energy-Charts country code for /cbpf cross-border flows
                 (None where not yet mapped; completed with the flows generalization)

Cross-border flows: sourced from Fraunhofer Energy-Charts /cbpf (CC BY 4.0,
energy_charts_flows.py). POWER_BORDERS below is only the /flows route summary list.
"""

from __future__ import annotations

from backend.config import settings

# ── Canonical registry: every ingestable ENTSO-E bidding zone (EICs verified against
#    backend.gas.entsoe.EU27_BIDDING_ZONES). Keys are power-style (underscored). ──
ZONE_REGISTRY: dict[str, dict] = {
    "DE_LU": {"eic": "10Y1001A1001A82H", "price_symbol": "POWER_DE", "label": "DE-LU", "ec_country": "de"},
    "FR": {"eic": "10YFR-RTE------C", "price_symbol": "POWER_FR", "label": "FR", "ec_country": "fr"},
    "NL": {"eic": "10YNL----------L", "price_symbol": "POWER_NL", "label": "NL", "ec_country": "nl"},
    "BE": {"eic": "10YBE----------2", "price_symbol": "POWER_BE", "label": "BE", "ec_country": "be"},
    "AT": {"eic": "10YAT-APG------L", "price_symbol": "POWER_AT", "label": "AT", "ec_country": "at"},
    "ES": {"eic": "10YES-REE------0", "price_symbol": "POWER_ES", "label": "ES", "ec_country": "es"},
    "PT": {"eic": "10YPT-REN------W", "price_symbol": "POWER_PT", "label": "PT", "ec_country": "pt"},
    "PL": {"eic": "10YPL-AREA-----S", "price_symbol": "POWER_PL", "label": "PL", "ec_country": "pl"},
    "CZ": {"eic": "10YCZ-CEPS-----N", "price_symbol": "POWER_CZ", "label": "CZ", "ec_country": "cz"},
    "HU": {"eic": "10YHU-MAVIR----U", "price_symbol": "POWER_HU", "label": "HU", "ec_country": "hu"},
    "RO": {"eic": "10YRO-TEL------P", "price_symbol": "POWER_RO", "label": "RO", "ec_country": "ro"},
    "GR": {"eic": "10YGR-HTSO-----Y", "price_symbol": "POWER_GR", "label": "GR", "ec_country": "gr"},
    "IE_SEM": {"eic": "10Y1001A1001A59C", "price_symbol": "POWER_IE_SEM", "label": "IE-SEM", "ec_country": "ie"},
    "IT_NORD": {"eic": "10Y1001A1001A73I", "price_symbol": "POWER_IT_NORD", "label": "IT-Nord", "ec_country": None},
    "IT_CENTRO_NORD": {"eic": "10Y1001A1001A70O", "price_symbol": "POWER_IT_CNOR", "label": "IT-Centro-Nord", "ec_country": None},
    "IT_CENTRO_SUD": {"eic": "10Y1001A1001A71M", "price_symbol": "POWER_IT_CSUD", "label": "IT-Centro-Sud", "ec_country": None},
    "IT_SUD": {"eic": "10Y1001A1001A788", "price_symbol": "POWER_IT_SUD", "label": "IT-Sud", "ec_country": None},
    "IT_SICILIA": {"eic": "10Y1001A1001A75E", "price_symbol": "POWER_IT_SICI", "label": "IT-Sicilia", "ec_country": None},
    "IT_SARDEGNA": {"eic": "10Y1001A1001A74G", "price_symbol": "POWER_IT_SARD", "label": "IT-Sardegna", "ec_country": None},
    "IT_CALABRIA": {"eic": "10Y1001C--00096J", "price_symbol": "POWER_IT_CALA", "label": "IT-Calabria", "ec_country": None},
    "BG": {"eic": "10YCA-BULGARIA-R", "price_symbol": "POWER_BG", "label": "BG", "ec_country": "bg"},
    "HR": {"eic": "10YHR-HEP------M", "price_symbol": "POWER_HR", "label": "HR", "ec_country": "hr"},
    "SI": {"eic": "10YSI-ELES-----O", "price_symbol": "POWER_SI", "label": "SI", "ec_country": "si"},
    "SK": {"eic": "10YSK-SEPS-----K", "price_symbol": "POWER_SK", "label": "SK", "ec_country": "sk"},
    "FI": {"eic": "10YFI-1--------U", "price_symbol": "POWER_FI", "label": "FI", "ec_country": "fi"},
    "DK1": {"eic": "10YDK-1--------W", "price_symbol": "POWER_DK1", "label": "DK1", "ec_country": None},
    "DK2": {"eic": "10YDK-2--------M", "price_symbol": "POWER_DK2", "label": "DK2", "ec_country": None},
}


def _parse_enabled(raw: str) -> list[str]:
    """Parse settings.enabled_zones into an ordered, validated key list."""
    keys = [k.strip() for k in (raw or "").split(",") if k.strip()]
    valid = [k for k in keys if k in ZONE_REGISTRY]
    return valid or ["DE_LU", "FR", "NL"]  # fail-safe to the original three


ENABLED_ZONES: list[str] = _parse_enabled(settings.enabled_zones)

# The zones the app actually serves. Same shape/metadata as before for DE_LU/FR/NL.
POWER_ZONES: dict[str, dict] = {k: ZONE_REGISTRY[k] for k in ENABLED_ZONES}

# Default zone: DE_LU when enabled, else the first enabled zone.
DEFAULT_ZONE = "DE_LU" if "DE_LU" in POWER_ZONES else ENABLED_ZONES[0]

# Real cross-border pairs for the /flows route summary (Energy-Charts /cbpf, CC BY 4.0).
# Block 2 will derive this from the registry; kept explicit for the current 3 zones.
POWER_BORDERS: list[tuple[str, str]] = [
    ("BE", "DE_LU"),
    ("BE", "NL"),
    ("CH", "DE_LU"),
    ("CH", "FR"),
    ("DE_LU", "FR"),
    ("DE_LU", "NL"),
    ("FR", "GB"),
]
