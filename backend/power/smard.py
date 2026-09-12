"""German congestion-management costs from SMARD (Bundesnetzagentur) —
CC BY 4.0, the one clean licence in this space.

What the desk gains: the PRICE of congestion. The map shows saturated
interconnectors and the outage board shows why; nobody in the free tier shows
what the resulting redispatch actually costs. SMARD publishes the TSOs'
monthly totals (Germany, since 2022-07):

    congestion.cost.security        — "ÜNB-Netzsicherheit": the network-security
                                      bundle (redispatch including feed-in
                                      management, reserve activations) in EUR
    congestion.cost.countertrading  — countertrading in EUR

Stored under DE_LU (the data is national), one point per month at the 1st
00:00 UTC. The two columns are SMARD's own split, not this desk's invention —
what "Netzsicherheit" bundles is defined by their Netzengpassmanagement
article, and the series deliberately keeps their umbrella rather than
pretending to a redispatch-only precision the source does not publish.

Plumbing: these modules exist ONLY in SMARD's download manager — the public
chart API carries no congestion filters (probed 2026-09, see
docs/findings/2026-09-13-smard-congestion-costs.md). The download manager is
an unauthenticated POST returning the FULL history as one small CSV
(German number format, semicolon-separated, "-" for months not yet
published — the lag runs 3-4 months). Every fetch re-reads the whole
history, so restatements by the source are absorbed on every run.

Attribution (CC BY 4.0): "Bundesnetzagentur | SMARD.de" — carried in
/api/v1/meta and docs/API.md.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session

from backend.gas import raw_cache
from backend.power.hourly_store import upsert_hourly

logger = logging.getLogger(__name__)

DOWNLOAD_URL = "https://www.smard.de/nip-download-manager/nip/download/market-data"
ATTRIBUTION = "Bundesnetzagentur | SMARD.de (CC BY 4.0)"
ZONE = "DE_LU"

#: Download-manager module ids (from /app/chart_configuration/
#: market_data_configuration.json, superCategory 5 "Systemstabilität",
#: sub 16 "Gesamtkosten").
MODULE_SECURITY = 16000418       # Kosten Netzsicherheitsmaßnahmen
MODULE_COUNTERTRADING = 16000419  # Kosten Countertrading

SERIES_SECURITY = "congestion.cost.security"
SERIES_COUNTERTRADING = "congestion.cost.countertrading"

#: SMARD's own history floor for these modules.
HISTORY_FROM_MS = 1656626400000  # 2022-07-01 CEST


def _de_float(raw: str) -> float | None:
    """'244.209.025,99' → 244209025.99; '-' → None (month not yet published)."""
    raw = raw.strip().strip('"')
    if raw in ("", "-"):
        return None
    return float(raw.replace(".", "").replace(",", "."))


def parse_costs_csv(text: str) -> list[tuple[int, float | None, float | None]]:
    """[(month_start_epoch_utc, security_eur, countertrading_eur)] from the
    download CSV. Header order is taken from the header line, not assumed."""
    lines = [ln for ln in text.lstrip("﻿").splitlines() if ln.strip()]
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split(";")]
    try:
        i_from = next(i for i, h in enumerate(header) if h.startswith("Datum von"))
        i_sec = next(i for i, h in enumerate(header) if "Netzsicherheit" in h)
        i_ct = next(i for i, h in enumerate(header) if "Countertrading" in h)
    except StopIteration as exc:
        raise ValueError(f"SMARD congestion CSV: unexpected header {header}") from exc
    out = []
    for ln in lines[1:]:
        cols = ln.split(";")
        try:
            day, month, year = cols[i_from].strip().split(".")
            ts = int(datetime(int(year), int(month), int(day), tzinfo=UTC).timestamp())
        except (ValueError, IndexError):
            continue
        out.append((ts, _de_float(cols[i_sec]), _de_float(cols[i_ct])))
    return out


async def ingest_congestion_costs(db: Session, *, overwrite: bool = False) -> dict:
    """Fetch the full monthly history (one small CSV) and upsert both series.
    Cached per calendar month of FETCH (evidence trail); the weekly job passes
    overwrite=True so a restated month is re-read."""
    now = datetime.now(UTC)

    async def _do() -> dict:
        body = {"request_form": [{
            "format": "CSV",
            "moduleIds": [MODULE_SECURITY, MODULE_COUNTERTRADING],
            "region": "DE",
            "timestamp_from": HISTORY_FROM_MS,
            "timestamp_to": int(now.timestamp() * 1000),
            "type": "discrete",
            "language": "de",
            "resolution": "month",
        }]}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(DOWNLOAD_URL, json=body,
                                     headers={"User-Agent": "obsyd.dev collector"})
            resp.raise_for_status()
            return {"csv": resp.text}

    payload = await raw_cache.fetch_or_cache(
        "smard", f"congestion-costs-{now:%Y-%m}", now.date(), _do, overwrite=overwrite
    )
    rows = parse_costs_csv(payload.get("csv", ""))
    security = [(ts, v) for ts, v, _ in rows if v is not None]
    counter = [(ts, v) for ts, _, v in rows if v is not None]
    written = 0
    if security:
        written += upsert_hourly(db, SERIES_SECURITY, ZONE, security, unit="EUR")
    if counter:
        written += upsert_hourly(db, SERIES_COUNTERTRADING, ZONE, counter, unit="EUR")
    logger.info("smard congestion costs: %d points (%d months published)", written, len(security))
    return {"points": written, "months": len(security)}
