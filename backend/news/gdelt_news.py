"""Cross-asset news reader — free English headlines via the GDELT DOC 2.0 API.

GDELT is free/no-key and already used elsewhere in the app (sentiment collector).
Here we use its article-list mode to power a per-topic / free-text news feed for the
terminal. Curated topics map to GDELT queries; results are normalised, deduped,
cached, and fail-soft. Descriptive aggregation of public news — not our own reporting.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from backend.collectors.gdelt_gate import gdelt_get

logger = logging.getLogger(__name__)

_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

# Curated cross-asset topics: (key, label, GDELT query). English filter added at fetch.
TOPICS: list[tuple[str, str, str]] = [
    ("markets", "Markets", '("financial markets" OR "stock market" OR "bond market")'),
    ("energy", "Energy & Power", '("power prices" OR "electricity market" OR "energy crisis")'),
    ("gas", "Gas & LNG", '("natural gas" OR LNG OR "gas prices")'),
    ("oil", "Oil", '("crude oil" OR OPEC OR "Brent crude")'),
    ("crypto", "Crypto", '(bitcoin OR ethereum OR "crypto market")'),
    ("rates", "Rates & Fed", '("Federal Reserve" OR "interest rates" OR "Treasury yields" OR "central bank")'),
    ("macro", "Macro", '(inflation OR "jobs report" OR recession OR "gross domestic product")'),
]
_TOPIC_QUERY = {k: q for k, _label, q in TOPICS}

_cache: dict[str, tuple[float, list]] = {}
_TTL = 20 * 60  # 20 min

_inflight: set[str] = set()  # keys currently being warmed (dedup concurrent warms)
_warm_tasks: set = set()  # strong refs to fire-and-forget warm tasks


def _iso(seendate: str | None) -> str | None:
    """GDELT 'YYYYMMDDTHHMMSSZ' → ISO 'YYYY-MM-DDTHH:MM:SSZ'."""
    if not seendate or len(seendate) < 15:
        return None
    s = seendate
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}T{s[9:11]}:{s[11:13]}:{s[13:15]}Z"


def parse_articles(raw: list[dict], limit: int = 25) -> list[dict]:
    """GDELT articles → [{title, url, source, published}], deduped by URL, order preserved."""
    seen = set()
    out = []
    for a in raw or []:
        url = a.get("url")
        title = (a.get("title") or "").strip()
        if not url or not title or url in seen:
            continue
        seen.add(url)
        out.append({
            "title": title,
            "url": url,
            "source": a.get("domain") or "",
            "published": _iso(a.get("seendate")),
        })
        if len(out) >= limit:
            break
    return out


async def _fetch(query: str, max_records: int, timespan: str) -> list[dict]:
    """Fetch raw GDELT articles through the shared rate-limit gate (see gdelt_gate)."""
    params = {
        "query": f"{query} sourcelang:english",
        "mode": "artlist",
        "format": "json",
        "maxrecords": str(max_records),
        "sort": "datedesc",
        "timespan": timespan,
    }
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "Obsyd/1.0 (+https://obsyd.dev)"}) as client:
        resp = await gdelt_get(client, _URL, params)
        resp.raise_for_status()
        return resp.json().get("articles", [])


async def get_feed(query: str, *, max_records: int = 25, timespan: str = "3d") -> list[dict]:
    """News feed for a GDELT query. Serves fresh cache instantly; else fetches through
    the shared GDELT gate. NEVER caches an empty/failed result — a transient 429 must
    not poison the feed — and serves stale data over nothing."""
    now = time.monotonic()
    ckey = f"{query}|{timespan}|{max_records}"
    cached = _cache.get(ckey)
    if cached and now - cached[0] < _TTL:
        return cached[1]
    try:
        raw = await _fetch(query, max_records, timespan)
    except Exception as exc:  # noqa: BLE001 — feed must never crash the route
        logger.warning("news: GDELT fetch failed: %s", exc)
        return cached[1] if cached else []
    data = parse_articles(raw, limit=max_records)
    if data:
        _cache[ckey] = (time.monotonic(), data)  # cache successes only
        return data
    return cached[1] if cached else []  # keep serving stale rather than empty


def _ckey(query: str, timespan: str, max_records: int) -> str:
    return f"{query}|{timespan}|{max_records}"


def get_cached(query: str, *, max_records: int = 25, timespan: str = "3d") -> list[dict] | None:
    """Return the cached feed (fresh OR stale) for a query without any network.
    None means it has never been fetched successfully. The request path uses this so
    a user never blocks on GDELT — warming happens in the background."""
    cached = _cache.get(_ckey(query, timespan, max_records))
    return cached[1] if cached else None


async def warm(query: str, *, max_records: int = 25, timespan: str = "3d") -> None:
    """Background: fetch + cache one query, deduped so concurrent warmers don't stack."""
    key = _ckey(query, timespan, max_records)
    if key in _inflight:
        return
    _inflight.add(key)
    try:
        await get_feed(query, max_records=max_records, timespan=timespan)
    finally:
        _inflight.discard(key)


def schedule_warm(query: str, *, max_records: int = 25, timespan: str = "3d") -> None:
    """Fire-and-forget background warm of one query (ref-held, deduped via warm())."""
    task = asyncio.create_task(warm(query, max_records=max_records, timespan=timespan))
    _warm_tasks.add(task)
    task.add_done_callback(_warm_tasks.discard)


async def refresh_all_topics() -> int:
    """Pre-warm every curated topic into the cache (rate-limited). Background/scheduler."""
    n = 0
    for _k, _label, q in TOPICS:
        arts = await get_feed(q)
        if arts:
            n += 1
    logger.info("news: prewarmed %d/%d topics", n, len(TOPICS))
    return n


def query_for_topic(topic: str) -> str | None:
    return _TOPIC_QUERY.get(topic)
