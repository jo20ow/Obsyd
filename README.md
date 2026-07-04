# OBSYD — the European electricity desk (a free "gridstatus for Europe")

**One desk for the European power grid: day-ahead prices, load & residual load, generation mix, wind/solar and cross-border flows for DE-LU, FR and NL — plus tomorrow's load & residual forecast and the gas that fuels the marginal price — from the official record (ENTSO-E, Fraunhofer Energy-Charts, GIE).**

[Live Demo](https://obsyd.dev) · [Free & open source](#cloud-hosting-or-self-host) · [AGPL-3.0](LICENSE)

OBSYD turns the free, official European power record into one legible, auditable desk — the way [gridstatus.io](https://www.gridstatus.io) does for US ISOs, but for Europe. An all-zones overview leads to per-zone detail: day-ahead price (with negative-price flags), residual load & Dunkelflaute, generation mix, spark spread, 20 cross-border flows, and the day-ahead load/residual **forecast**. A live anomaly radar flags negative prices, Dunkelflaute, day-ahead spikes and gas-balance moves the moment they deviate from each zone's own history — every number arriving with a plain-language "what this means" and a "vs normal" reference. Every rule and threshold runs in code you can read on GitHub. No black-box ML, no proprietary scoring, no "trust us".

OBSYD is **not** a Montel, EPEX or Bloomberg replacement — no intraday or settlement-grade pricing. And it is deliberately **power-only**: non-power commodities (oil flows, shipping/AIS, metals) live in a separate sibling project. It is a way to stop wiring up a dozen ENTSO-E queries by hand and read the European power situation from the official record, with the signal code open for audit.

![OBSYD Dashboard](docs/screenshot.png)

## Cloud hosting or self-host

OBSYD is open source under AGPL-3.0 and **completely free** — there is no paid tier. Two ways to use it:

- **Self-host (free):** Clone the repo, plug in your own API keys, run on your own infra. Full feature set, no usage limits.
- **Cloud (free):** Use the hosted version at [obsyd.dev](https://obsyd.dev). The full energy desk, chokepoint map, anomaly radar, and market data need no account. Personal features (your watchlist, custom alerts, daily brief) just need a free magic-link login — no card, no payment.

## Features

- **9 European bidding zones** — DE-LU, FR, NL, BE, AT, ES, PT, PL, CZ (config-only to extend toward all ~27 ENTSO-E zones)
- **Hourly resolution, 5 years of history** — day-ahead price, actual load, generation by fuel, residual load, and forecasts, per zone (~7M points and growing)
- **Near-real-time** — actual load/generation/flows refresh every 30 min; today fills in hour by hour (the honest ~1h ceiling of free ENTSO-E data)
- **Day-ahead forecasts** — load, wind/solar and residual-load forecast vs actual, incl. tomorrow's hourly residual curve
- **Cross-border flows** — physical net flows between zones (Fraunhofer Energy-Charts, CC BY 4.0)
- **Gas fuel side** — EU storage (AGSI), LNG send-out (ALSI), ENTSOG flows, power-burn, demand model and the residual balance signal
- **Spark spread** — CCGT margin per zone (power − gas × heat-rate)
- **Anomaly radar** — negative prices, Dunkelflaute and gas-balance moves flagged vs each zone's own history (descriptive, not a forecast)
- **Europe map** — bidding-zone choropleth by day-ahead price or grid state
- **Public data API** — `GET /api/v1/series` (JSON/CSV export), catalog, meta and an honest coverage/status endpoint — see [docs/API.md](docs/API.md)
- **Interactive series explorer** — query any series/zone/range in the browser and download the exact query as CSV

## Public data API

A free, versioned HTTP API over the canonical power record: `GET /api/v1/series?series=&zone=&start=&end=&resolution=&format=json|csv`, plus `/api/v1/{catalog,meta,status}`. Interactive docs at `/api/docs`. Full reference: **[docs/API.md](docs/API.md)**.

## Roadmap / deferred

Investigated and deliberately deferred (with their blockers), not silently dropped:

- **Imbalance / balancing prices (ENTSO-E A85)** — returned as a ZIP archive and keyed by control area, not bidding zone (Germany has 4 control areas); needs a control-area↔zone mapping layer + long/short 15-min handling.
- **Installed capacity, curtailment, reserves, interconnector nominations** — additional ENTSO-E doctypes; capacity is annual (a different shape from the hourly store).
- **Parquet export** and a **pip-installable Python client** — CSV + JSON cover most needs today; these are additive follow-ups.
- **Continuous intraday prices** — no clean, free, redistributable source.

## Tech Stack

**Backend:** FastAPI · SQLAlchemy · SQLite (WAL mode) · APScheduler · Python 3.11+
**Frontend:** React 19 · Vite · Tailwind CSS 4 · deck.gl · Recharts · Lightweight Charts
**Deployment:** Ubuntu 24.04 · systemd · a reverse proxy for TLS. Self-host: the included `deploy/setup-vps.sh` provisions a standalone nginx + Let's Encrypt (certbot). Hosted obsyd.dev runs behind Caddy (shared with another app) via `deploy/install-caddy-integration.sh` — use whichever proxy you prefer.

## Screenshots

> Screenshots coming soon. Visit [obsyd.dev](https://obsyd.dev) for the live demo.

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

| Source | Data | Update Frequency |
|--------|------|-----------------|
| [AISStream](https://aisstream.io/) | Real-time AIS vessel positions via WebSocket | Real-time |
| [AISHub](https://www.aishub.net/) | Global AIS positions (contributor network) | Every 60s |
| [IMF PortWatch](https://portwatch.imf.org/) | Chokepoint transit counts, disruption events | Daily (3-5 day lag) |
| [EIA](https://www.eia.gov/) | Crude inventories, refinery utilization, SPR, prices | Weekly |
| [FRED](https://fred.stlouisfed.org/) | Historical oil prices, DXY, yields, macro data | Daily |
| [yfinance](https://github.com/ranaroussi/yfinance) | Live commodity futures (CL=F, BZ=F, NG=F, GC=F) | ~15 min delay |
| [GDELT](https://www.gdeltproject.org/) | News volume and tone for energy keywords | Every 2h |
| [Finnhub](https://finnhub.io/) | Financial and energy news headlines | Every 2h |
| [NOAA NWS](https://www.weather.gov/) | Hurricane and tropical storm alerts (Gulf Coast) | Every 30 min |
| [Open-Meteo](https://open-meteo.com/) | Marine conditions (wave height, wind) per zone | Every 30 min |
| [JODI](https://www.jodidata.org/) | World oil production by country | Monthly |
| [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/) | VIIRS thermal hotspots near refineries | Every 6h |
| [Alpha Vantage](https://www.alphavantage.co/) | Commodity price fallback | On demand |
| [Copernicus/Sentinel-1](https://dataspace.copernicus.eu/) | SAR backscatter for Cushing tank farm (experimental) | ~12 day revisit |

## Architecture

FastAPI backend with APScheduler running 20+ periodic data collection jobs. Dual-AIS ingestion pipeline: AISStream delivers real-time zone tracking via WebSocket, while AISHub provides a global vessel snapshot every 60 seconds as fallback. All data is stored in SQLite with WAL mode enabled for concurrent reads (busy_timeout=30s). The signal engine runs heuristic rule checks every 5 minutes against current database state to generate alerts.

The React frontend uses deck.gl for GPU-accelerated map rendering of 40,000+ vessel positions, with Lightweight Charts for OHLCV price data and Recharts for time series visualizations. The UI follows a monospace terminal aesthetic.

## Known Limitations

- **No satellite AIS** — Terrestrial receivers only; vessels beyond ~50 km from coast are invisible. Suez and Panama have limited coverage.
- **Vessel counts, not barrels** — Chokepoint data shows ship transits, not cargo volume or oil flow estimates.
- **yfinance is unofficial** — Live prices may lag or temporarily fail; FRED daily prices serve as historical fallback.
- **PortWatch publication delay** — IMF publishes transit data with a 3-5 day lag from actual transits.
- **SQLite single-writer** — Sufficient for moderate traffic; not suitable for high-concurrency deployments.
- **AIS is self-reported** — Vessels can spoof position, type, or disable transponders entirely. Data is unverified.
- **SAR index is experimental** — Sentinel-1 backscatter correlation with Cushing inventory levels is unvalidated.

## License

[AGPL-3.0-or-later](LICENSE). Self-hosting is encouraged. If you offer OBSYD (or a modified version) as a service over a network, AGPL §13 requires you to publish your source changes — this protects the project from closed-source forks of the hosted offering.

## Disclaimer

OBSYD is an information tool for market observation, not financial advice. All data is aggregated from public sources and provided as-is without warranty. Correlations shown are statistical observations, not causal predictions. AIS data represents aggregated global network positions, not satellite telemetry. Not regulated by BaFin, SEC, or any financial authority.

---

Built with [Claude Code](https://claude.ai/claude-code).
