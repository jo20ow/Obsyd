# OBSYD — the European electricity desk (a free "gridstatus for Europe")

**One desk for the European power grid: day-ahead prices at the market's real 15-minute resolution, load & residual load, generation mix, a live generation-outage board, cross-border flows and reservoir levels across 37 bidding zones — plus forecasts and the gas that fuels the marginal price — from the official record (ENTSO-E, Fraunhofer Energy-Charts, GIE).**

[Live Demo](https://obsyd.dev) · [Free & open source](#cloud-hosting-or-self-host) · [AGPL-3.0](LICENSE)

OBSYD turns the free, official European power record into one legible, auditable desk — the way [gridstatus.io](https://www.gridstatus.io) does for US ISOs, but for Europe. An all-zones overview leads to per-zone detail: day-ahead price (hourly and as-traded 15-minute, with negative-price flags), residual load & Dunkelflaute, generation mix, spark spread, generation outages (revision-aware), cross-border flows, and the day-ahead load/residual **forecast** with its measured error. A live anomaly radar flags forced outages, negative prices, Dunkelflaute and gas-balance moves the moment they deviate from each zone's own history — every number arriving with a plain-language "what this means" and a "vs normal" reference. Every rule and threshold runs in code you can read on GitHub. No black-box ML, no proprietary scoring, no "trust us".

OBSYD is **not** a Montel, EPEX or Bloomberg replacement — no intraday or settlement-grade pricing. And it is deliberately **power-only**: non-power commodities (oil flows, shipping/AIS, metals) live in a separate sibling project. It is a way to stop wiring up a dozen ENTSO-E queries by hand and read the European power situation from the official record, with the signal code open for audit.

![OBSYD Dashboard](docs/screenshot.png)

## Cloud hosting or self-host

OBSYD is open source under AGPL-3.0 and **completely free** — there is no paid tier. Two ways to use it:

- **Self-host (free):** Clone the repo, plug in your own API keys, run on your own infra. Full feature set, no usage limits.
- **Cloud (free):** Use the hosted version at [obsyd.dev](https://obsyd.dev). The full power desk, Europe map, anomaly radar, and data explorer need no account. Personal features (your watchlist, custom alerts, daily brief) just need a free magic-link login — no card, no payment.

## Features

- **~37 European bidding zones** — all 27 EU zones plus non-EU neighbours CH, NO1–5, SE1–4 (config-only via `ENABLED_ZONES`)
- **Hourly resolution, 5 years of history** — day-ahead price, actual load, generation by fuel, residual load, forecasts and imbalance prices, per zone
- **Imbalance prices** — 15-min settlement prices → hourly, per zone (DE-LU included: the combined reBAP is served under the country EIC)
- **Installed capacity** — generation capacity by fuel per zone (ENTSO-E A68), annual context
- **Near-real-time** — actual load/generation/flows refresh every 30 min; today fills in hour by hour (the honest ~1h ceiling of free ENTSO-E data)
- **Day-ahead forecasts** — load, wind/solar and residual-load forecast vs actual, incl. tomorrow's hourly residual curve
- **Cross-border flows** — physical net flows between zones (Fraunhofer Energy-Charts, CC BY 4.0)
- **Gas fuel side** — EU storage (AGSI), LNG send-out (ALSI), ENTSOG flows, power-burn, demand model and the residual balance signal
- **Spark spread** — CCGT margin per zone (power − gas × heat-rate)
- **Anomaly radar** — negative prices, Dunkelflaute and gas-balance moves flagged vs each zone's own history (descriptive, not a forecast)
- **Europe map** — bidding-zone choropleth by day-ahead price or grid state
- **Public data API + Python client** — `GET /api/v1/series` (JSON/CSV/**Parquet** export), catalog, zones, capacity, meta and an honest coverage/status endpoint — see [docs/API.md](docs/API.md); pip-installable client in [clients/python](clients/python)
- **Interactive series explorer** — query any series/zone/range, **compare two zones** on one chart, download as CSV
- **Chart-Builder** (`/builder`) — the series explorer as its own full-screen, shareable-URL page
- **Embeddable widgets + badges** — self-refreshing `/embed/<zone>/<metric>` iframes (price/genmix/load) and `/api/v1/badge` SVG status images for READMEs/dashboards
- **Activated balancing energy** — aFRR/mFRR activation price (and volume, where ENTSO-E serves it) per zone
- **German balancing-capacity prices** — FCR/aFRR/mFRR procured-capacity tenders (DE-LU LFC block)
- **Transmission outages** — cross-border line/PST unavailability (ENTSO-E A78) alongside the generation-outage board (A77)
- **Live today-view** — near-real-time load, generation mix and day-ahead price for the current day, refreshing intraday

## Public data API

A free, versioned HTTP API over the canonical power record: `GET /api/v1/series?series=&zone=&start=&end=&resolution=&format=json|csv|parquet`, plus `/api/v1/{zones,catalog,capacity,meta,status}`. Interactive docs at `/api/docs`. Full reference: **[docs/API.md](docs/API.md)**.

## Roadmap / deferred

Shipped since the first cut: imbalance prices (A85, incl. Germany's reBAP via the country EIC),
installed capacity (A68), hydro reservoirs (A72), the generation-outage board (A77,
revision-aware), hourly cross-border flows, all-time records, Parquet export, the
pip-installable Python client, and the non-EU neighbour zones (CH, NO, SE).

Investigated and deliberately deferred (with their blockers), not silently dropped:

- **Great Britain** — left ENTSO-E's day-ahead publication post-Brexit (A44 returns an Acknowledgement); not addable as a priced zone from the free feed.
- **Curtailment / redispatch / reserves** (ENTSO-E A63/A80/…) — fragmented, border-specific congestion-management data, not a clean pan-EU series.
- **Continuous intraday prices** — no clean, free, redistributable source.
- **`power_hourly` retention** — the canonical store grows unbounded by design (all-time records need all-time history). If disk ever becomes the constraint, the 15-min `.qh` series are the thinning candidates (raw quarter-hours matter most recently; the hourly series keeps the long history) — documented option, deliberately not built.

## Tech Stack

**Backend:** FastAPI · SQLAlchemy · SQLite (WAL mode) · APScheduler · Python 3.11+
**Frontend:** React 19 · Vite · Tailwind CSS 4 · deck.gl · Recharts · Lightweight Charts
**Deployment:** Ubuntu 24.04 · systemd · a reverse proxy for TLS. Self-host: the included `deploy/setup-vps.sh` provisions a standalone nginx + Let's Encrypt (certbot). Hosted obsyd.dev runs behind Caddy (shared with another app) via `deploy/install-caddy-integration.sh` — use whichever proxy you prefer.

## Quick Start

```bash
git clone https://github.com/jo20ow/obsyd.git
cd obsyd

# Backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # Fill in API keys (see table below)

# Frontend
cd frontend && npm install && npm run build && cd ..

# Run
uvicorn backend.main:app
# Open http://localhost:8000
```

## API Keys

All keys are free. **For the electricity+gas desk the two that matter are `ENTSOE_API_TOKEN`
(ENTSO-E day-ahead/load/generation/forecasts — request "Restful API" access at
transparency.entsoe.eu) and `GIE_API_KEY` (AGSI storage + ALSI LNG).** Cross-border flows
(Energy-Charts) need no key. `ENABLED_ZONES` (comma list, e.g. `DE_LU,FR,NL,BE,AT,ES,PT,PL,CZ`)
selects the zones. Also set `SECRET_KEY` and `JWT_SECRET`. The keys below are legacy/optional —
they power the dormant non-power modules being split into a sibling project.

| Environment Variable | Source | Required | Notes |
|---------------------|--------|----------|-------|
| `ENTSOE_API_TOKEN` | [transparency.entsoe.eu](https://transparency.entsoe.eu/) | **Yes (power/gas)** | Day-ahead prices, load, generation, forecasts |
| `GIE_API_KEY` | [gie.eu](https://www.gie.eu/) | **Yes (gas)** | AGSI storage + ALSI LNG (one key, both) |
| `AISSTREAM_API_KEY` | [aisstream.io](https://aisstream.io/) | Legacy | Real-time AIS WebSocket feed (dormant non-power) |
| `EIA_API_KEY` | [eia.gov](https://www.eia.gov/opendata/register.php) | Legacy | US energy inventories and prices (dormant) |
| `FRED_API_KEY` | [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) | **Yes** | Historical oil prices, macro indicators |
| `AISHUB_API_KEY` | [aishub.net](https://www.aishub.net/) | Optional | Fallback AIS source (requires own AIS station) |
| `ALPHA_VANTAGE_API_KEY` | [alphavantage.co](https://www.alphavantage.co/support/#api-key) | Optional | Price fallback provider |
| `FIRMS_API_KEY` | [firms.modaps.eosdis.nasa.gov](https://firms.modaps.eosdis.nasa.gov/api/area/) | Optional | Thermal hotspots near refineries |
| `FINNHUB_API_KEY` | [finnhub.io](https://finnhub.io/) | Optional | Financial news headlines |
| `SECRET_KEY` | Self-generated | **Yes** | App secret for HMAC signatures (64+ random chars) |
| `JWT_SECRET` | Self-generated | **Yes** | JWT signing key (64+ random chars) |

## Data Sources & Attribution

The power desk runs on the official European record:

| Source | Data | Update Frequency |
|--------|------|-----------------|
| [ENTSO-E Transparency](https://transparency.entsoe.eu/) | Day-ahead prices (A44, 15-min since 2025-10), load (A65), generation per fuel (A75), forecasts (A69/A71), imbalance (A85/reBAP), hydro reservoirs (A72), outages (A77/A78), installed capacity (A68), balancing energy/capacity (A83/A84/A15) | 30 min – daily per doc type |
| [Fraunhofer Energy-Charts](https://www.energy-charts.info/) | Cross-border physical flows (CBPF), CC BY 4.0 | 30 min |
| [GIE AGSI+/ALSI](https://agsi.gie.eu/) | EU gas storage and LNG send-out | Daily |
| [ENTSOG](https://transparency.entsog.eu/) | Cross-border gas flows | Daily |
| [Eurostat](https://ec.europa.eu/eurostat) | Gas demand calibration | Monthly |
| [Open-Meteo](https://open-meteo.com/) | Heating-degree-days for the gas demand model | Daily |
| [yfinance](https://github.com/ranaroussi/yfinance) | TTF gas price (spark-spread gas leg) | ~15 min delay |

<details>
<summary><b>Legacy sources (dormant)</b> — power the non-power modules being split into a sibling project; kept for the record, not part of the power desk.</summary>

AISStream / AISHub (vessel AIS), IMF PortWatch (chokepoints), EIA (US oil), FRED (macro/oil), GDELT + Finnhub (news/sentiment), NOAA NWS (Gulf storms), JODI (oil production), NASA FIRMS (refinery thermal), Alpha Vantage (price fallback), Copernicus/Sentinel-1 (experimental SAR). Their known caveats: terrestrial-only AIS (self-reported, spoofable), PortWatch 3–5 day lag, unvalidated SAR index.

</details>

## Architecture

FastAPI backend with APScheduler running the collection jobs (nightly deep ingest, 30-min intraday refresh for load/generation/flows, 2h outage refresh, 5-min anomaly evaluation). Everything lands in a canonical hourly store (`power_hourly`, SQLite WAL, single writer) plus per-domain tables; every raw API payload is disk-cached so recalibrations never re-hit the sources. The anomaly radar runs pure, descriptive detectors against persisted state every 5 minutes.

The React frontend renders the bidding-zone map with deck.gl (real zone geometry, © Electricity Maps contributors), time series with Recharts, and follows a monospace terminal aesthetic. Dormant non-power modules (AIS map, chokepoints, metals, sentiment) remain in the tree for extraction into a sibling project and are lazy-loaded out of the main bundle.

## Known Limitations

- **~1h publication lag** — actual load/generation ride ENTSO-E's free publication cycle; "near-real-time" honestly means the last hour or two fills in as published.
- **A75 coverage varies by zone** — some zones under-report generation (notably NL); renewable-share metrics are suppressed rather than shown when coverage is too low to trust.
- **Energy-Charts flows are country-level** — Nordic/Italian sub-zones have no per-sub-zone border series.
- **yfinance is unofficial** — the TTF leg of the spark spread may lag or temporarily fail.
- **SQLite single-writer** — sufficient for moderate traffic; not suitable for high-concurrency deployments.

## License

[AGPL-3.0-or-later](LICENSE). Self-hosting is encouraged. If you offer OBSYD (or a modified version) as a service over a network, AGPL §13 requires you to publish your source changes — this protects the project from closed-source forks of the hosted offering.

## Disclaimer

OBSYD is an information tool for market observation, not financial advice. All data is aggregated from public sources and provided as-is without warranty. Correlations shown are statistical observations, not causal predictions. AIS data (dormant legacy modules) is self-reported and unverified. Not regulated by BaFin, SEC, or any financial authority.

---

Built with [Claude Code](https://claude.ai/claude-code).
