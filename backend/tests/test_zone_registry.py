"""Canonical zone registry: full registry + config-driven enablement, unchanged defaults."""
from __future__ import annotations

from backend.power import zones
from backend.power.zones import (
    DEFAULT_ZONE,
    ENABLED_ZONES,
    POWER_ZONES,
    ZONE_REGISTRY,
    _parse_enabled,
)


def test_default_enabled_zones_unchanged():
    assert set(ENABLED_ZONES) == {"DE_LU", "FR", "NL"}
    assert set(POWER_ZONES) == {"DE_LU", "FR", "NL"}
    assert DEFAULT_ZONE == "DE_LU"


def test_original_three_zones_metadata_unchanged():
    assert POWER_ZONES["DE_LU"]["eic"] == "10Y1001A1001A82H"
    assert POWER_ZONES["DE_LU"]["price_symbol"] == "POWER_DE"
    assert POWER_ZONES["DE_LU"]["label"] == "DE-LU"
    assert POWER_ZONES["FR"]["price_symbol"] == "POWER_FR"
    assert POWER_ZONES["NL"]["price_symbol"] == "POWER_NL"


def test_registry_is_full_and_consistent():
    assert len(ZONE_REGISTRY) >= 27
    # every entry carries the required fields
    for key, meta in ZONE_REGISTRY.items():
        assert meta["eic"], key
        assert meta["price_symbol"], key
        assert meta["label"], key
        assert "ec_country" in meta, key
    # price symbols + EICs are unique across the registry
    symbols = [m["price_symbol"] for m in ZONE_REGISTRY.values()]
    eics = [m["eic"] for m in ZONE_REGISTRY.values()]
    assert len(symbols) == len(set(symbols))
    assert len(eics) == len(set(eics))


def test_enabled_zones_are_a_subset_of_the_registry():
    assert set(POWER_ZONES).issubset(set(ZONE_REGISTRY))


def test_parse_enabled_filters_and_failsafes():
    assert _parse_enabled("DE_LU,FR") == ["DE_LU", "FR"]
    assert _parse_enabled("DE_LU, BE ,AT") == ["DE_LU", "BE", "AT"]  # trims whitespace
    assert _parse_enabled("BOGUS,XX") == ["DE_LU", "FR", "NL"]       # all invalid → default
    assert _parse_enabled("") == ["DE_LU", "FR", "NL"]               # empty → default
    assert _parse_enabled("DE_LU,BOGUS,NL") == ["DE_LU", "NL"]       # drops unknown


def test_expanding_a_zone_is_config_only():
    # Enabling BE should require nothing but a config value + a registry entry.
    assert "BE" in ZONE_REGISTRY
    assert zones._parse_enabled("DE_LU,FR,NL,BE") == ["DE_LU", "FR", "NL", "BE"]
