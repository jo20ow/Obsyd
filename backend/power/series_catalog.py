"""Server-side presentation metadata for the canonical hourly series (power_hourly).

Single source of truth for what a series is CALLED and which GROUP it belongs to.
Until now this lived only client-side (frontend/src/components/SeriesExplorer.jsx's
SERIES_LABELS/GROUP_ORDER/GROUP_LABELS/seriesLabel) — fine for one hand-built
Explorer, not for a planned Chart-Builder that needs the same answer without
duplicating the mapping in JS. Migrated here so both can read it (the v1 catalog
endpoint serves it; the frontend switch-over is a follow-on, not this change).

No DB access at call time — `series_label`/`series_group`/`catalog_groups` are safe
to call on any string with no session in scope. That is not the same as import-side
purity: importing PSR_LABELS from entsoe_grid transitively pulls in httpx/SQLAlchemy/
settings/raw_cache at IMPORT time (entsoe_grid is a collector module, not a leaf
constants file). Nothing in this module opens a session or issues a query.
"""
from __future__ import annotations

from collections.abc import Iterable

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
    "outage.offline": "Outages · capacity offline",
    "outage.forced": "Outages · forced offline",
    "netpos.dayahead": "Net position · day-ahead",
}

#: aFRR/mFRR product-code -> display name, shared by the balancing.*/capacity.* pattern
#: rules below (FCR/aFRR/mFRR are ENTSO-E's own product names, not this desk's invention).
_RESERVE_PRODUCT_LABELS: dict[str, str] = {"fcr": "FCR", "afrr": "aFRR", "mfrr": "mFRR"}

# Display order for grouped pickers (e.g. the Explorer's <optgroup> list) and the
# friendly name for each group key. Any group encountered that isn't listed here
# (a future series prefix) sorts after these, keyed by its own group key.
GROUP_ORDER: list[str] = [
    "price", "imbalance", "load", "residual", "generation", "wind", "solar",
    "gen", "consumption", "flow", "sched", "hydro",
    "balancing", "capacity", "outage", "netpos",
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
    "balancing": "Balancing energy",
    "capacity": "Balancing capacity",
    "outage": "Outages",
    "netpos": "Net position",
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
      balancing.<product>.<price|vol>.<up|down> → activated balancing ENERGY (A83/A84)
      capacity.<fcr.price | <product>.price.<pos|neg>> → procured balancing CAPACITY (A15)
    The "Balancing capacity ·" prefix on the latter is deliberate: this is NOT the same
    thing as `/api/v1/capacity` (installed generation capacity, A68) — the label must say
    so on sight, not just via a different key.
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
    if key.startswith("balancing."):
        parts = key.split(".")
        if len(parts) == 4:
            _, product, measure, direction = parts
            name = _RESERVE_PRODUCT_LABELS.get(product, product.upper())
            if measure == "price":
                return f"Balancing · {name} activation price ({direction})"
            if measure == "vol":
                return f"Balancing · {name} activated volume ({direction})"
        return f"Balancing · {key[len('balancing.'):]}"
    if key.startswith("capacity."):
        parts = key.split(".")
        if parts[1:] == ["fcr", "price"]:
            return "Balancing capacity · FCR price"
        if len(parts) == 4 and parts[2] == "price":
            _, product, _measure, direction = parts
            name = _RESERVE_PRODUCT_LABELS.get(product, product.upper())
            return f"Balancing capacity · {name} price ({direction})"
        return f"Balancing capacity · {key[len('capacity.'):]}"
    return key


def series_group(key: str) -> str:
    """Stable group key for a series — its dot-prefix (e.g. 'price', 'gen',
    'flow'), which is how every series in the store is namespaced. A key with
    no dot is its own group; this never raises."""
    return key.split(".", 1)[0]


def catalog_groups(keys: Iterable[str]) -> list[dict]:
    """[{key, label}] for exactly the groups present among `keys`, in GROUP_ORDER
    order — the data a client needs to render grouped pickers (e.g. the
    Explorer's <optgroup> list) without hardcoding its own copy of GROUP_ORDER/
    GROUP_LABELS. A group not in GROUP_ORDER (a new series prefix nobody has
    labelled yet) is appended afterward, sorted by its own key, so the response
    is deterministic even for an unlabelled group."""
    present = {series_group(k) for k in keys}
    ordered = [g for g in GROUP_ORDER if g in present]
    extra = sorted(present - set(GROUP_ORDER))
    return [{"key": g, "label": GROUP_LABELS.get(g, g)} for g in ordered + extra]
