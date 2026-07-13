"""Ask ENTSO-E what it actually has, before we write code that assumes.

Three times in Tier 1 the data refused the plan: the seasonal baseline was defeated by
fleet growth, A77 turned out to hold no history at all, and a query that answered in 13 ms
on the dev database took 7.8 s on prod. Every one of those was cheap to discover and
expensive to discover late. So: probe first, in a script that anyone can re-run, and write
the finding down.

READ-ONLY BY CONSTRUCTION. This never touches the database and never writes the raw cache —
a probe that populates the cache would poison the ingest that follows it with documents
fetched under exploratory parameters.

    python -m backend.scripts.probe_entsoe --doctype a09 --dry-run
    python -m backend.scripts.probe_entsoe --doctype a09     # the border discovery sweep
    python -m backend.scripts.probe_entsoe --doctype a25
    python -m backend.scripts.probe_entsoe --doctype a71

WHY A09 SWEEPS EVERY PAIR INSTEAD OF A GEOGRAPHIC GUESS
-------------------------------------------------------
Because guessing is wrong in both directions, and quietly. IT_SICILIA↔IT_SUD looks obvious
on a map and does not exist; IT_SICILIA↔IT_CALABRIA does. A hand-authored adjacency list is
how `zones.py::POWER_BORDERS` ended up listing a border to GB, which is not a zone we carry.
Non-existent pairs answer with a clean Acknowledgement, so the full sweep is safe, cheap
(one small window per pair) and it is the only version of this list that cannot be wrong.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import xml.etree.ElementTree as ET
from collections import Counter

import httpx

from backend.config import settings
from backend.gas.entsoe import ENTSOE_BASE, _localname, _token
from backend.power.zones import ZONE_REGISTRY

#: ENTSO-E's published ceiling is ~400 requests/minute. Stay far under it: this script is a
#: courtesy caller on a free public API, and a ban costs the whole desk, not just the probe.
THROTTLE_SECONDS = 0.35

#: A 2-day window is enough to answer "does this border exist at all" and keeps every
#: response small. Coverage over TIME is a separate question, answered by the ingest.
PROBE_START = "202607010000"
PROBE_END = "202607030000"

SCHEDULED_EXCHANGE_DOCTYPE = "A09"
NET_POSITION_DOCTYPE = "A25"
NET_POSITION_BUSINESS_TYPE = "B09"  # NOT the psrType B09 (= "Geothermal")
UNIT_REGISTRY_DOCTYPE = "A71"
UNIT_REGISTRY_PROCESS_TYPE = "A33"


async def _get(client: httpx.AsyncClient, params: dict) -> tuple[int, str]:
    resp = await client.get(ENTSOE_BASE, params={"securityToken": _token(), **params})
    return resp.status_code, resp.text


def _root_name(xml_text: str) -> str:
    try:
        return _localname(ET.fromstring(xml_text).tag)
    except ET.ParseError:
        return "unparseable"


def _has_data(xml_text: str) -> bool:
    """An Acknowledgement_MarketDocument is ENTSO-E politely saying "nothing here"."""
    return _root_name(xml_text).endswith("Publication_MarketDocument")


def _points(xml_text: str) -> int:
    return sum(1 for e in ET.fromstring(xml_text).iter() if _localname(e.tag) == "Point")


# ── A09: the border discovery sweep ───────────────────────────────────────────────────


async def probe_a09(dry_run: bool) -> int:
    zones = sorted(ZONE_REGISTRY)
    pairs = [(a, b) for i, a in enumerate(zones) for b in zones[i + 1 :]]
    print(f"# A09 scheduled exchanges — sweeping {len(pairs)} zone pairs "
          f"({len(pairs) * THROTTLE_SECONDS / 60:.1f} min at {THROTTLE_SECONDS}s each)")
    if dry_run:
        print(f"# dry run: would probe {pairs[0]} … {pairs[-1]}")
        return 0

    found: list[tuple[str, str]] = []
    async with httpx.AsyncClient(timeout=90) as client:
        for i, (a, b) in enumerate(pairs):
            try:
                status, xml = await _get(client, {
                    "documentType": SCHEDULED_EXCHANGE_DOCTYPE,
                    "contract_MarketAgreement.Type": "A05",
                    "out_Domain": ZONE_REGISTRY[a]["eic"],
                    "in_Domain": ZONE_REGISTRY[b]["eic"],
                    "periodStart": PROBE_START, "periodEnd": PROBE_END,
                })
            except httpx.HTTPError as exc:
                print(f"  !! {a}->{b}: {exc}", file=sys.stderr)
                continue
            if status == 200 and _has_data(xml):
                found.append((a, b))
                print(f"  ✓ {a}-{b}  ({_points(xml)} points)")
            await asyncio.sleep(THROTTLE_SECONDS)
            if (i + 1) % 100 == 0:
                print(f"  … {i + 1}/{len(pairs)} probed, {len(found)} borders so far",
                      file=sys.stderr)

    print(f"\n# {len(found)} borders answered. Paste into backend/power/border_registry.py:\n")
    print("SCHEDULED_BORDERS: list[tuple[str, str]] = [")
    for a, b in found:
        print(f'    ("{a}", "{b}"),')
    print("]")

    covered = {z for pair in found for z in pair}
    missing = sorted(set(ZONE_REGISTRY) - covered)
    if missing:
        print(f"\n# Zones with NO scheduled-exchange border: {', '.join(missing)}")
    return 0


# ── A25: which zones publish a market net position ────────────────────────────────────


async def probe_a25(dry_run: bool) -> int:
    zones = sorted(ZONE_REGISTRY)
    print(f"# A25/B09 market net position — probing {len(zones)} zones")
    if dry_run:
        return 0

    answered, empty = [], []
    async with httpx.AsyncClient(timeout=180) as client:
        for zone in zones:
            eic = ZONE_REGISTRY[zone]["eic"]
            try:
                status, xml = await _get(client, {
                    "documentType": NET_POSITION_DOCTYPE,
                    "businessType": NET_POSITION_BUSINESS_TYPE,
                    "contract_MarketAgreement.Type": "A01",  # mandatory — rejected without it
                    "in_Domain": eic, "out_Domain": eic,
                    "periodStart": PROBE_START, "periodEnd": PROBE_END,
                })
            except httpx.HTTPError as exc:
                print(f"  !! {zone}: {exc}", file=sys.stderr)
                continue
            if status == 200 and _has_data(xml):
                # The sign lives in the domain PAIR, not in the quantity: a TimeSeries whose
                # out_Domain is the zone is an EXPORT block, one whose in_Domain is the zone
                # is an IMPORT block. Count both — a zone showing only one kind would mean
                # the sweep window caught it never flipping, not that it cannot.
                blocks = Counter()
                for ts in ET.fromstring(xml).iter():
                    if _localname(ts.tag) != "TimeSeries":
                        continue
                    out_ = next((e.text for e in ts.iter()
                                 if _localname(e.tag) == "out_Domain.mRID"), None)
                    blocks["export" if out_ == eic else "import"] += 1
                answered.append(zone)
                print(f"  ✓ {zone:16s} {_points(xml):4d} points  "
                      f"{blocks['export']} export / {blocks['import']} import blocks")
            else:
                empty.append(zone)
                print(f"  – {zone:16s} no data")
            await asyncio.sleep(THROTTLE_SECONDS)

    print(f"\n# {len(answered)}/{len(zones)} zones publish A25. No coverage: "
          f"{', '.join(empty) or 'none'}")
    return 0


# ── A71/A33: the production-unit registry ─────────────────────────────────────────────


async def probe_a71(dry_run: bool) -> int:
    zones = sorted(ZONE_REGISTRY)
    print(f"# A71/A33 production units — probing {len(zones)} zones (slow: up to ~9s each)")
    if dry_run:
        return 0

    total_units = 0
    async with httpx.AsyncClient(timeout=180) as client:
        for zone in zones:
            try:
                status, xml = await _get(client, {
                    "documentType": UNIT_REGISTRY_DOCTYPE,
                    "processType": UNIT_REGISTRY_PROCESS_TYPE,
                    "in_Domain": ZONE_REGISTRY[zone]["eic"],
                    "periodStart": "202601010000", "periodEnd": "202601020000",
                })
            except httpx.HTTPError as exc:
                print(f"  !! {zone}: {exc}", file=sys.stderr)
                continue
            if status == 200 and _has_data(xml):
                root = ET.fromstring(xml)
                nominals = [float(e.text) for e in root.iter()
                            if _localname(e.tag) == "nominalP" and e.text]
                psrs = Counter(e.text for e in root.iter()
                               if _localname(e.tag) == "psrType" and e.text)
                total_units += len(nominals)
                print(f"  ✓ {zone:16s} {len(nominals):4d} units  "
                      f"{sum(nominals):9,.0f} MW  psr={dict(psrs)}")
            else:
                print(f"  – {zone:16s} no data")
            await asyncio.sleep(THROTTLE_SECONDS)

    print(f"\n# {total_units} published units across the registry.")
    print("# NOTE: this is the >~100 MW publication threshold, NOT the installed fleet (A68).")
    return 0


PROBES = {"a09": probe_a09, "a25": probe_a25, "a71": probe_a71}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--doctype", required=True, choices=sorted(PROBES))
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be probed, make no requests")
    args = ap.parse_args(argv[1:])

    if not settings.entsoe_api_token and not args.dry_run:
        print("ENTSOE_API_TOKEN is not set.", file=sys.stderr)
        return 1
    return asyncio.run(PROBES[args.doctype](args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
