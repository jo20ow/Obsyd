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
    python -m backend.scripts.probe_entsoe --doctype a61     # day-ahead NTC per border
    python -m backend.scripts.probe_entsoe --doctype a71
    python -m backend.scripts.probe_entsoe --doctype a73     # generation per unit (DE-LU)

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
import io
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone

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
NTC_DOCTYPE = "A61"
NTC_CONTRACT_DAYAHEAD = "A01"  # contract_MarketAgreement.Type, not the curveType A01
UNIT_GENERATION_DOCTYPE = "A73"
UNIT_GENERATION_PROCESS_TYPE = "A16"

#: DE-LU for the A73 probe: the bidding zone plus its four control areas. ENTSO-E documents
#: per-unit generation at control-area granularity; the BZN row exists to prove or disprove
#: that the API accepts the bidding zone directly (which would spare us 4 requests/day).
A73_PROBE_DOMAINS = [
    ("BZN DE-LU", "10Y1001A1001A82H"),
    ("CTA 50Hertz", "10YDE-VE-------2"),
    ("CTA Amprion", "10YDE-RWENET---I"),
    ("CTA TenneT-DE", "10YDE-EON------1"),
    ("CTA TransnetBW", "10YDE-ENBW-----N"),
]


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


async def _get_bytes(client: httpx.AsyncClient, params: dict) -> tuple[int, bytes]:
    resp = await client.get(ENTSOE_BASE, params={"securityToken": _token(), **params})
    return resp.status_code, resp.content


def _documents(blob: bytes) -> list[str]:
    """A multi-document answer arrives as a zip (the A77 lesson); a single one as bare XML."""
    if blob[:2] == b"PK":
        zf = zipfile.ZipFile(io.BytesIO(blob))
        return [zf.read(name).decode("utf-8", "replace") for name in zf.namelist()]
    return [blob.decode("utf-8", "replace")]


def _reason(xml_text: str) -> str:
    """The Reason/text of an Acknowledgement — the exact phrase matters, because the
    ingest may cache 'genuine emptiness' only for known structural phrases."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return xml_text[:200].replace("\n", " ")
    for e in root.iter():
        if _localname(e.tag) == "text" and e.text:
            return e.text.strip()[:200]
    return _root_name(xml_text)


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


# ── A61: day-ahead NTC — which borders publish a transfer capacity at all ─────────────


async def probe_a61(dry_run: bool) -> int:
    from backend.power.border_registry import SCHEDULED_BORDERS, directed_pairs

    pairs = directed_pairs()
    print(f"# A61 day-ahead NTC — probing {len(pairs)} directed border pairs "
          f"({len(pairs) * THROTTLE_SECONDS / 60:.1f}+ min at {THROTTLE_SECONDS}s each)")
    if dry_run:
        print(f"# dry run: would probe {pairs[0]} … {pairs[-1]}")
        return 0

    answered: dict[tuple[str, str], int] = {}
    curve_types: Counter = Counter()
    resolutions: Counter = Counter()
    async with httpx.AsyncClient(timeout=90) as client:
        for i, (frm, to) in enumerate(pairs):
            try:
                status, xml = await _get(client, {
                    "documentType": NTC_DOCTYPE,
                    "contract_MarketAgreement.Type": NTC_CONTRACT_DAYAHEAD,
                    "out_Domain": ZONE_REGISTRY[frm]["eic"],
                    "in_Domain": ZONE_REGISTRY[to]["eic"],
                    "periodStart": PROBE_START, "periodEnd": PROBE_END,
                })
            except httpx.HTTPError as exc:
                print(f"  !! {frm}->{to}: {exc}", file=sys.stderr)
                continue
            if status == 200 and _has_data(xml):
                root = ET.fromstring(xml)
                curve_types.update(e.text for e in root.iter()
                                   if _localname(e.tag) == "curveType" and e.text)
                resolutions.update(e.text for e in root.iter()
                                   if _localname(e.tag) == "resolution" and e.text)
                answered[(frm, to)] = _points(xml)
                print(f"  ✓ {frm}->{to}  ({answered[(frm, to)]} points)")
            elif status != 200:
                print(f"  !! {frm}->{to}: HTTP {status}: {_reason(xml)}", file=sys.stderr)
            await asyncio.sleep(THROTTLE_SECONDS)
            if (i + 1) % 50 == 0:
                print(f"  … {i + 1}/{len(pairs)} probed, {len(answered)} directions so far",
                      file=sys.stderr)

    canonical = sorted({(min(a, b), max(a, b)) for a, b in answered})
    print(f"\n# {len(answered)} directions on {len(canonical)} borders answered.")
    print(f"# curveType seen: {dict(curve_types)}   resolution seen: {dict(resolutions)}")
    print("\n# Paste into backend/power/border_registry.py:\n")
    print("NTC_BORDERS: list[tuple[str, str]] = [")
    for a, b in canonical:
        print(f'    ("{a}", "{b}"),')
    print("]")
    one_way = [(a, b) for a, b in canonical
               if ((a, b) in answered) != ((b, a) in answered)]
    if one_way:
        print("\n# ONE-WAY publication (only one direction answered):")
        for a, b in one_way:
            direction = f"{a}->{b}" if (a, b) in answered else f"{b}->{a}"
            print(f"#   {direction}")
    silent = [p for p in SCHEDULED_BORDERS if p not in set(canonical)]
    print(f"\n# {len(silent)} scheduled borders with NO A61 "
          f"(expected: flow-based Core + Nordics publish none):")
    for a, b in silent:
        print(f"#   {a}-{b}")
    return 0


# ── A73: actual generation per generation unit ────────────────────────────────────────


def _a73_stats(docs: list[str]) -> dict:
    ts_count, points = 0, 0
    units: set[str] = set()
    psrs: Counter = Counter()
    resolutions: Counter = Counter()
    ends: list[str] = []
    for doc in docs:
        try:
            root = ET.fromstring(doc)
        except ET.ParseError:
            continue
        for e in root.iter():
            name = _localname(e.tag)
            if name == "TimeSeries":
                ts_count += 1
            elif name == "Point":
                points += 1
            elif name == "registeredResource.mRID" and e.text:
                units.add(e.text)
            elif name == "psrType" and e.text:
                psrs[e.text] += 1
            elif name == "resolution" and e.text:
                resolutions[e.text] += 1
            elif name == "end" and e.text:
                ends.append(e.text)
    return {"ts_count": ts_count, "points": points, "units": units,
            "psrs": psrs, "resolutions": resolutions,
            "latest_end": max(ends) if ends else None}


def _print_a73_result(label: str, status: int, blob: bytes) -> dict | None:
    kind = "zip" if blob[:2] == b"PK" else "xml"
    if status != 200:
        print(f"  – {label:16s} HTTP {status}: {_reason(blob.decode('utf-8', 'replace'))}")
        return None
    docs = _documents(blob)
    stats = _a73_stats(docs)
    if not stats["ts_count"]:
        print(f"  – {label:16s} 200 but no TimeSeries ({_root_name(docs[0])}: "
              f"{_reason(docs[0])})")
        return None
    print(f"  ✓ {label:16s} {kind}, {len(docs)} doc(s), {stats['ts_count']} TimeSeries, "
          f"{len(stats['units'])} units, {stats['points']} points, "
          f"res={dict(stats['resolutions'])}, latest end={stats['latest_end']}, "
          f"{len(blob):,} bytes")
    print(f"      psr={dict(stats['psrs'])}")
    return stats


async def probe_a73(dry_run: bool) -> int:
    now = datetime.now(timezone.utc)
    day = lambda d: (now + timedelta(days=d)).strftime("%Y%m%d0000")  # noqa: E731
    print(f"# A73/A16 generation per unit — probing {len(A73_PROBE_DOMAINS)} DE-LU domains")
    if dry_run:
        return 0

    def base(eic: str) -> dict:
        return {"documentType": UNIT_GENERATION_DOCTYPE,
                "processType": UNIT_GENERATION_PROCESS_TYPE, "in_Domain": eic}

    async with httpx.AsyncClient(timeout=180) as client:
        # German per-unit data is known to lag days, so "yesterday is empty" proves
        # nothing. Walk backwards on one CTA until a day answers — that lag IS a finding.
        scan_label, scan_eic = A73_PROBE_DOMAINS[1]  # 50Hertz
        window: tuple[str, str] | None = None
        print(f"# 1. publication-lag scan on {scan_label}:")
        for lag in (1, 3, 7, 30):
            status, blob = await _get_bytes(
                client, {**base(scan_eic), "periodStart": day(-lag),
                         "periodEnd": day(-lag + 1)})
            if _print_a73_result(f"D-{lag}", status, blob):
                window = (day(-lag), day(-lag + 1))
                break
            await asyncio.sleep(THROTTLE_SECONDS)

        if window is None:
            print("\n# NO window up to D-30 answered on the scan CTA — Slice C is a NO-GO"
                  " (or the domain choice is wrong; try other CTAs manually).")
            return 0

        start, end = window
        print(f"\n# 2. all domains at the first answering window {start}->{end}:")
        best: tuple[str, str] | None = None
        for label, eic in A73_PROBE_DOMAINS:
            try:
                status, blob = await _get_bytes(
                    client, {**base(eic), "periodStart": start, "periodEnd": end})
            except httpx.HTTPError as exc:
                print(f"  !! {label}: {exc}", file=sys.stderr)
                continue
            if _print_a73_result(label, status, blob) and best is None:
                best = (label, eic)
            await asyncio.sleep(THROTTLE_SECONDS)

        if best is None:
            return 0
        label, eic = best
        print(f"\n# Follow-ups on {label} ({eic}):")

        print("# 3. explicit offset — pagination semantics (offset=0 must not change the answer):")
        for offset in (0, 100):
            status, blob = await _get_bytes(
                client, {**base(eic), "periodStart": start, "periodEnd": end,
                         "offset": str(offset)})
            _print_a73_result(f"offset={offset}", status, blob)
            await asyncio.sleep(THROTTLE_SECONDS)

        print("# 4. window limit — 8-day window ending at the answering day "
              "(docs say 1 day; record the exact phrase):")
        status, blob = await _get_bytes(
            client, {**base(eic),
                     "periodStart": (datetime.strptime(start, "%Y%m%d%H%M")
                                     - timedelta(days=7)).strftime("%Y%m%d%H%M"),
                     "periodEnd": end})
        _print_a73_result("8-day window", status, blob)
        print(f"# now = {now.isoformat(timespec='minutes')}; lag = see scan above.")
    return 0


PROBES = {"a09": probe_a09, "a25": probe_a25, "a61": probe_a61,
          "a71": probe_a71, "a73": probe_a73}


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
