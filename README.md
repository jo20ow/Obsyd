# OBSYD

Open-source energy market intelligence dashboard. Real-time AIS vessel tracking, commodity prices, refinery thermal monitoring, chokepoint flow analysis, and geopolitical sentiment — all in one interface.

**Live:** https://obsyd.dev

![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-3.11+-blue)
![React](https://img.shields.io/badge/react-18-61DAFB)

## Features

### 9 Data Sources

| Source | Data | Update Frequency |
|--------|------|------------------|
| **EIA** | Crude prices (WTI/Brent), inventories, refinery utilization, imports/exports, SPR stocks | Weekly (Wed) |
| **FRED** | DXY, Fed Funds Rate, 10Y/2Y yields, yield curve spread | Daily |
| **AISHub** | Global AIS vessel positions, tanker tracking across 6 chokepoint zones | Every minute |
| **AISStream** | Real-time satellite AIS WebSocket feed | Real-time |
| **IMF PortWatch** | Chokepoint transit counts, trade disruption events | Daily backfill |
| **NOAA** | Weather alerts for Gulf Coast energy infrastructure | Every 30 min |
| **GDELT** | News volume + tone for 7 energy keywords, rule-based sentiment risk score | Every 15 min |
| **JODI** | World oil production by country (top producers) | Monthly |
| **NASA FIRMS** | Satellite thermal hotspots near major refineries (VIIRS) | Every 6 hours |

### Map Modes

- **Geofence** — Tanker positions in 6 energy chokepoints (Hormuz, Suez, Malacca, Panama, Cape, Houston)
- **All Vessels** — Full global AIS snapshot with tanker highlighting
- **Thermal** — NASA FIRMS satellite hotspots near refineries (brightness-scaled)

### Signal Alerts

Automated anomaly detection with 6 alert types:

- `STOR` — Floating storage detection (vessels stationary in loading zones, 7-day baseline)
- `FLOW` — Chokepoint flow anomalies (2-sigma or 30% deviation from baseline)
- `CUSH` — Cushing inventory drawdown signals
- `THERM` — Refinery thermal anomalies (missing heat signatures at known refineries)
- `CHOKE` — IMF PortWatch chokepoint traffic anomalies
- `WX` — NOAA weather alerts affecting energy infrastructure

### Correlation Engine

Chokepoint-to-Brent price correlation analysis:
- Pearson r (level + delta) with lag optimization (0-14 days)
- Price impact estimates from historical disruption events (>30% traffic drops)
- Active event tracking with real-time Brent price comparison

### Dashboard Panels

- **Price Chart** — WTI/Brent with EIA inventory overlay
- **Fundamentals** — Refinery utilization gauge, SPR level, crude trade balance
- **JODI** — Top-5 producer output (KSA, RUS, USA, IRQ, CAN)
- **Macro** — DXY, 10Y/2Y yields, Fed Funds rate
- **Sentiment** — GDELT news volume + tone, risk score (1-10)
- **Chokepoint Monitor** — 5 chokepoints with transit history + Brent overlay
- **Correlation** — Chokepoint flow vs Brent price analysis
- **Vessel Map** — Interactive deck.gl map with geofence zones
- **Alerts** — Unified signal feed (weather, flow, thermal, chokepoint alerts)

## Architecture

```
backend/          FastAPI + SQLAlchemy + SQLite
  collectors/     9 data collectors with APScheduler
  signals/        Alert evaluation (every 5 min), correlation engine, sentiment scorer
  geofences/      6 chokepoint bounding boxes
  routes/         23 REST API endpoints
  models/         SQLAlchemy 2.0 models

frontend/         React 18 + Vite + Tailwind CSS 4
  components/     12 components: deck.gl map, Recharts, alert feed, skeleton loading

deploy/           systemd + nginx configs, VPS setup script
```

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+

### Backend

```bash
git clone https://github.com/jo20ow/obsyd.git
cd obsyd

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your API keys (see below)

uvicorn backend.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend runs on `http://localhost:5173` and proxies API calls to the backend at port 8000.

### Production Deployment

```bash
# On your VPS (Ubuntu 24.04):
sudo bash deploy/setup-vps.sh

# As obsyd user:
cd ~/obsyd
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Upload .env, build frontend dist/, copy databases
sudo systemctl start obsyd
```

See `deploy/` for systemd service and nginx config files.

## API Keys

All keys are optional — the dashboard works with partial data. Add keys to `.env`:

| Key | Source | Cost | Required For |
|-----|--------|------|--------------|
| `EIA_API_KEY` | [eia.gov](https://www.eia.gov/opendata/register.php) | Free | Prices, inventories, fundamentals |
| `FRED_API_KEY` | [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) | Free | Macro indicators, oil prices |
| `AISHUB_API_KEY` | [aishub.net](https://www.aishub.net/) | Free tier | Global vessel tracking |
| `AISSTREAM_API_KEY` | [aisstream.io](https://aisstream.io/) | Free tier | Real-time satellite AIS |
| `FIRMS_API_KEY` | [firms.modaps.eosdis.nasa.gov](https://firms.modaps.eosdis.nasa.gov/api/area/) | Free | Thermal hotspot monitoring |
| `ALPHA_VANTAGE_API_KEY` | [alphavantage.co](https://www.alphavantage.co/support/#api-key) | Free tier | Live commodity prices |

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /api/health/collectors` | Data source status (EIA, FRED, AIS, GDELT) |
| `GET /api/prices/eia` | EIA weekly price series |
| `GET /api/prices/eia/fundamentals` | Refinery util, imports, exports, SPR |
| `GET /api/prices/fred` | FRED macro indicators |
| `GET /api/prices/live` | Live oil prices (WTI + Brent) |
| `GET /api/prices/oil` | Historical oil price series |
| `GET /api/vessels/zones` | Geofence zone definitions |
| `GET /api/vessels/positions` | Latest tanker positions per zone |
| `GET /api/vessels/global` | Full global vessel snapshot |
| `GET /api/vessels/geofence-events` | Daily aggregated zone activity |
| `GET /api/alerts` | Signal alerts feed |
| `GET /api/alerts/portwatch` | PortWatch chokepoint anomaly alerts |
| `GET /api/ports/summary` | Port activity summary |
| `GET /api/weather/alerts` | NOAA weather alerts |
| `GET /api/weather/marine` | Marine conditions per zone |
| `GET /api/sentiment/volume` | GDELT news volume by keyword |
| `GET /api/sentiment/risk` | Sentiment risk score (1-10) |
| `GET /api/sentiment/headlines` | Latest energy headlines |
| `GET /api/jodi/summary` | JODI production by country |
| `GET /api/thermal/hotspots` | FIRMS thermal detections |
| `GET /api/portwatch/summary` | Chokepoint transit summary |
| `GET /api/signals/correlation` | Chokepoint-Brent correlation analysis |

## Geofence Zones

| Zone | Coverage | AIS |
|------|----------|-----|
| Hormuz | Strait of Hormuz — 20% of global oil transit | Terrestrial + Satellite |
| Suez | Suez Canal + Bab-el-Mandeb — Red Sea to Med | PortWatch only |
| Malacca | Strait of Malacca — Asian oil import route | Terrestrial + Satellite |
| Panama | Panama Canal — Atlantic/Pacific transit | PortWatch only |
| Cape | Cape of Good Hope — Suez alternative | Terrestrial |
| Houston | Gulf of Mexico — Refineries, LOOP terminal | Terrestrial + Satellite |

## Known Limitations

- **Suez/Panama AIS**: No terrestrial AIS coverage; PortWatch provides transit counts
- **SQLite**: Single-writer, sufficient for MVP (~50 concurrent users)
- **GDELT**: Rate-limited to avoid 429 errors; headlines sometimes empty
- **FIRMS**: Satellite coverage gaps mean "no hotspot" does not equal "no activity"
- **Sentiment**: Rule-based (GDELT tone), not LLM-based (LLM integration optional via BYOK)

## License

MIT
