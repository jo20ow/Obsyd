# OBSYD

Open-source energy market intelligence dashboard. Real-time AIS vessel tracking, commodity prices, refinery thermal monitoring, port congestion, and geopolitical sentiment — all in one interface.

![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-3.11+-blue)
![React](https://img.shields.io/badge/react-18-61DAFB)

## Features

### 10 Data Sources

| Source | Data | Update Frequency |
|--------|------|------------------|
| **EIA** | Crude prices (WTI/Brent), inventories, refinery utilization, imports/exports, SPR stocks | Weekly (Wed) |
| **FRED** | DXY, Fed Funds Rate, breakeven inflation, T-bill spread | Daily |
| **AISHub** | Global AIS vessel positions, tanker tracking across 6 chokepoint zones | Every minute |
| **AISStream** | Real-time AIS WebSocket feed (secondary source) | Real-time |
| **IMF PortWatch** | Port-level trade disruption indices | Weekly (Tue) |
| **NOAA** | Weather alerts for Gulf Coast energy infrastructure | Every 30 min |
| **GDELT** | News volume for energy keywords + sentiment scoring | Every 15 min |
| **JODI** | World oil production, refinery throughput, and stocks (top 10 producers) | Monthly |
| **NASA FIRMS** | Satellite thermal hotspots near major refineries (VIIRS) | Every 6 hours |
| **Finnhub / Alpha Vantage** | Real-time commodity futures (BYOK) | On demand |

### Map Modes

- **Geofence** — Tanker positions in 6 energy chokepoints (Hormuz, Suez, Malacca, Panama, Cape, Houston)
- **All Vessels** — Full global AIS snapshot with tanker highlighting
- **Thermal** — NASA FIRMS satellite hotspots near refineries (brightness-scaled, orange-to-red gradient)

### Signal Alerts

Automated anomaly detection with 5 alert types:

- `STOR` — Floating storage detection (vessels stationary in loading zones)
- `FLOW` — Chokepoint flow anomalies (unusual tanker counts)
- `CUSH` — Cushing inventory drawdown signals
- `THERM` — Refinery thermal anomalies (missing heat signatures at known refineries)
- `WX` — NOAA weather alerts affecting energy infrastructure

### Dashboard Panels

- **Price Chart** — WTI/Brent with EIA inventory overlay
- **Fundamentals** — Refinery utilization gauge, SPR level, crude trade balance
- **JODI** — Top-5 producer output (horizontal bars, Mbd)
- **Macro** — DXY, rates, inflation expectations
- **Sentiment** — GDELT news volume + tone analysis
- **Alerts** — Unified signal feed (weather + anomaly alerts)

## Architecture

```
backend/          FastAPI + SQLAlchemy + SQLite
  collectors/     10 data collectors with APScheduler
  signals/        Alert evaluation engine (runs every 5 min)
  geofences/      6 chokepoint bounding boxes
  routes/         REST API endpoints
  models/         SQLAlchemy 2.0 models

frontend/         React 18 + Vite + TailwindCSS
  components/     deck.gl map, chart panels, alert feed
```

## Setup

### Prerequisites

- Python 3.11+
- Node.js 18+

### Backend

```bash
# Clone
git clone https://github.com/jo20ow/Obsyd.git
cd Obsyd

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure API keys
cp .env.example .env
# Edit .env with your keys (see API Keys section below)

# Start backend (port 8000)
uvicorn backend.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The frontend runs on `http://localhost:5173` and proxies API calls to the backend.

### API Keys

All keys are optional — the dashboard works with partial data. Add keys to `.env` as needed:

| Key | Source | Cost | Required For |
|-----|--------|------|--------------|
| `EIA_API_KEY` | [eia.gov](https://www.eia.gov/opendata/register.php) | Free | Prices, inventories, fundamentals |
| `FRED_API_KEY` | [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) | Free | Macro indicators |
| `AISHUB_API_KEY` | [aishub.net](https://www.aishub.net/) | Free tier | Vessel tracking |
| `AISSTREAM_API_KEY` | [aisstream.io](https://aisstream.io/) | Free tier | Real-time AIS WebSocket |
| `FIRMS_API_KEY` | [firms.modaps.eosdis.nasa.gov](https://firms.modaps.eosdis.nasa.gov/api/area/) | Free | Thermal hotspot monitoring |
| `FINNHUB_API_KEY` | [finnhub.io](https://finnhub.io/) | Free tier | Real-time futures |
| `ALPHA_VANTAGE_API_KEY` | [alphavantage.co](https://www.alphavantage.co/support/#api-key) | Free tier | Commodity prices |
| `OPENAI_API_KEY` | [openai.com](https://platform.openai.com/) | Paid | Sentiment analysis |
| `ANTHROPIC_API_KEY` | [anthropic.com](https://console.anthropic.com/) | Paid | Sentiment analysis |

### Geofence Zones

| Zone | Coverage | AIS Coverage |
|------|----------|--------------|
| Hormuz | Strait of Hormuz — 20% of global oil transit | Yes |
| Suez | Suez Canal + Bab-el-Mandeb — Red Sea to Mediterranean | No (satellite only) |
| Malacca | Strait of Malacca — Asian oil import route | Yes |
| Panama | Panama Canal — Atlantic/Pacific transit | No (satellite only) |
| Cape | Cape of Good Hope — Suez alternative route | No (satellite only) |
| Houston | Gulf of Mexico — Gulf Coast refineries, LOOP terminal | Yes |

### Monitored Refineries (FIRMS)

| Refinery | Location | Capacity |
|----------|----------|----------|
| Baytown (ExxonMobil) | Texas, USA | 584 kbd |
| Port Arthur (Motiva) | Texas, USA | 636 kbd |
| Galveston Bay (Marathon) | Texas, USA | 593 kbd |
| Ras Tanura (Saudi Aramco) | Saudi Arabia | 550 kbd |
| Jurong Island | Singapore | 592 kbd |

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Health check |
| `GET /api/prices/eia` | EIA price series |
| `GET /api/prices/eia/fundamentals` | Refinery util, imports, exports, SPR |
| `GET /api/vessels/zones` | Tanker counts per geofence zone |
| `GET /api/vessels/positions` | Tanker positions by zone |
| `GET /api/vessels/global` | Full global vessel snapshot |
| `GET /api/alerts` | Signal alerts feed |
| `GET /api/ports/disruptions` | PortWatch disruption indices |
| `GET /api/weather/alerts` | NOAA weather alerts |
| `GET /api/sentiment/gdelt` | GDELT news volume |
| `GET /api/sentiment/tone` | GDELT sentiment scores |
| `GET /api/jodi/summary` | JODI production summary (per country) |
| `GET /api/jodi/production` | JODI production time series |
| `GET /api/thermal/hotspots` | FIRMS thermal hotspot detections |
| `GET /api/thermal/refineries` | Refinery thermal status |

## License

MIT
