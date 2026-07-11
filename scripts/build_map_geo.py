"""Build frontend/public/geo/eu-zones.geojson from Electricity Maps' zone geometry.

Source: https://github.com/electricitymaps/electricitymaps-contrib (AGPL-3.0,
same license family as this project) — geo/world.geojson, 362 world zones.
We keep the European bidding zones OBSYD serves (tagged with their OBSYD zone
key) plus neighbouring countries as untinted context, quantize coordinates to
3 decimals (~110 m — plenty for a zoom≤6 overview map), and drop everything
else. Result is a self-contained file the map loads in one request.

Usage:
    python scripts/build_map_geo.py path/to/world.geojson

Re-run only when zones change; the output is committed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path("frontend/public/geo/eu-zones.geojson")

#: Electricity-Maps zoneName → OBSYD zone key. Multiple features may map to one
#: key (DE_LU = the DE and LU polygons); IT_CALABRIA has no separate polygon in
#: this geometry (it is part of IT-SO) and stays points-only on the map.
EM_TO_OBSYD = {
    "DE": "DE_LU", "LU": "DE_LU",
    "FR": "FR", "FR-COR": "FR",
    "NL": "NL", "BE": "BE", "AT": "AT", "ES": "ES", "PT": "PT",
    "PL": "PL", "CZ": "CZ", "HU": "HU", "RO": "RO", "GR": "GR",
    "IE": "IE_SEM", "BG": "BG", "HR": "HR", "SI": "SI", "SK": "SK",
    "FI": "FI", "CH": "CH",
    "IT-NO": "IT_NORD", "IT-CNO": "IT_CENTRO_NORD", "IT-CSO": "IT_CENTRO_SUD",
    "IT-SO": "IT_SUD", "IT-SIC": "IT_SICILIA", "IT-SAR": "IT_SARDEGNA",
    "DK-DK1": "DK1", "DK-DK2": "DK2",
    "NO-NO1": "NO1", "NO-NO2": "NO2", "NO-NO3": "NO3", "NO-NO4": "NO4", "NO-NO5": "NO5",
    "SE-SE1": "SE1", "SE-SE2": "SE2", "SE-SE3": "SE3", "SE-SE4": "SE4",
}

#: Neighbours rendered as untinted context so the continent reads as a place,
#: not floating shapes. GB-NIR joins IE_SEM visually? No — SEM is the all-island
#: MARKET, but we keep the map political: NIR stays context.
CONTEXT = {
    "GB", "GB-NIR", "UA", "UA-CR", "RS", "BA", "MK", "AL", "ME", "XK", "MD",
    "TR", "EE", "LV", "LT", "IS", "MT", "CY",
}

# Lon/lat window: mainland Europe incl. Nordics; excludes the Canaries/Azores
# outliers that would stretch the viewport.
BBOX = (-25.0, 33.0, 45.0, 72.0)


def _q(x: float) -> float:
    return round(x, 3)


def _quantize(coords):
    """Recursively quantize a (multi)polygon coordinate array, dropping
    consecutive duplicate points created by the rounding."""
    if isinstance(coords[0], (int, float)):
        return [_q(coords[0]), _q(coords[1])]
    out = []
    prev = None
    for c in coords:
        qc = _quantize(c)
        if qc != prev:
            out.append(qc)
        prev = qc
    return out


def _in_bbox(geom) -> bool:
    def points(c):
        if isinstance(c[0], (int, float)):
            yield c
        else:
            for s in c:
                yield from points(s)

    for lon, lat in points(geom["coordinates"]):
        if BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]:
            return True
    return False


def main(src: str) -> None:
    world = json.loads(Path(src).read_text())
    kept = []
    for f in world["features"]:
        name = f["properties"].get("zoneName", "")
        zone = EM_TO_OBSYD.get(name)
        if zone is None and name not in CONTEXT:
            continue
        if not _in_bbox(f["geometry"]):
            continue
        kept.append({
            "type": "Feature",
            "properties": {"zone": zone, "em": name},
            "geometry": {
                "type": f["geometry"]["type"],
                "coordinates": _quantize(f["geometry"]["coordinates"]),
            },
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "type": "FeatureCollection",
        "attribution": "Zone geometry © Electricity Maps contributors (AGPL-3.0), electricitymaps-contrib",
        "features": kept,
    }, separators=(",", ":")))
    zones = sorted({f["properties"]["zone"] for f in kept if f["properties"]["zone"]})
    print(f"{len(kept)} features → {OUT} ({OUT.stat().st_size / 1e6:.2f} MB)")
    print(f"{len(zones)} OBSYD zones: {', '.join(zones)}")


if __name__ == "__main__":
    main(sys.argv[1])
