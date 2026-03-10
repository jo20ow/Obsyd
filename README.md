# OBSYD — Open-Source Energy Market Intelligence

**Track global oil tanker movements, chokepoint flows, and commodity prices in one dashboard.**

[Live Demo](https://obsyd.dev) | [MIT License](LICENSE)

OBSYD aggregates free, public data sources into a single energy market intelligence platform. It tracks real-time vessel positions at six global chokepoints, detects floating storage and ship-to-ship transfers, and correlates physical oil flows with commodity prices. Built as an open-source alternative to Bloomberg Terminal or Kpler for retail commodity traders and independent energy analysts.

![OBSYD Dashboard](docs/screenshot.png)

## Features

- **Live Tanker Tracking** — Real-time AIS positions on a deck.gl globe with 6 geofence zones (Hormuz, Suez, Malacca, Panama, Cape, Houston)
- **Dual AIS Feed** — AISStream WebSocket (primary) + AISHub HTTP polling (fallback) with automatic failover
- **Chokepoint Monitor** — IMF PortWatch transit counts with historical averages, anomaly detection, and Brent price overlay
- **Floating Storage Detection** — Identifies tankers stationary for 7+ days (potential floating storage plays)
- **Voyage Detection** — Zone-to-zone transit tracking with flow matrix visualization
- **Vessel Enrichment** — Ship class classification (VLCC/Suezmax/Aframax) and DWT estimation from AIS dimensions
- **STS Detection** — Ship-to-ship transfer candidates in 5 known hotspots, proximity pairs, and dark vessel tracking
- **Commodity Prices** — WTI, Brent, Natural Gas, TTF, Gold, Silver, Copper with intraday OHLCV charts
- **Market Structure** — Contango/backwardation detection with futures curve spread analysis
- **Correlation Engine** — Chokepoint traffic vs. Brent price (Pearson r with lag optimization up to 7 days)
- **Rerouting Index** — Cape vs. Suez traffic ratio to detect Red Sea/Suez disruption patterns
- **Signal Alerts** — Automated rule-based alerts for flow anomalies, anchored vessel surges, Cushing drawdowns, crack spread extremes
- **Morning Briefing** — Daily anomaly summary with historical Brent price impact context
- **EIA Fundamentals** — Crude inventories, refinery utilization, SPR levels, imports/exports
- **Experimental: SAR Backscatter** — Sentinel-1 synthetic aperture radar index for Cushing tank farm monitoring

## Tech Stack

**Backend:** FastAPI · SQLAlchemy · SQLite (WAL mode) · APScheduler · Python 3.11+
**Frontend:** React 19 · Vite · Tailwind CSS 4 · deck.gl · Recharts · Lightweight Charts
**Deployment:** Ubuntu 24.04 · nginx · systemd · Let's Encrypt

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

All keys are free. The dashboard works with partial data — each missing key disables only its data source.

| Environment Variable | Source | Required | Notes |
|---------------------|--------|----------|-------|
| `AISSTREAM_API_KEY` | [aisstream.io](https://aisstream.io/) | **Yes** | Real-time AIS WebSocket feed |
| `EIA_API_KEY` | [eia.gov](https://www.eia.gov/opendata/register.php) | **Yes** | US energy inventories and prices |
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

[MIT](LICENSE)

## Disclaimer

OBSYD is an information tool for market observation, not financial advice. All data is aggregated from public sources and provided as-is without warranty. Correlations shown are statistical observations, not causal predictions. AIS data represents aggregated global network positions, not satellite telemetry. Not regulated by BaFin, SEC, or any financial authority.

---

Built with [Claude Code](https://claude.ai/claude-code).
