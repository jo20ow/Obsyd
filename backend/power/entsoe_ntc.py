"""Day-ahead NTC (ENTSO-E A61) — the capacity the auction was actually offered.

WHAT THIS IS, AND IS NOT
------------------------
A61 with contract A01 is the Net Transfer Capacity OFFERED TO THE DAY-AHEAD
AUCTION, per directed border. It exists only for NTC-ALLOCATED borders: the
flow-based Core region and the Nordics allocate capacity flow-based, publish no
per-border NTC, and never will — that is a market-design fact, not missing data.
For those borders the desk's "at the rail" p95 proxy (borders.py) stays the
honest reference.

NTC is NOT a physical thermal limit. It is what the TSOs offered the SDAC
auction after their own security margins, and utilization measured against it
can exceed 100% once intraday trading and countertrading move the border past
its day-ahead allocation. Descriptive, per Posture B: |flow| ÷ offered capacity
is a statement about published records, not about what the wires could carry.

Swept 2026-07-28 (backend/scripts/probe_entsoe.py --doctype a61): all 126
directed scheduled-border pairs asked, 23 of 63 borders answered — in BOTH
directions each, no one-way publication. curveType is always A03 (the sparse
step function `parse_step_series` exists for), resolution mostly PT60M with
occasional PT15M, points sparse (1-48 per 2-day window). The 40 silent borders
are the flow-based Core region plus the Nordics.

THE SIGN CONVENTION DEVIATES — DELIBERATELY
-------------------------------------------
`flow.*` and `sched.*` net the two directions onto the canonical SORTED pair.
NTC is stored as `ntc.<TO>` under zone `<FROM>` — ONE SERIES PER DIRECTION,
non-negative magnitudes, NEVER netted. A->B and B->A are two INDEPENDENT
offered capacities (frequently asymmetric), and both are needed as
denominators: an export hour divides by the forward NTC, an import hour by the
reverse one. Netting them would destroy exactly the quantity this series
exists to provide.
"""

from __future__ import annotations

import logging
from datetime import date

import httpx
from sqlalchemy.orm import Session

from backend.config import settings
from backend.gas import raw_cache
from backend.gas.entsoe import ENTSOE_BASE, _token
from backend.power.border_registry import NTC_BORDERS
from backend.power.entsoe_exchange import _month_bounds, parse_step_series
from backend.power.hourly_store import upsert_hourly
from backend.power.zones import ZONE_REGISTRY

logger = logging.getLogger(__name__)

#: Forecasted transfer capacities. NOT the A11 physical-flow doctype the deleted
#: ingest used, and not a docStatus.
NTC_DOCTYPE = "A61"

#: contract_MarketAgreement.Type "A01" = DAILY (the day-ahead allocation) — a
#: contract code, NOT the curveType A01. The documents themselves come back as
#: curveType A03 (probe-verified), which is why parse_step_series reads them.
CONTRACT_DAYAHEAD = "A01"

#: Globally unique. "entsoe_scheduled_exchange" is A09, "entsoe_netpos" is A25 —
#: sharing either would serve back the wrong document from disk.
CACHE_SOURCE = "entsoe_a61"

SERIES_PREFIX = "ntc."


async def _fetch_ntc_month(
    out_zone: str, in_zone: str, month_start: date, *, overwrite: bool = False
) -> str:
    """One directed border-month of A61, disk-cached. Returns "" on a clean no-data ACK."""
    out_eic = ZONE_REGISTRY[out_zone]["eic"]
    in_eic = ZONE_REGISTRY[in_zone]["eic"]
    period_start, period_end = _month_bounds(month_start)

    async def _do() -> dict:
        params = {
            "securityToken": _token(),
            "documentType": NTC_DOCTYPE,
            "contract_MarketAgreement.Type": CONTRACT_DAYAHEAD,
            "out_Domain": out_eic,
            "in_Domain": in_eic,
            "periodStart": period_start,
            "periodEnd": period_end,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(ENTSOE_BASE, params=params)
            if resp.status_code == 400:
                # A border-month with no NTC answers 400 with a clean Acknowledgement
                # (probe-verified). That is data, not an error: cache the emptiness so
                # we never ask again.
                return {"xml": ""}
            # Anything else non-2xx (5xx, 429 …) is transient — raise so nothing is
            # cached and the next run asks again.
            resp.raise_for_status()
            return {"xml": resp.text}

    payload = await raw_cache.fetch_or_cache(
        CACHE_SOURCE,
        f"{out_zone}_{in_zone}_{month_start:%Y-%m}",
        month_start,
        _do,
        overwrite=overwrite,
    )
    return payload.get("xml", "")


async def ingest_ntc(
    db: Session,
    months: list[date],
    *,
    borders: list[tuple[str, str]] | None = None,
    overwrite: bool = False,
) -> dict:
    """Day-ahead NTC per border-month → `ntc.<TO>` under zone `<FROM>`, BOTH directions.

    Two upserts per border-month, never netted (see module docstring): the two
    directions are independent offered capacities and both are denominators.
    """
    if not settings.entsoe_api_token:
        return {"skipped": "no token"}

    borders = borders or NTC_BORDERS
    written = 0
    covered = 0
    for a, b in borders:
        for month in months:
            for frm, to in ((a, b), (b, a)):
                try:
                    xml = await _fetch_ntc_month(frm, to, month, overwrite=overwrite)
                except httpx.HTTPError as exc:
                    logger.warning("ntc %s->%s %s: %s", frm, to, month, exc)
                    continue
                if not xml:
                    continue  # a cached clean ACK — genuine emptiness, not a failure
                try:
                    points = parse_step_series(xml)
                except ValueError as exc:
                    logger.warning("ntc %s->%s %s: %s", frm, to, month, exc)
                    continue
                if not points:
                    continue
                written += upsert_hourly(db, f"{SERIES_PREFIX}{to}", frm,
                                         sorted(points.items()), unit="MW")
                covered += 1
    db.commit()
    return {"borders": len(borders), "direction_months": covered, "written": written}
