# OBSYD Status Report

**Generated:** 2026-03-06 16:40 UTC
**VPS:** 72.61.190.129 (4GB RAM, Ubuntu 24.04)
**Commit:** ae84f17 `fix: handle Twelve Data single-symbol response format`
**Frontend:** http://72.61.190.129 (HTTP only, no SSL)

---

## 1. Infrastructure

| Component | Status | Details |
|-----------|--------|---------|
| Backend (uvicorn) | RUNNING | PID 114182, 102MB RAM, 1 worker, systemd auto-restart |
| Nginx (reverse proxy) | RUNNING | Port 80, static dist/ + API proxy to :8000 |
| Frontend (React SPA) | DEPLOYED | Built 2026-03-06 11:08 UTC (stale — before Gold fix) |
| SQLite (obsyd.db) | OK | 26MB, WAL mode, busy_timeout=30s |
| SQLite (portwatch.db) | OK | 6.7MB |
| APScheduler | RUNNING | 14 scheduled jobs |
| UFW Firewall | ACTIVE | Ports 22, 80, 443 |

### API Endpoints (40 registered, 40 respond)

```
GET  /health                              GET  /api/health/collectors
GET  /api/prices/eia                      POST /api/prices/eia/collect
GET  /api/prices/eia/series               GET  /api/prices/eia/fundamentals
GET  /api/prices/fred                     POST /api/prices/fred/collect
GET  /api/prices/fred/series              GET  /api/prices/oil
GET  /api/prices/live                     GET  /api/prices/commodities
GET  /api/prices/intraday                 GET  /api/vessels/positions
GET  /api/vessels/global                  GET  /api/vessels/geofence-events
GET  /api/vessels/zones                   GET  /api/sentiment/headlines
GET  /api/sentiment/risk                  GET  /api/sentiment/volume
GET  /api/sentiment/status                GET  /api/portwatch/chokepoints
GET  /api/portwatch/disruptions           GET  /api/portwatch/summary
GET  /api/portwatch/chokepoints/{name}/history
GET  /api/alerts                          GET  /api/alerts/portwatch
GET  /api/ports/activity                  GET  /api/ports/summary
GET  /api/jodi/production                 GET  /api/jodi/summary
POST /api/jodi/collect                    GET  /api/thermal/hotspots
GET  /api/thermal/refineries              POST /api/thermal/collect
GET  /api/weather/alerts                  GET  /api/weather/marine
GET  /api/signals/correlation             GET  /api/settings
GET  /api/settings/credits                POST /api/settings/provider
```

### .env Keys Configured

EIA, FRED, ALPHA_VANTAGE, TWELVEDATA, AISSTREAM, AISHUB, FIRMS, OPENAI, ANTHROPIC, FINNHUB (unused)

---

## 2. Database Tables

### obsyd.db (26MB)

| Table | Rows | Latest Data | Notes |
|-------|------|-------------|-------|
| vessel_positions | 164,936 | 2026-03-06 16:34 | Live AIS via AISStream, ~31h rolling window |
| global_vessel_positions | 39,393 | -- | AISHub global snapshot, replaced each minute |
| eia_prices | 416 | 2026-02-27 | 8 series x 52 weeks each |
| fred_series | 2,816 | 2026-03-05 | 8 series (DXY, yields, CPI, oil daily) |
| gdelt_volume | 240 | 2026-03-06 16:10 | 6 keywords, actively collecting |
| port_activity | 85 | -- | PortWatch port-level data |
| jodi_production | 59 | -- | Monthly JODI production by country |
| alerts | 14 | -- | All severity=warning |
| geofence_events | 8 | 2026-03-06 | 4 zones x 2 days |
| disruptions | 21 | -- | PortWatch disruption events |
| sentiment_scores | 1 | -- | Single computed score |
| thermal_hotspots | **0** | -- | FIRMS collector returns nothing |
| weather_alerts | **0** | -- | NOAA collector returns nothing |

### portwatch.db (6.7MB)

| Table | Rows | Date Range | Notes |
|-------|------|------------|-------|
| chokepoint_daily | 73,276 | 2019-01-01 to 2026-03-01 | 28 chokepoints, ~2617 days each |
| disruptions | 21 | -- | Duplicate of obsyd.db |
| oil_prices | 699 | -- | Legacy FRED cache, unused |
| port_daily | **0** | -- | Never populated |

### Data Freshness Detail

**EIA (8 series, all latest = 2026-02-27):**
PET.RWTC.W (WTI), PET.RBRTE.W (Brent), NG.RNGWHHD.W (Henry Hub),
PET.WCSSTUS1.W (Cushing), PET.WPULEUS3.W (Refinery Util),
PET.WCRIMUS2.W (Imports), PET.WCREXUS2.W (Exports), PET.WCSSTUS1.W.SPR (SPR)

**FRED (8 series):**
| Series | Latest | Count |
|--------|--------|-------|
| T10Y2Y (Yield Curve) | 2026-03-05 | 347 |
| DGS10 (10Y Treasury) | 2026-03-04 | 347 |
| DGS2 (2Y Treasury) | 2026-03-04 | 347 |
| DCOILWTICO (WTI Daily) | 2026-03-02 | 345 |
| DCOILBRENTEU (Brent Daily) | 2026-03-02 | 354 |
| DTWEXBGS (Dollar Index) | 2026-02-27 | 347 |
| FEDFUNDS (Fed Funds) | 2026-02-01 | 365 |
| CPIAUCSL (CPI) | 2026-01-01 | 364 |

**GDELT Volume (6 keywords):**
| Keyword | Latest | Count |
|---------|--------|-------|
| LNG | 2026-03-06 16:10 | 54 |
| oil price | 2026-03-06 15:35 | 48 |
| oil supply disruption | 2026-03-06 15:51 | 48 |
| OPEC | 2026-03-06 15:35 | 48 |
| refinery shutdown | 2026-03-06 15:30 | 18 |
| Suez Canal | 2026-03-05 14:08 | 24 |

**Geofence Events (4 active zones):**
cape, hormuz, houston, malacca — all latest 2026-03-06

**PortWatch:** 28 chokepoints, 2617 days each, data through 2026-03-01

---

## 3. Data Sources — Real Status

### Price Data

| Source | Commodities | Real Price? | Status | Problem |
|--------|------------|-------------|--------|---------|
| **Alpha Vantage** | WTI, Brent, NG, Copper | YES ($/bbl, $/MMBtu, $/mt) | RATE LIMITED | 25 calls/day exhausted within 20 minutes of restart |
| **FRED** (fallback) | WTI, Brent only | YES ($/bbl) | WORKING | No NG, no Copper |
| **Twelve Data** | Gold (XAU/USD) | YES ($/oz) | WORKING | 4 credits used today of 800 |
| **TD Intraday** | USO, BNO, UNG, COPX | **NO — ETF proxies** | WORKING | Y-axis shows ETF price, NOT commodity price |
| **EIA** (weekly) | WTI, Brent, NG, Cushing, SPR, etc. | YES | WORKING | Weekly granularity only |

**What the VPS actually returns right now:**
```json
{
  "WTI":   {"current": 71.13, "source": "FRED",       "date": "2026-03-02"},
  "Brent": {"current": 77.24, "source": "FRED",       "date": "2026-03-02"},
  "Gold":  {"current": 5143.66, "source": "TwelveData", "date": "2026-03-06"},
  "NG":    "MISSING — AV rate-limited, no FRED fallback",
  "Copper": "MISSING — AV rate-limited, no FRED fallback"
}
```

**Intraday WTI example (actually USO ETF):**
```json
{"datetime": "2026-03-06 11:30:00", "close": 105.66}
```
Real WTI is $71.13/bbl. The chart shows $105.66 (USO ETF share price). **This is misleading.**

### Vessel Tracking

| Source | Status | Volume | Notes |
|--------|--------|--------|-------|
| AISStream (WebSocket) | WORKING | 164,936 positions | Real-time, ~31h rolling window |
| AISHub (HTTP) | PARTIAL | 39,393 global | "no coverage" for cape, hormuz, houston, malacca, panama, suez |

### Intelligence Sources

| Source | Status | Notes |
|--------|--------|-------|
| GDELT (volume) | WORKING | 6 keywords tracked every 15min, 240 records |
| GDELT (headlines) | **BROKEN** | 429 Too Many Requests, returns empty arrays |
| PortWatch (IMF) | WORKING | 73K records, 28 chokepoints, excellent historical data |
| NASA FIRMS | **BROKEN** | 0 hotspots ever stored, collector runs but returns nothing |
| NOAA Weather | **BROKEN** | 0 alerts ever stored, collector runs but returns nothing |
| JODI (production) | WORKING | 59 records, monthly |

---

## 4. Git Log (Last 20 Commits)

```
ae84f17 fix: handle Twelve Data single-symbol response format
32cdc30 fix: real commodity prices — AV for energy+copper, TD for gold spot
b4c0e3b fix: hybrid merge always uses real energy prices from AV/FRED
659c889 fix: hybrid price strategy — real commodity prices + TD metals
39c87af fix: Twelve Data symbols — use ETFs + forex for Free Tier compatibility
ec6be31 feat: news headlines feed with 30min cache in SentimentPanel
59e8106 feat: intraday candlestick chart, commodity cards, settings panel
d704a92 feat: price provider abstraction + Twelve Data integration + settings API
f1dc05b docs: README rewrite for MVP, add STATUS_REPORT and frontend README
e00ff9d feat: VPS deployment — systemd service, nginx reverse proxy, setup script
23ae3bd feat: skeleton loading, error states, weather dedup, health status dots
ba922fe feat: health endpoint, signal rules tuning, correlation engine, sentiment scorer
5d8a3c8 refactor: collectors — fix FIRMS/GDELT/portwatch, add scheduler hardening
daeef78 fix: SQLite WAL mode + busy_timeout for production stability
273575e feat: PortWatch integration, chokepoint alerts, correlation engine, sentinel PoC
cfc7347 Update README with full feature documentation and setup guide
35c948a Add NASA FIRMS thermal hotspot integration with refinery anomaly detection
463064b Add global vessel view with toggle between geofence and all-vessels mode
700edf2 Fix GDELT 429 rate-limiting and show AIS coverage gaps in frontend
1b52e60 Fix Suez and Panama bounding boxes (lon_min > lon_max bug)
```

---

## 5. Open Problems (Honest)

### CRITICAL

**1. Alpha Vantage rate limit burns through 25 calls/day in ~20 minutes.**

The scheduler calls `refresh_live_prices()` every 15 minutes. Each call fetches 4 AV commodities (WTI, Brent, NG, Copper) = 4 API calls. The AV in-memory cache TTL is 15 minutes — same as the scheduler interval. But the frontend also triggers `get_live_prices()` on page load and through the `/api/prices/commodities` endpoint. On top of that, the scheduler's `get_live_prices` call races with the 15-min AV cache expiry.

**Result:** 96 AV API calls logged in 25 minutes. The daily limit (25) is exhausted within 2 scheduler cycles. After that, NG and Copper are unavailable for the rest of the day because FRED has no fallback for them.

**Root cause:** `scheduler.py` line 158-163 calls `get_live_prices` every 15 min. `alphavantage.py` CACHE_TTL = 900s (15 min). These are synchronized, so the cache expires right as the scheduler fires. Plus frontend requests add more calls.

**2. Intraday charts show ETF prices labeled as commodities.**

The WTI intraday chart shows USO at ~$105.66. Real WTI is $71.13/bbl. There is zero indication to the user that this is an ETF proxy. The Y-axis, tooltips, and everything else present it as if it were the actual commodity price. Same for BNO (Brent), UNG (Natural Gas), COPX (Copper).

**3. No HTTPS.**

Everything over cleartext HTTP. No domain, no SSL certificate. All API traffic including the settings panel (which can change provider configuration) is unencrypted.

### SERIOUS

**4. NASA FIRMS: 0 hotspots, ever.**

The collector has run every 6 hours since deployment. Zero rows in `thermal_hotspots`. Either the API key is bad, the FIRMS API endpoint changed, or the geographic queries return nothing. Nobody has investigated. The `/api/thermal/hotspots` endpoint returns `[]` always.

**5. NOAA Weather: 0 alerts, ever.**

Same pattern. The collector runs every 30 minutes, zero rows stored. Gulf Coast marine weather alerts — one of the more useful features for energy infrastructure monitoring — are completely absent.

**6. GDELT headlines return empty.**

The DOC 2.0 article search consistently gets 429 rate-limited. The 30min cache was added but the initial fetch after cache expiry fails, so the cache just caches "empty". Headlines section in the frontend shows nothing.

**7. Two obsyd.db files on VPS.**

`/home/obsyd/obsyd/obsyd.db` (26MB, active) and `/home/obsyd/obsyd/data/obsyd.db` (0 bytes, empty). The `data/` directory also has `portwatch.db` and `settings.json`. Confusing — unclear which is the "correct" location.

**8. Frontend dist is stale.**

Built 2026-03-06 11:08, before the Twelve Data Gold parsing fix (ae84f17). The frontend JS itself doesn't need to change for this fix (it's a backend change), but any frontend changes since the last build are not deployed.

### MODERATE

**9. Geofence coverage gaps.**

Only 4 zones produce events: cape, hormuz, houston, malacca. Suez and Panama — arguably the two most important chokepoints — have zero geofence events. This is an AIS coverage limitation (terrestrial AIS doesn't reach mid-ocean chokepoints), but PortWatch data does cover them. The disconnect is confusing.

**10. AISHub "no coverage" for all key zones.**

Logs: "no coverage for cape, hormuz, houston, malacca, panama, suez". AISHub's terrestrial network has gaps in exactly the places that matter most. AISStream partially compensates.

**11. Sentiment system barely functional.**

1 sentiment score ever computed. The risk score endpoint exists and responds, but with essentially no data. The scoring model needs consistent GDELT data (which is rate-limited) and more history.

**12. No monitoring or alerting.**

A cron health-check exists, but it only checks if the process is running. If FIRMS has been broken for weeks, or AV is rate-limited every day, nobody knows unless they manually check logs.

**13. SQLite locking storm on every restart.**

All startup tasks fire simultaneously: portwatch, GDELT, FIRMS, geofence, AIS. They all write to the same SQLite DB. Result: 30-45 seconds of `database is locked` errors. Self-resolving but wastes startup data collection attempts.

**14. vessel_positions growing unbounded.**

164,936 rows after ~31 hours. That's ~5,300 rows/hour, ~127K rows/day, ~46M rows/year. No retention policy. The DB will grow ~2GB/year from this table alone.

---

## 6. What Would Be Sensible Next

### Fix What's Broken (immediate)

1. **Fix AV rate limit burn.** Change AV cache TTL from 15 min to 4 hours (14,400s). Or change the scheduler's `live_price_refresh` from `*/15` to a 4-hour interval. With 4 commodities, 25 calls/day supports 6 refreshes. Schedule at market-relevant times (pre-market, open, midday, close, evening, overnight). TD Gold can keep the 15-min refresh since it only costs 1 credit of 800.

2. **Rebuild and deploy frontend.** `npm run build` → scp dist/ → VPS.

3. **Label intraday charts as ETF proxies.** Add "(USO ETF)" to chart title. Or hide Y-axis absolute prices and show only % change. Or remove intraday entirely until real data is available.

4. **Debug FIRMS collector.** SSH to VPS, run `curl` against the FIRMS API with the configured key, see what comes back. Either fix or disable.

5. **Debug NOAA collector.** Same — manual test of the NOAA API endpoint.

### Short-term (make it reliable)

6. **Add HTTPS.** Get a domain, `certbot --nginx`. This is baseline for anything public-facing.

7. **Fix GDELT headlines.** Implement exponential backoff, or switch to a different news source (NewsAPI, RSS feeds).

8. **Stagger startup collectors.** Add 5-10s delays between `create_task()` calls in `main.py` startup.

9. **Add vessel_positions retention.** Daily cron: `DELETE FROM vessel_positions WHERE timestamp < datetime('now', '-7 days')`. Keep 7 days, not infinity.

10. **Basic uptime monitoring.** External ping to `/health`, alert on failure (UptimeRobot free tier, or a simple cron + email).

### Medium-term (add real value)

11. **Get a premium AV key or switch to FRED-only for energy.** The free tier cannot support 4 commodities refreshed through the day. FRED is free, unlimited, and has real prices — just 1 day delayed. Accept the delay or pay $50/month for AV premium.

12. **Add NG and Copper to FRED fallback.** FRED has `DHHNGSP` (Henry Hub NG daily) and potentially copper series. This would eliminate the AV dependency for energy prices entirely.

13. **Build alert delivery.** The 14 alerts in the DB are useless without notification. Add Telegram bot, email, or webhook delivery.

14. **Consolidate DB paths.** Pick one location, delete the other, update config.

15. **Add smoke tests.** At minimum: hit every endpoint, verify 200 status and non-empty response. Run on deploy.
