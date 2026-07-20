"""Server-side presentation metadata for the canonical hourly series (power_hourly).

Single source of truth for what a series is CALLED and which GROUP it belongs to.
Until now this lived only client-side (frontend/src/components/SeriesExplorer.jsx's
SERIES_LABELS/GROUP_ORDER/GROUP_LABELS/seriesLabel) — fine for one hand-built
Explorer, not for a planned Chart-Builder that needs the same answer without
duplicating the mapping in JS. Migrated here so both can read it (the v1 catalog
endpoint serves it; the frontend switch-over is a follow-on, not this change).

Pure: no DB access, no imports of anything that touches a session. `series_label`
and `series_group` must be safe to call on any string, known or not.
"""
from __future__ import annotations

from backend.power.entsoe_grid import PSR_LABELS
from backend.power.zones import ZONE_REGISTRY

# Friendly names for the canonical series the desk itself charts. Everything else
# (gen.<fuel>, consumption.<fuel>, flow.<zone>, sched.<zone>) gets a generated
# label via the pattern rules in series_label() below, so a series added by a new
# collector is readable in the catalog on day one, not just the raw key.
SERIES_LABELS: dict[str, str] = {
    "price.dayahead": "Day-ahead price · hourly",
    "price.dayahead.qh": "Day-ahead price · 15-min",
    "imbalance.price": "Imbalance price · hourly",
    "imbalance.price.qh": "Imbalance price · 15-min",
    "load.actual": "Load · actual",
    "load.forecast": "Load · TSO forecast",
    "residual.actual": "Residual load · actual",
    "residual.forecast": "Residual load · TSO forecast",
    "generation.forecast": "Generation · TSO forecast",
    "hydro.reservoir": "Hydro reservoir filling · weekly",
    "wind.actual": "Wind · actual",
    "solar.actual": "Solar · actual",
}

# Display order for grouped pickers (e.g. the Explorer's <optgroup> list) and the
# friendly name for each group key. Any group encountered that isn't listed here
# (a future series prefix) sorts after these, keyed by its own group key.
GROUP_ORDER: list[str] = [
    "price", "imbalance", "load", "residual", "generation", "wind", "solar",
    "gen", "consumption", "flow", "sched", "hydro",
]
GROUP_LABELS: dict[str, str] = {
    "price": "Prices",
    "imbalance": "Imbalance",
    "load": "Load",
    "residual": "Residual load",
    "generation": "Generation forecast",
    "wind": "Wind",
    "solar": "Solar",
    "gen": "Generation mix (per fuel)",
    "consumption": "Consumption (pumped storage)",
    "flow": "Cross-border flows (hourly)",
    "sched": "Scheduled commercial exchange (hourly)",
    "hydro": "Hydro",
}


def _zone_label(zone_key: str) -> str:
    """Zone registry label for a key, falling back to the raw key for one the
    registry doesn't know (e.g. a stale/typo'd series from an old ingest run)."""
    meta = ZONE_REGISTRY.get(zone_key)
    return meta["label"] if meta else zone_key


def series_label(key: str) -> str:
    """Human-readable label for a series key.

    Static keys (the desk's own canonical series) resolve from SERIES_LABELS.
    Dynamic keys are pattern-matched by their dot-prefix:
      gen.<Bxx> / consumption.<Bxx>  → PSR_LABELS (ENTSO-E production-type codes)
      flow.<ZONEKEY> / sched.<ZONEKEY> → the zone registry's label
    An unrecognised key falls back to itself — never raise, never hide a series.
    """
    if key in SERIES_LABELS:
        return SERIES_LABELS[key]
    if key.startswith("flow."):
        return f"Flow ↔ {_zone_label(key[len('flow.'):])}"
    if key.startswith("sched."):
        return f"Scheduled ↔ {_zone_label(key[len('sched.'):])}"
    if key.startswith("gen."):
        code = key[len("gen."):]
        return f"Generation · {PSR_LABELS.get(code, code)}"
    if key.startswith("consumption."):
        code = key[len("consumption."):]
        return f"Consumption · {PSR_LABELS.get(code, code)}"
    return key


def series_group(key: str) -> str:
    """Stable group key for a series — its dot-prefix (e.g. 'price', 'gen',
    'flow'), which is how every series in the store is namespaced. A key with
    no dot is its own group; this never raises."""
    return key.split(".", 1)[0]
