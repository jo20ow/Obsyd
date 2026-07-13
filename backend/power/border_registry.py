"""Which borders actually exist — discovered from ENTSO-E, not drawn on a map.

The desk had no border list. `zones.py::POWER_BORDERS` was seven hand-written pairs, dead
since the A11 ingest was deleted (fb19ab3), and it still listed a border to GB — which is not
a zone we carry. Cross-border flows, meanwhile, were discovered from whatever Fraunhofer
Energy-Charts happened to return, which is COUNTRY-level: the 18 sub-zones (NO1-5, SE1-4,
DK1/2, IT_*) therefore had no border data at all, and DE_LU-DK1 existed only as an aggregate
`flow.DK` with no price behind it.

This list was SWEPT, not authored: every one of the 666 zone pairs was asked, and 63
answered. That matters, because a map lies in both directions and quietly:

    IT_SICILIA - IT_SUD       looks obvious. Does not exist.
    IT_SICILIA - IT_CALABRIA  does.
    IE_SEM                    has NO scheduled-exchange border at all — its only neighbour
                              is GB, which left ENTSO-E's publication after Brexit.

Non-existent pairs answer with a clean Acknowledgement, so the sweep is safe and cheap and
can be re-run whenever a zone is added: backend/scripts/probe_entsoe.py --doctype a09.

Swept 2026-07-13 against a 2-day window. 63 borders, 36 of 37 zones — every sub-zone included,
and with them the INTERNAL borders no country-level source can ever provide (NO1-NO2, SE3-SE4,
IT_NORD-IT_CENTRO_NORD). Pairs are canonical: sorted, so `(a, b)` with a < b, matching the
`flow.<TO>` under `<FROM>` convention in energy_charts_flows.py.
"""

from __future__ import annotations

from backend.power.zones import ZONE_REGISTRY

SCHEDULED_BORDERS: list[tuple[str, str]] = [
    ("AT", "CH"),
    ("AT", "CZ"),
    ("AT", "DE_LU"),
    ("AT", "HU"),
    ("AT", "IT_NORD"),
    ("AT", "SI"),
    ("BE", "DE_LU"),
    ("BE", "FR"),
    ("BE", "NL"),
    ("BG", "GR"),
    ("BG", "RO"),
    ("CH", "DE_LU"),
    ("CH", "FR"),
    ("CH", "IT_NORD"),
    ("CZ", "DE_LU"),
    ("CZ", "PL"),
    ("CZ", "SK"),
    ("DE_LU", "DK1"),
    ("DE_LU", "DK2"),
    ("DE_LU", "FR"),
    ("DE_LU", "NL"),
    ("DE_LU", "NO2"),
    ("DE_LU", "PL"),
    ("DE_LU", "SE4"),
    ("DK1", "DK2"),
    ("DK1", "NL"),
    ("DK1", "NO2"),
    ("DK1", "SE3"),
    ("DK2", "SE4"),
    ("ES", "FR"),
    ("ES", "PT"),
    ("FI", "SE1"),
    ("FI", "SE3"),
    ("FR", "IT_NORD"),
    ("GR", "IT_SUD"),
    ("HR", "HU"),
    ("HR", "SI"),
    ("HU", "RO"),
    ("HU", "SI"),
    ("HU", "SK"),
    ("IT_CALABRIA", "IT_SICILIA"),
    ("IT_CALABRIA", "IT_SUD"),
    ("IT_CENTRO_NORD", "IT_CENTRO_SUD"),
    ("IT_CENTRO_NORD", "IT_NORD"),
    ("IT_CENTRO_SUD", "IT_SARDEGNA"),
    ("IT_CENTRO_SUD", "IT_SUD"),
    ("IT_NORD", "SI"),
    ("NL", "NO2"),
    ("NO1", "NO2"),
    ("NO1", "NO3"),
    ("NO1", "NO5"),
    ("NO1", "SE3"),
    ("NO2", "NO5"),
    ("NO3", "NO4"),
    ("NO3", "NO5"),
    ("NO3", "SE2"),
    ("NO4", "SE1"),
    ("NO4", "SE2"),
    ("PL", "SE4"),
    ("PL", "SK"),
    ("SE1", "SE2"),
    ("SE2", "SE3"),
    ("SE3", "SE4"),
]


#: The one zone ENTSO-E publishes no scheduled exchange for. Named, not silently absent —
#: a zone that simply fails to appear looks like a bug; a zone that is listed here as
#: unbordered is a fact about the data.
ZONES_WITHOUT_BORDERS = ("IE_SEM",)


def directed_pairs() -> list[tuple[str, str]]:
    """Every border in BOTH directions — A09 is a directed query and the net is the
    difference between them. (a, b) and (b, a) for each canonical pair."""
    return [p for a, b in SCHEDULED_BORDERS for p in ((a, b), (b, a))]


def borders_for(zone: str) -> list[tuple[str, str]]:
    """The canonical borders `zone` participates in."""
    return [(a, b) for a, b in SCHEDULED_BORDERS if zone in (a, b)]


def counterparties(zone: str) -> list[str]:
    return sorted(b if a == zone else a for a, b in borders_for(zone))


# A pseudo-zone in this list is how `flow.DK` / `flow.NO` / `flow.GB` happened: series that
# exist in the store with no price, no zone, and nothing to join to. Fail at import.
_unknown = {z for pair in SCHEDULED_BORDERS for z in pair} - set(ZONE_REGISTRY)
if _unknown:  # pragma: no cover - a broken registry must not start the app
    raise RuntimeError(f"border registry references non-zones: {sorted(_unknown)}")
