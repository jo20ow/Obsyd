# OBSYD — Project Status Report

_Generated: 2026-03-06_

---

## 1. ARCHITEKTUR

### Verzeichnisstruktur

```
obsyd/
├── backend/
│   ├── collectors/       # 13 Datenquellen-Collector
│   ├── config.py         # Pydantic Settings (.env)
│   ├── database.py       # SQLAlchemy setup
│   ├── geofences/        # AIS-Zonen (Hormuz, Suez, etc.)
│   ├── main.py           # FastAPI app + lifespan
│   ├── models/           # SQLAlchemy ORM models
│   ├── routes/           # 11 API-Route-Module
│   └── signals/          # Alert-Regeln + Korrelation
├── frontend/
│   ├── src/
│   │   ├── App.jsx       # Haupt-Layout
│   │   └── components/   # 11 React-Komponenten
│   ├── dist/             # Vite build output
│   ├── package.json
│   └── vite.config.js    # Proxy /api → localhost:8000
├── data/
│   └── portwatch.db      # Standalone SQLite (6.7 MB)
├── obsyd.db              # Haupt-DB (13.2 MB)
├── .env                  # API-Keys (nicht committed)
├── .env.example
├── requirements.txt
└── .venv/                # Python virtual environment
```

### Tech Stack

| Layer | Technologie |
|-------|------------|
| Backend | Python 3, FastAPI, uvicorn, SQLAlchemy |
| Frontend | React 19, Vite 7, Tailwind CSS v4 |
| Charts | lightweight-charts (TradingView OSS), Recharts |
| Maps | deck.gl, react-map-gl, MapLibre GL |
| Datenbanken | SQLite x2 (obsyd.db + data/portwatch.db) |
| Scheduler | APScheduler (AsyncIO) |
| HTTP Client | httpx (async) |

### Starten

```bash
# Backend
cd ~/obsyd
source .venv/bin/activate
uvicorn backend.main:app --reload          # Port 8000

# Frontend
cd ~/obsyd/frontend
npm run dev                                 # Port 5173 (Proxy → 8000)
```

**Aktueller Status**: Backend (uvicorn :8000) und Frontend (vite :5173) laufen beide.

---

## 2. DATENQUELLEN

### 2.1 EIA (Energy Information Administration)

| | |
|---|---|
| Collector | `backend/collectors/eia.py` |
| API | `https://api.eia.gov/v2` |
| Key | `EIA_API_KEY` — SET |
| Daten | WTI weekly (PET.RWTC.W), Brent weekly (PET.RBRTE.W), NG (NG.RNGWHHD.W), Cushing stocks (PET.WCSSTUS1.W), Refinery util, Imports, Exports, SPR |
| Intervall | Wöchentlich (Mi 15:00 UTC, nach WPSR-Release) |
| DB-Tabelle | `obsyd.db/eia_prices` — 416 Rows |
| Letzter Datenpunkt | **2026-02-27** |
| Status | **OK** — Funktioniert, Daten 7 Tage alt (normal, wöchentlich) |

### 2.2 FRED (Federal Reserve Economic Data)

| | |
|---|---|
| Collector | `backend/collectors/fred.py` |
| API | `https://api.stlouisfed.org/fred` |
| Key | `FRED_API_KEY` — SET |
| Daten | WTI daily (DCOILWTICO), Brent daily (DCOILBRENTEU), DXY, 10Y/2Y Yields, CPI, Fed Funds |
| Intervall | Täglich (18:00 UTC) |
| DB-Tabelle | `obsyd.db/fred_series` — 2,816 Rows (seit 1995-09-01) |
| Letzter Datenpunkt | **2026-03-05** (gestern) |
| Status | **OK** — Aktuell |

### 2.3 Alpha Vantage

| | |
|---|---|
| Collector | `backend/collectors/alphavantage.py` |
| API | `https://www.alphavantage.co/query` |
| Key | `ALPHA_VANTAGE_API_KEY` — SET |
| Daten | WTI, Brent, Natural Gas — tägliche Preise |
| Intervall | On-demand, 15min In-Memory-Cache |
| DB-Tabelle | Kein DB-Store (nur Cache) |
| Letzter Datenpunkt | **2026-03-02** (WTI $71.13, Brent $77.24, NG $2.99) |
| Status | **OK** — Liefert aktuelle Preise. 25 calls/Tag Limit. |

### 2.4 Finnhub

| | |
|---|---|
| Collector | `backend/collectors/finnhub.py` |
| API | `https://finnhub.io/api/v1` |
| Key | `FINNHUB_API_KEY` — SET |
| Daten | Forex only (EUR/USD, GBP/USD, USD/JPY) — **KEINE Commodities** |
| Intervall | On-demand |
| DB-Tabelle | Kein DB-Store |
| Letzter Datenpunkt | N/A |
| Status | **KAPUTT** — Key ist gesetzt, API liefert **403 Forbidden** auf alle 3 Forex-Paare. OANDA-Symbole erfordern vermutlich Finnhub Premium. Endpoint liefert `{"available": true, "prices": {}}`. Wird nirgends im Frontend angezeigt. |

### 2.5 IMF PortWatch (Standalone)

| | |
|---|---|
| Collector | `backend/collectors/portwatch_store.py` |
| API | ArcGIS Feature Server (IMF) |
| Key | Kein Key nötig (öffentlich) |
| Daten | Chokepoint daily transits (5 Key-Chokepoints), Disruptions, Oil Prices (FRED) |
| Intervall | Backfill komplett seit 2019-01-01 |
| DB-Tabelle | `data/portwatch.db` — chokepoint_daily: 73,276 Rows (2019-01-01 → 2026-03-01), oil_prices: 699 Rows, disruptions: 21 Rows |
| Letzter Datenpunkt | **2026-03-01** (chokepoints), **2026-03-02** (oil prices) |
| Status | **OK** — Vollständiger Backfill, 1 Tag Lag (normal) |

### 2.6 IMF PortWatch (SQLAlchemy-Integrationslayer)

| | |
|---|---|
| Collector | `backend/collectors/portwatch.py` |
| API | Gleiche ArcGIS API, via portwatch_store |
| Daten | Ports (Houston, Singapore etc.) + Chokepoints → obsyd.db |
| Intervall | Wöchentlich (Di 12:00 UTC) + Startup |
| DB-Tabelle | `obsyd.db/port_activity` — 85 Rows (2026-02-21 → 2026-03-04) |
| Letzter Datenpunkt | **2026-03-04** |
| Status | **OK** |

### 2.7 AISstream (WebSocket)

| | |
|---|---|
| Collector | `backend/collectors/aisstream.py` |
| API | `wss://stream.aisstream.io/v0/stream` |
| Key | `AISSTREAM_API_KEY` — SET |
| Daten | Echtzeit-AIS-Positionen für Geofence-Zonen |
| Intervall | Echtzeit (WebSocket) |
| DB-Tabelle | `obsyd.db/vessel_positions` — 73,454 Rows (latest: 2026-03-06 09:56) |
| Letzter Datenpunkt | **Heute, live** |
| Status | **EINGESCHRANKT** — `websockets`-Paket fehlt in .venv (Import schlaegt fehl). Laeuft aktuell ueber System-Python. 73k Rows in DB zeigen dass es funktioniert hat, aber ein `pip install websockets` in .venv ist noetig. |

### 2.8 AISHub

| | |
|---|---|
| Collector | `backend/collectors/aishub.py` |
| API | `http://data.aishub.net/ws.php` |
| Key | `AISHUB_API_KEY` — SET, `AISHUB_USERNAME` — NOT SET |
| Daten | Globale Vessel-Positionen |
| Intervall | Polling (alle paar Minuten) |
| DB-Tabelle | `obsyd.db/global_vessel_positions` — 39,663 Rows |
| Status | **TEILWEISE** — Key gesetzt, Username fehlt. AISHub benötigt beides. |

### 2.9 NOAA Weather

| | |
|---|---|
| Collector | `backend/collectors/noaa.py` |
| API | `https://api.weather.gov` |
| Key | Kein Key nötig (öffentlich) |
| Daten | Weather alerts (Gulf Coast, hurricanes), Marine forecasts |
| Intervall | Alle 30 Minuten |
| DB-Tabelle | `obsyd.db/weather_alerts` — **0 Rows** |
| Status | **OK aber leer** — Keine aktiven Weather Alerts derzeit (normal, kein Sturm). Endpoint funktioniert, marine forecasts werden live abgerufen (2 Forecasts returned). |

### 2.10 GDELT

| | |
|---|---|
| Collector | `backend/collectors/gdelt.py` |
| API | GDELT DOC 2.0 API |
| Key | Kein Key nötig (öffentlich) |
| Daten | Nachrichtenvolumen (oil-relevante Keywords), Sentiment |
| Intervall | Primär: 15min, Sekundär: stündlich, Sentiment: täglich |
| DB-Tabelle | `obsyd.db/gdelt_volume` — 222 Rows (latest: 2026-03-06 07:15), `sentiment_scores` — **0 Rows** |
| Status | **TEILWEISE** — Volume/Tone funktioniert. Headlines haben einen **Query-Syntax-Bug** (`gdelt.py` ~Zeile 100): OR-Terms brauchen Klammern, aktuell `"oil price" OR "OPEC" sourcelang:english` statt `("oil price" OR "OPEC") sourcelang:english`. Sentiment-Risk braucht OpenAI/Anthropic Key (beide NOT SET). `sentiment_scores` Tabelle leer. GDELT-API hat aggressives Rate-Limiting (429 nach ~3 Calls). |

### 2.11 JODI (Joint Organisations Data Initiative)

| | |
|---|---|
| Collector | `backend/collectors/jodi.py` |
| API | JODI (UN open data) |
| Key | Kein Key nötig |
| Daten | Ölproduktion, -verbrauch, -lager nach Land |
| Intervall | Monatlich (15. um 10:00 UTC) |
| DB-Tabelle | `obsyd.db/jodi_production` — 59 Rows (2025-01 → 2025-12) |
| Status | **OK** — 12 Monate Daten, 5 Länder |

### 2.12 NASA FIRMS

| | |
|---|---|
| Collector | `backend/collectors/firms.py` |
| API | NASA FIRMS VIIRS API |
| Key | `FIRMS_API_KEY` — SET |
| Daten | Thermische Hotspots nahe Raffinerien |
| Intervall | Alle 6 Stunden |
| DB-Tabelle | `obsyd.db/thermal_hotspots` — 2 Rows (2026-03-06) |
| Status | **OK** — 2 Hotspots heute detected (Gulf Coast) |

### 2.13 FRED Oil Prices (Standalone)

| | |
|---|---|
| Collector | `backend/collectors/portwatch_store.py` (fetch_oil_prices) |
| API | FRED API |
| Key | `FRED_API_KEY` (shared) |
| Daten | WTI + Brent daily in portwatch.db |
| Intervall | On-demand (auto-fetch bei leerem Cache) |
| DB-Tabelle | `data/portwatch.db/oil_prices` — 699 Rows (2024-10-08 → 2026-03-02) |
| Status | **OK** — Genutzt für ChokePointMonitor Brent-Overlay und Correlation Engine |

---

## 3. API-ENDPOINTS

Alle Endpoints getestet gegen laufendes Backend (localhost:8000).

### Health

| Methode | Pfad | Status | Ergebnis |
|---------|------|--------|----------|
| GET | `/health` | **OK** | `{"status": "ok", "service": "obsyd"}` |

**Hinweis**: Pfad ist `/health`, nicht `/api/health`.

### Prices (6 Endpoints)

| Methode | Pfad | Status | Ergebnis |
|---------|------|--------|----------|
| GET | `/api/prices/eia` | **OK** | 416 Rows, latest 2026-02-27 |
| GET | `/api/prices/eia/series` | **OK** | 8 Serien |
| GET | `/api/prices/eia/fundamentals` | **OK** | 4 Serien |
| POST | `/api/prices/eia/collect` | **OK** | Manueller Trigger |
| GET | `/api/prices/live` | **OK** | Source: alphavantage, WTI/BRENT/NG |
| GET | `/api/prices/forex` | **LEER** | available=true aber prices={} |
| GET | `/api/prices/fred` | **OK** | 2,816 Rows, latest 2026-03-05 |
| GET | `/api/prices/fred/series` | **OK** | Serien-Liste |
| POST | `/api/prices/fred/collect` | **OK** | Manueller Trigger |
| GET | `/api/prices/oil` | **OK** | WTI + Brent daily |

### Vessels (4 Endpoints)

| Methode | Pfad | Status | Ergebnis |
|---------|------|--------|----------|
| GET | `/api/vessels/zones` | **OK** | 6 Zonen |
| GET | `/api/vessels/positions` | **OK** | Vessel-Positionen (live) |
| GET | `/api/vessels/global` | **OK** | Globale Positionen |
| GET | `/api/vessels/geofence-events` | **OK** | 0 Events (keine Geofence-Crossings gespeichert) |

### PortWatch (4 Endpoints)

| Methode | Pfad | Status | Ergebnis |
|---------|------|--------|----------|
| GET | `/api/portwatch/chokepoints` | **OK** | 5 Chokepoints aktuell |
| GET | `/api/portwatch/chokepoints/{name}/history` | **OK** | Zeitreihe (getestet: Hormuz 30d) |
| GET | `/api/portwatch/disruptions` | **OK** | 21 total, 2 aktiv |
| GET | `/api/portwatch/summary` | **OK** | 5 CPs + Anomalien + Disruptions |

### Alerts (2 Endpoints)

| Methode | Pfad | Status | Ergebnis |
|---------|------|--------|----------|
| GET | `/api/alerts` | **OK** | 14 Alerts in DB |
| GET | `/api/alerts/portwatch` | **OK** | 4 Live-Anomaly-Alerts |

### Signals (1 Endpoint)

| Methode | Pfad | Status | Ergebnis |
|---------|------|--------|----------|
| GET | `/api/signals/correlation` | **OK** | 5 Chokepoint-Brent-Korrelationen |

### Weather (2 Endpoints)

| Methode | Pfad | Status | Ergebnis |
|---------|------|--------|----------|
| GET | `/api/weather/alerts` | **OK** | 0 Alerts (kein Sturm) |
| GET | `/api/weather/marine` | **OK** | 2 Marine Forecasts |

### Sentiment (4 Endpoints)

| Methode | Pfad | Status | Ergebnis |
|---------|------|--------|----------|
| GET | `/api/sentiment/status` | **OK** | active=true, 222 Records |
| GET | `/api/sentiment/volume` | **OK** | 2 Keyword-Gruppen |
| GET | `/api/sentiment/headlines` | **OK** | 2 Headline-Kategorien |
| GET | `/api/sentiment/risk` | **LEER** | available=false, score=null |

### JODI (3 Endpoints)

| Methode | Pfad | Status | Ergebnis |
|---------|------|--------|----------|
| GET | `/api/jodi/production` | **OK** | 59 Rows |
| GET | `/api/jodi/summary` | **OK** | 5 Länder, latest 2025-12 |
| POST | `/api/jodi/collect` | **OK** | Manueller Trigger |

### Thermal (3 Endpoints)

| Methode | Pfad | Status | Ergebnis |
|---------|------|--------|----------|
| GET | `/api/thermal/hotspots` | **OK** | 2 Hotspots (Gulf Coast, heute) |
| GET | `/api/thermal/refineries` | **OK** | 5 Raffinerie-Standorte |
| POST | `/api/thermal/collect` | **OK** | Manueller Trigger |

### Ports (2 Endpoints)

| Methode | Pfad | Status | Ergebnis |
|---------|------|--------|----------|
| GET | `/api/ports/activity` | **OK** | 85 Rows |
| GET | `/api/ports/summary` | **OK** | 5 Chokepoints, 0 Ports (ports-Array leer) |

**Gesamt: 35 Endpoints, 32 OK, 2 LEER (forex, sentiment/risk), 1 strukturell leer (geofence-events)**

---

## 4. FRONTEND-KOMPONENTEN

11 Komponenten in `frontend/src/components/`:

| Komponente | Anzeige | API-Endpoint | Status |
|-----------|---------|-------------|--------|
| `Header.jsx` | Titel, AIS/GDELT Status-Badges | Props von App | **OK** |
| `PriceChart.jsx` | Candlestick-Chart (lightweight-charts) | `/api/prices/eia` (via Props) | **OK** — zeigt EIA weekly WTI+Brent |
| `StatCards.jsx` | WTI/Brent/NG/Cushing Preiskarten | `/api/prices/live` + `/api/prices/eia` (via Props) | **OK** — LIVE badge mit AV, DAILY mit FRED, WEEKLY mit EIA |
| `MacroPanel.jsx` | DXY, Yields, CPI, Fed Funds | `/api/prices/fred` | **OK** |
| `SentimentPanel.jsx` | GDELT Nachrichtenvolumen + Tone | `/api/sentiment/volume`, `/api/sentiment/headlines` | **OK** — zeigt Daten wenn vorhanden |
| `FundamentalsPanel.jsx` | Refinery Util, Imports, Exports, SPR | `/api/prices/eia/fundamentals` | **OK** |
| `JODIPanel.jsx` | Produktion/Verbrauch/Lager nach Land | `/api/jodi/summary` | **OK** |
| `VesselMap.jsx` | Interaktive Karte (deck.gl) | `/api/vessels/positions`, `/api/vessels/global` | **OK** — Geofence + Global toggle |
| `AlertsPanel.jsx` | Signal Alerts + Weather + Chokepoint-Anomalien | `/api/alerts`, `/api/weather/alerts`, `/api/alerts/portwatch` | **OK** — kombiniert 3 Alert-Quellen |
| `ChokePointMonitor.jsx` | Chokepoint Cards + History-Chart + Disruptions | `/api/portwatch/summary`, `/api/portwatch/chokepoints/{name}/history`, `/api/prices/oil` | **OK** — Brent-Overlay auf Zeitreihe |
| `CorrelationPanel.jsx` | Chokepoint-Brent Korrelationstabelle | `/api/signals/correlation` | **OK** — Level r, Delta r, Lag, Impact, Active Event Banner |

### Frontend Dependencies

```
react 19.2, react-dom 19.2
recharts 3.7 (ChokePointMonitor, FundamentalsPanel, JODIPanel)
lightweight-charts 5.1 (PriceChart)
deck.gl 9.2 + react-map-gl 8.1 + maplibre-gl 5.19 (VesselMap)
tailwindcss 4.2
vite 7.3
```

---

## 5. DATENBANKEN

### obsyd.db (SQLAlchemy, 13.2 MB)

| Tabelle | Rows | Zeitraum | Bemerkung |
|---------|------|----------|-----------|
| eia_prices | 416 | 2025-03-07 → 2026-02-27 | Wöchentlich, 8 Serien |
| fred_series | 2,816 | 1995-09-01 → 2026-03-05 | Täglich, 8 Serien |
| vessel_positions | 73,454 | → 2026-03-06 09:56 | AIS live, Geofence-Zonen |
| global_vessel_positions | 39,663 | | AISHub global |
| port_activity | 85 | 2026-02-21 → 2026-03-04 | PortWatch Ports |
| gdelt_volume | 222 | → 2026-03-06 07:15 | Nachrichtenvolumen |
| jodi_production | 59 | 2025-01 → 2025-12 | 5 Länder, 12 Monate |
| thermal_hotspots | 2 | 2026-03-06 | NASA FIRMS |
| alerts | 14 | → 2026-03-06 09:55 | Signal-Alerts |
| disruptions | 21 | | PortWatch Disruptions |
| geofence_events | **0** | | Nie befüllt |
| weather_alerts | **0** | | Aktuell keine Alerts |
| sentiment_scores | **0** | | Nie befüllt |

### data/portwatch.db (Standalone SQLite, 6.7 MB)

| Tabelle | Rows | Zeitraum | Bemerkung |
|---------|------|----------|-----------|
| chokepoint_daily | 73,276 | 2019-01-01 → 2026-03-01 | Vollständiger Backfill |
| oil_prices | 699 | 2024-10-08 → 2026-03-02 | FRED WTI + Brent daily |
| disruptions | 21 | | Aktive Disruptions |
| port_daily | **0** | | Nie befüllt |

---

## 6. PROBLEME & LUCKEN

### Kaputt / Nicht funktional

1. **Finnhub Forex**: Key ist gesetzt, Endpoint liefert `available: true` aber `prices: {}`. Finnhub Free Tier scheint keine Forex-Quotes mehr zu liefern, oder die Symbole (EUR/USD etc.) sind falsch. **Kein Nutzer-Impact** — wird nirgends im Frontend angezeigt.

2. **Sentiment Risk Score**: `/api/sentiment/risk` liefert `available: false`. Sentiment-Berechnung (GDELT Tone → Risk Score) ist implementiert, aber der Score wird nie berechnet/gespeichert. `sentiment_scores` Tabelle ist leer.

3. **Geofence Events**: Tabelle `geofence_events` hat 0 Rows. Das Signal-System (`evaluator.py`) braucht GeofenceEvents für `check_flow_anomaly()` und `check_floating_storage()`, bekommt aber keine Daten. Die AIS-Positionen werden gespeichert, aber nie zu Geofence-Events aggregiert. **Die Signal-Rules laufen leer.**

4. **AISHub Username**: `AISHUB_USERNAME` ist nicht gesetzt (nur der API Key). AISHub braucht beides. Unklar ob der Collector überhaupt Daten liefert — die 39k global_vessel_positions könnten von einem früheren Test stammen.

### Veraltete Daten

5. **Chokepoint Daily**: Letzte Daten vom **2026-03-01** (5 Tage alt). Die Summary-API fetcht nur 7-35 Tage von ArcGIS und speichert. Kein automatischer täglicher Backfill — nur manuell oder bei API-Aufruf.

6. **Oil Prices (portwatch.db)**: Letzte Daten vom **2026-03-02** (4 Tage alt). Werden nur on-demand bei leerem Cache nachgeladen, kein automatischer Refresh.

7. **Port Summary**: `/api/ports/summary` liefert `ports: []` — der Port-Teil ist leer, nur Chokepoints werden befüllt.

### Unvollstandige Features

8. **port_daily**: Tabelle existiert in portwatch.db aber hat 0 Rows. `fetch_port_data()` ist implementiert aber wird nie aufgerufen.

9. **Scheduler kennt portwatch_store nicht**: Der Scheduler refresht `portwatch.py` (SQLAlchemy layer) wöchentlich, aber die Standalone-portwatch_store (chokepoint_daily mit 73k Rows) wird nicht automatisch aktualisiert. Neue Tage müssen manuell via API-Aufruf oder Backfill getriggert werden.

10. **Oil Price Daten-Duplikation**: Ölpreise existieren in 3 Stellen: `obsyd.db/fred_series`, `data/portwatch.db/oil_prices`, und Alpha Vantage In-Memory-Cache. Keine Synchronisation zwischen ihnen.

### Code-Bugs

11. **GDELT Headlines Query-Syntax**: `backend/collectors/gdelt.py` ~Zeile 100: `" OR ".join(keywords)` erzeugt `"oil price" OR "OPEC" sourcelang:english` — GDELT braucht Klammern: `("oil price" OR "OPEC") sourcelang:english`. Headlines kommen daher oft leer zurueck.

12. **`websockets` fehlt in .venv**: AISstream-Collector kann nicht importiert werden. `pip install websockets` in .venv noetig. Laeuft vermutlich ueber System-Python oder eine andere Session.

13. **Finnhub 403**: Nicht "leer" sondern aktiv abgewiesen. OANDA Forex-Symbole brauchen Finnhub Premium Plan.

### Frontend-Schwaechen

14. **Silent Error Swallowing**: Alle Komponenten nutzen `.catch(() => {})` — Fehler werden komplett verschluckt, kein Console-Log, keine User-Meldung. Nur `App.jsx` zeigt Fehler beim initialen Load.

15. **Kein Loading-State in Child-Komponenten**: MacroPanel, SentimentPanel, FundamentalsPanel, JODIPanel geben `null` zurueck waehrend Daten laden — Content "poppt" ohne Skeleton/Spinner rein.

16. **Doppelter Fetch**: `/api/weather/alerts` wird von VesselMap UND AlertsPanel unabhaengig gefetcht. Koennte in App.jsx gehoben und als Props geteilt werden.

17. **Font nicht geladen**: JetBrains Mono ist in `index.css` referenziert aber nie via `<link>`, `@font-face` oder npm geladen. Browser faellt auf Fira Code / SF Mono / System-Monospace zurueck.

18. **Header Status-Dots**: EIA und FRED Status sind hardcoded `ok={true}` — zeigen immer gruen, egal ob der Collector tatsaechlich funktioniert.

### .env Variablen

| Variable | Status |
|----------|--------|
| `DATABASE_URL` | SET |
| `EIA_API_KEY` | SET |
| `FRED_API_KEY` | SET |
| `ALPHA_VANTAGE_API_KEY` | SET |
| `FINNHUB_API_KEY` | SET (aber Endpoint liefert leer) |
| `AISSTREAM_API_KEY` | SET |
| `AISHUB_API_KEY` | SET |
| `AISHUB_USERNAME` | **NOT SET** |
| `FIRMS_API_KEY` | SET |
| `OPENAI_API_KEY` | NOT SET (nicht benutzt) |
| `ANTHROPIC_API_KEY` | NOT SET (nicht benutzt) |

---

## 7. OBSYD-SENTINEL (~/obsyd-sentinel/)

**Status: GEPARKT**

### Was wurde getestet

- Sentinel-1 SAR-basierte Tankfuellstandserkennung in Cushing, Oklahoma
- 25 Sentinel-1 GRD IW Szenen (Orbit 34), April 2025 - Marz 2026
- 3 Methoden: Farm Bounding-Box (roh + kalibriert), 20 Einzeltank-Kreise (35m Radius)
- Korrelation gegen EIA PET.WCRSTUS1.W (Cushing Crude Oil Stocks)

### Ergebnis

- **Beste Korrelation: r = -0.194** (nicht statistisch signifikant bei n=24)
- Vorzeichen physikalisch korrekt (voller Tank = niedriger Backscatter)
- Signal existiert moglicherweise, ist aber im Rauschen nicht extrahierbar
- 10m Auflosung: nur ~38 Pixel pro Tank, Speckle dominiert
- 12-Tage Kadenz: zu wenig Datenpunkte, zeitlicher Versatz zu EIA

### Was bleibt erhalten

```
obsyd-sentinel/
├── download.py          # Sentinel-1 Download via sentinelsat
├── preprocess.py        # GRD Preprocessing (Kalibrierung, Speckle-Filter)
├── preprocess_v2.py     # Verbesserte Version
├── identify_farms.py    # Tank-Farm-Erkennung
├── measure.py           # Backscatter-Messung
├── measure_tanks.py     # Einzeltank-Messung (20 Tanks)
├── validate.py          # Korrelation vs EIA
├── pipeline.py          # End-to-End Pipeline
├── config.py            # Konfiguration
├── results/             # Plots und CSVs
├── tanks/               # Tank-Positionen
└── RESULTS.md           # Ergebnis-Dokumentation
```

Pipeline funktionsfahig fur spatere Nutzung mit besseren SAR-Daten (ICEYE/Capella, 1m, taglich — ca. 25k EUR/Jahr).

---

## 8. DEPLOYMENT

### Aktueller Status

- **Nur lokal** — Kein VPS-Deployment. Lauft auf lokaler Maschine (Linux 6.17.0, Ubuntu/Debian-basiert).
- Backend: uvicorn mit `--reload` (Development-Modus)
- Frontend: Vite Dev-Server mit Proxy
- `frontend/dist/` existiert (letzter Build: 2026-03-06 10:31)

### Git Status

```
Branch: main
Remote: git@github.com:jo20ow/obsyd.git (origin)
Tracking: origin/main (aktuell)
```

**UNCOMMITTED CHANGES** (12 geanderte + 8 neue Dateien):

Geandert:
- `backend/collectors/portwatch.py`
- `backend/database.py`
- `backend/main.py`
- `backend/models/__init__.py`, `models/ports.py`
- `backend/routes/alerts.py`, `routes/prices.py`
- `frontend/package.json`, `package-lock.json`
- `frontend/src/App.jsx`
- `frontend/src/components/AlertsPanel.jsx`, `StatCards.jsx`

Neue Dateien (unversioniert):
- `backend/collectors/portwatch_store.py`
- `backend/routes/portwatch.py`, `routes/signals.py`
- `backend/signals/correlation.py`, `signals/portwatch_alerts.py`
- `frontend/src/components/ChokePointMonitor.jsx`, `CorrelationPanel.jsx`

### .gitignore

Vorhanden und vollstandig. Ignoriert: `__pycache__`, `.env`, `*.db`, `.venv/`, IDE-Dateien. Datenbanken und Secrets werden korrekt ausgeschlossen.

---

## Zusammenfassung

| Bereich | Status |
|---------|--------|
| Backend (FastAPI) | 35 Endpoints, 32 funktional, 3 leer |
| Frontend (React) | 11 Komponenten, alle rendern, aber stille Fehlerbehandlung und fehlende Loading-States |
| Datenquellen | 13 Collector: 9 OK, 2 teilweise (GDELT, AISHub), 2 kaputt (Finnhub 403, AISstream Import) |
| Datenbanken | 2 SQLite (19.9 MB gesamt), 12 Tabellen mit Daten, 3 permanent leer |
| Signal-System | PortWatch-Alerts + Correlation funktional, AIS-basierte Signals tot (keine GeofenceEvents) |
| Code-Bugs | 3 (GDELT Query-Syntax, fehlendes websockets-Paket, Finnhub 403) |
| Git | 20 uncommitted changes (PortWatch + Correlation + StatCards Feature) |
| Deployment | Nur lokal, kein VPS |
| Sentinel | Geparkt (r=-0.194, nicht verwertbar) |
