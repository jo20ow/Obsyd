"""Which borders exist is a question for ENTSO-E, not for a map.

The desk has been bitten by hand-drawn geography twice. `zones.py::POWER_BORDERS` listed a
border to GB — not a zone we carry — and stayed there for a year after the ingest that used it
was deleted. And Energy-Charts, being country-level, produced series named `flow.DK`,
`flow.NO`, `flow.IT`, `flow.GB`, `flow.LU`: pseudo-zones that sit in the store today with no
price behind them and nothing to join to.

These tests exist so the swept registry cannot become the third instance.
"""
from __future__ import annotations

from backend.power import zones
from backend.power.border_registry import (
    SCHEDULED_BORDERS,
    ZONES_WITHOUT_BORDERS,
    borders_for,
    counterparties,
    directed_pairs,
)
from backend.power.zones import ZONE_REGISTRY

SUB_ZONES = [
    "DK1", "DK2",
    "NO1", "NO2", "NO3", "NO4", "NO5",
    "SE1", "SE2", "SE3", "SE4",
    "IT_NORD", "IT_CENTRO_NORD", "IT_CENTRO_SUD", "IT_SUD",
    "IT_SICILIA", "IT_SARDEGNA", "IT_CALABRIA",
]


def test_no_pseudo_zone_can_enter_the_border_registry():
    """`flow.DK` / `flow.NO` / `flow.GB` are real series in the store today, under keys that
    are not zones: country aggregates with no price and nothing to join. Every side of every
    border here must be a zone we actually carry."""
    for a, b in SCHEDULED_BORDERS:
        assert a in ZONE_REGISTRY, f"{a} is not a bidding zone"
        assert b in ZONE_REGISTRY, f"{b} is not a bidding zone"


def test_the_sub_zones_have_borders_at_last():
    """The whole point. All 18 of these had ZERO border data, because Energy-Charts reports
    Denmark, not DK1 and DK2. If this ever fails, someone has 'simplified' the registry back
    to country level and silently deleted half of Europe's borders again."""
    for zone in SUB_ZONES:
        assert borders_for(zone), f"{zone} has no border"


def test_the_internal_borders_no_country_source_can_ever_see():
    """DK1-DK2, NO1-NO2, SE3-SE4, IT_NORD-IT_CENTRO_NORD are borders WITHIN a country. A
    country-level feed cannot represent them even in principle — they net to zero."""
    for pair in [("DK1", "DK2"), ("NO1", "NO2"), ("SE3", "SE4"),
                 ("IT_CENTRO_NORD", "IT_NORD")]:
        assert tuple(sorted(pair)) in SCHEDULED_BORDERS


def test_geography_is_not_guessable():
    """Both directions of the trap, in one test. Sicily touches Calabria across the strait,
    not IT_SUD — and a map-reader would get that backwards. This is why the list was swept."""
    assert ("IT_CALABRIA", "IT_SICILIA") in SCHEDULED_BORDERS
    assert ("IT_SICILIA", "IT_SUD") not in SCHEDULED_BORDERS
    assert counterparties("IT_SICILIA") == ["IT_CALABRIA"]


def test_a_zone_with_no_border_is_NAMED_not_silently_absent():
    """IE_SEM publishes no scheduled exchange: its only neighbour is GB, which left ENTSO-E's
    publication after Brexit. A zone that merely fails to appear looks like a bug. A zone
    listed as unbordered is a fact about the data."""
    assert "IE_SEM" in ZONES_WITHOUT_BORDERS
    assert not borders_for("IE_SEM")
    covered = {z for pair in SCHEDULED_BORDERS for z in pair}
    assert set(ZONE_REGISTRY) - covered == set(ZONES_WITHOUT_BORDERS)


def test_pairs_are_canonical_sorted_unique_and_never_self():
    """The sign convention of the whole border layer rests on this: `net > 0` means the
    sorted-FIRST zone exports. An unsorted pair silently inverts a flow."""
    for a, b in SCHEDULED_BORDERS:
        assert a < b, f"({a}, {b}) is not sorted"
        assert a != b
    assert len(set(SCHEDULED_BORDERS)) == len(SCHEDULED_BORDERS)


def test_directed_pairs_are_both_ways_because_a09_is_directed():
    """A09 answers per direction and the net is A→B minus B→A. Query one leg only and a
    500 MW export reads as an export while 800 MW came the other way."""
    directed = directed_pairs()
    assert len(directed) == 2 * len(SCHEDULED_BORDERS)
    for a, b in SCHEDULED_BORDERS:
        assert (a, b) in directed and (b, a) in directed


def test_the_dead_hand_drawn_border_list_is_gone():
    """It listed a border to GB and outlived its only consumer by a year. Two competing border
    lists is exactly how the pseudo-zone series were born."""
    assert not hasattr(zones, "POWER_BORDERS")
