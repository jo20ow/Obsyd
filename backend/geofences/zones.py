"""
Geofence bounding boxes for energy chokepoints and key port areas.

Each zone is defined as a dict with:
  - name: Human-readable name
  - bounds: [[lat_min, lon_min], [lat_max, lon_max]]
  - description: What this zone monitors

All coordinates are WGS84 (lat/lon).
AIS ship types 80-89 = tankers (liquid cargo).
"""

TANKER_SHIP_TYPES = range(80, 90)

ZONES: list[dict] = [
    {
        "name": "hormuz",
        "display_name": "Strait of Hormuz",
        "bounds": [[25.0, 55.5], [27.0, 57.5]],
        "description": "Persian Gulf to Arabian Sea transit. ~20% of global seaborne crude.",
    },
    {
        "name": "suez",
        "display_name": "Suez Canal / Bab-el-Mandeb",
        "bounds": [[12.0, 32.0], [31.5, 44.0]],
        "description": "Red Sea to Mediterranean transit via Suez Canal.",
    },
    {
        "name": "malacca",
        "display_name": "Strait of Malacca",
        "bounds": [[1.0, 99.5], [4.5, 104.5]],
        "description": "Indian Ocean to South China Sea. Key route for Asian oil imports.",
    },
    {
        "name": "panama",
        "display_name": "Panama Canal",
        "bounds": [[7.0, -80.5], [10.0, -78.5]],
        "description": "Atlantic to Pacific transit.",
    },
    {
        "name": "cape",
        "display_name": "Cape of Good Hope",
        "bounds": [[-36.0, 17.0], [-33.0, 21.0]],
        "description": "Alternative route when Suez is disrupted.",
    },
    {
        "name": "houston",
        "display_name": "Gulf of Mexico / Houston",
        "bounds": [[27.5, -96.0], [30.0, -93.5]],
        "description": "Gulf Coast refineries, Houston Ship Channel, LOOP terminal.",
    },
]


# Zones with no terrestrial AIS coverage (AISHub has no shore stations nearby)
NO_AIS_COVERAGE = {"suez", "panama", "cape"}


# STS transfer hotspot zones — smaller geofences where ship-to-ship transfers
# commonly occur. Vessels anchored (SOG < 1 kn) in these zones are flagged.
STS_HOTSPOTS: list[dict] = [
    {
        "name": "sts_laconian",
        "display_name": "Laconian Gulf (Greece)",
        "bounds": [[36.3, 22.3], [36.9, 23.1]],
        "description": "Major STS hub for Russian crude oil transfers.",
    },
    {
        "name": "sts_oman",
        "display_name": "Gulf of Oman (Fujairah)",
        "bounds": [[25.0, 56.0], [25.5, 56.6]],
        "description": "Fujairah anchorage — STS transfers and bunkering hub.",
    },
    {
        "name": "sts_malaysia",
        "display_name": "East of Port Limits (Malaysia)",
        "bounds": [[1.15, 104.25], [1.45, 104.65]],
        "description": "Singapore Strait EOPL — STS for sanctioned/blended crude.",
    },
    {
        "name": "sts_lome",
        "display_name": "Lomé Anchorage (Togo)",
        "bounds": [[5.8, 1.0], [6.3, 1.6]],
        "description": "West Africa STS hub for Nigerian crude.",
    },
    {
        "name": "sts_kalamata",
        "display_name": "Kalamata (Greece)",
        "bounds": [[36.6, 21.6], [37.1, 22.3]],
        "description": "Secondary Greek STS zone near Laconian Gulf.",
    },
]


def point_in_sts_zone(lat: float, lon: float) -> dict | None:
    """Return the STS hotspot zone for a given position, or None."""
    for zone in STS_HOTSPOTS:
        if point_in_zone(lat, lon, zone):
            return zone
    return None


def point_in_zone(lat: float, lon: float, zone: dict) -> bool:
    """Check if a lat/lon point is inside a zone's bounding box."""
    (lat_min, lon_min), (lat_max, lon_max) = zone["bounds"]
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def find_zone(lat: float, lon: float) -> dict | None:
    """Return the first matching zone for a given position, or None."""
    for zone in ZONES:
        if point_in_zone(lat, lon, zone):
            return zone
    return None


def is_tanker(ship_type: int) -> bool:
    """Check if AIS ship type indicates a tanker (liquid cargo)."""
    return ship_type in TANKER_SHIP_TYPES
