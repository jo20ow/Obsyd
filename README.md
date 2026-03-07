# OBSYD

**Open-source energy market intelligence. Correlates global ship movements with oil prices.**

**Live:** [https://obsyd.dev](https://obsyd.dev)

![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-3.11+-blue)
![React](https://img.shields.io/badge/react-18-61DAFB)

<!-- screenshot: replace with actual hero image -->
<!-- ![OBSYD Dashboard](docs/screenshot.png) -->

---

## What It Does

OBSYD pulls data from 14 sources and correlates physical oil flows with market prices in real time. Built for analysts, researchers, and anyone curious about how tanker movements, chokepoint disruptions, and geopolitical events move crude oil markets.

- **Track tankers** across 6 energy chokepoints (Hormuz, Suez, Malacca, Panama, Cape, Houston)
- **Detect STS transfers** at known ship-to-ship hotspots (Laconian Gulf, Fujairah, Malaysia EOPL, Lome, Kalamata)
- **Spot dark vessels** — tankers that go silent (AIS gap > 48h)
- **Correlate** chokepoint traffic with Brent price movements using historical disruption data
- **Monitor** crude inventories, refinery utilization, SPR levels, macro indicators
- **Score** geopolitical sentiment from 500+ news sources via GDELT + Finnhub

## Features

### Compact View
A single-screen briefing with anomaly alerts, market snapshot, rerouting index, and top headlines. One click to expand into the full dashboard.

### Full Dashboard

| Panel | Description |
|-------|-------------|
| **Vessel Map** | Interactive deck.gl map — geofence zones, STS hotspots, thermal overlays |
| **Chokepoint Monitor** | 5 chokepoints with transit history charts, PortWatch + AIS data, Brent overlay |
| **STS / Dark Activity** | Ship-to-ship transfer candidates, proximity pairs (< 500m), dark vessel tracking |
| **Price Chart** | WTI/Brent candle + line charts across multiple timeframes |
| **Market Structure** | Contango/backwardation detection with futures curve spreads |
| **Fundamentals** | Refinery utilization gauge, SPR level, crude trade balance |
| **JODI** | Top-5 producer output (KSA, RUS, USA, IRQ, CAN) |
| **Macro** | DXY, 10Y/2Y yields, Fed Funds rate |
| **Sentiment** | GDELT news volume + tone, Finnhub headlines, risk score (1-10) |
| **Correlation** | Chokepoint flow vs. Brent price — Pearson r, lag optimization, impact estimates |
| **Rerouting Index** | Cape vs. Suez routing ratio — detects Red Sea avoidance patterns |
| **Event Timeline** | Historical disruption events with Brent price impact |
| **Morning Briefing** | AI-generated daily summary with anomaly detection and historical context |
| **Alerts** | Unified signal feed — weather, flow, thermal, chokepoint, STS alerts |

### 14 Data Sources

| Source | Data | Frequency |
|--------|------|-----------|
| **EIA** | Crude prices, inventories, refinery util, imports/exports, SPR | Weekly |
| **FRED** | DXY, Fed Funds, 10Y/2Y yields, historical oil prices | Daily |
| **yfinance** | Live WTI/Brent prices, intraday data | 15 min |
| **AISStream** | Real-time satellite AIS via WebSocket | Real-time |
| **AISHub** | Global AIS vessel positions (HTTP polling) | Every minute |
| **IMF PortWatch** | Chokepoint transit counts, trade disruption events | Daily |
| **GDELT** | News volume + tone for energy keywords | 15 min |
| **Finnhub** | Financial news headlines | 30 min |
| **NOAA** | Hurricane/weather alerts for Gulf Coast infrastructure | 30 min |
| **JODI** | World oil production by country | Monthly |
| **NASA FIRMS** | Satellite thermal hotspots near refineries (VIIRS) | 6 hours |
| **MarineTraffic** | Ship metadata enrichment (class, DWT, flag) | On demand |
| **Twelve Data** | Live commodity price fallback | On demand |
| **Alpha Vantage** | Live commodity price fallback | On demand |

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+

### 1. Clone & Backend

```bash
git clone https://github.com/jo20ow/obsyd.git
cd obsyd

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Add your API keys (all optional — dashboard works with partial data)

uvicorn backend.main:app --reload
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` — the dev server proxies API calls to port 8000.

### 3. Production

```bash
# Build frontend
cd frontend && npm run build

# On your VPS (Ubuntu 24.04)
sudo bash deploy/setup-vps.sh
sudo systemctl start obsyd
```

See `deploy/` for systemd service and nginx configuration.

## API Keys

All keys are optional. Add to `.env`:

| Key | Source | Cost |
|-----|--------|------|
| `EIA_API_KEY` | [eia.gov](https://www.eia.gov/opendata/register.php) | Free |
| `FRED_API_KEY` | [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) | Free |
| `AISHUB_API_KEY` | [aishub.net](https://www.aishub.net/) | Free tier |
| `AISSTREAM_API_KEY` | [aisstream.io](https://aisstream.io/) | Free tier |
| `FIRMS_API_KEY` | [firms.modaps.eosdis.nasa.gov](https://firms.modaps.eosdis.nasa.gov/api/area/) | Free |
| `FINNHUB_API_KEY` | [finnhub.io](https://finnhub.io/) | Free tier |
| `ALPHA_VANTAGE_API_KEY` | [alphavantage.co](https://www.alphavantage.co/support/#api-key) | Free tier |

## Built With

- **Backend:** Python, FastAPI, SQLAlchemy, SQLite, APScheduler
- **Frontend:** React 18, Vite, Tailwind CSS 4, deck.gl, Recharts
- **Data:** EIA, FRED, AISStream, AISHub, IMF PortWatch, GDELT, NASA FIRMS, NOAA, JODI, Finnhub
- **Deployment:** Ubuntu 24.04, nginx, systemd, Let's Encrypt

## Project Structure

```
backend/
  collectors/     14 data collectors with APScheduler
  signals/        Alert engine, correlation, sentiment scorer, STS detection
  geofences/      6 chokepoint zones + 5 STS hotspots
  routes/         REST API endpoints
  models/         SQLAlchemy 2.0 models

frontend/
  src/components/ 18 React components (deck.gl map, charts, panels)

deploy/           systemd + nginx configs
```

## License

[MIT](LICENSE)

---

OBSYD is an open-source market observation tool. It does not provide investment advice, trading signals, or recommendations. All data is provided as-is for informational purposes only. Past correlations do not indicate future results. Not regulated by BaFin or any financial authority.
