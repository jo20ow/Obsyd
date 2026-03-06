# OBSYD Status Report

Stand: 6. März 2026

---

## 1. AKTUELLER STAND

### Infrastruktur

| Komponente | Status | Details |
|-----------|--------|---------|
| **Backend** | LIVE | uvicorn, 1 Worker, systemd auto-restart |
| **Frontend** | LIVE | nginx serviert dist/, SPA routing |
| **VPS** | Hostinger KVM1 Frankfurt | Ubuntu 24.04, 3.8 GB RAM (711 MB belegt), 48 GB Disk (25% belegt) |
| **URL** | http://72.61.190.129 | Kein HTTPS (braucht Domain) |
| **Firewall** | UFW aktiv | Nur SSH (22), HTTP (80), HTTPS (443) |
| **Monitoring** | Cron health-check | Alle 5 Minuten, auto-restart bei Ausfall |
| **SQLite** | WAL + busy_timeout=30s | Lock-Errors nur beim Startup (transient) |

### Datenquellen

| Quelle | Status | Scheduler | Letzte Daten |
|--------|--------|-----------|--------------|
| **EIA** | Aktiv | Wöchentlich Mi 15:00 UTC | 416 Preisreihen |
| **FRED** | Aktiv | Täglich 18:00 UTC | 2.816 Datenpunkte |
| **AISHub** | Aktiv | Jede Minute | 39.129 globale Positionen, 98.930 Zone-Positionen |
| **AISStream** | Aktiv | Echtzeit WebSocket | Verbindet auf VPS |
| **PortWatch** | Aktiv | Täglich 06:00 UTC + wöchentlich Di | 73.276 Chokepoint-Tage |
| **NOAA** | Aktiv | Alle 30 Minuten | 0 aktive Alerts (ruhiges Wetter) |
| **GDELT** | Aktiv | Alle 15 Minuten | 228 Volume-Records, 5 Keywords |
| **JODI** | Aktiv | Monatlich am 15. | 59 Produktionsdaten |
| **FIRMS** | Aktiv | Alle 6 Stunden | 0 aktive Hotspots (Satellitenlücke) |

### Alle 24 API-Endpoints (VPS, 6. März 2026)

| # | Endpoint | HTTP | Bytes |
|---|----------|------|-------|
| 1 | `GET /health` | 200 | 33 |
| 2 | `GET /api/health/collectors` | 200 | 48 |
| 3 | `GET /api/prices/eia` | 200 | 132 |
| 4 | `GET /api/prices/fred` | 200 | 126 |
| 5 | `GET /api/prices/live` | 200 | 290 |
| 6 | `GET /api/prices/oil` | 200 | 302 |
| 7 | `GET /api/prices/eia/fundamentals` | 200 | 24.781 |
| 8 | `GET /api/vessels/zones` | 200 | 1.088 |
| 9 | `GET /api/vessels/positions` | 200 | 193 |
| 10 | `GET /api/vessels/global` | 200 | 150 |
| 11 | `GET /api/vessels/geofence-events` | 200 | 92 |
| 12 | `GET /api/alerts` | 200 | 262 |
| 13 | `GET /api/alerts/portwatch` | 200 | 1.084 |
| 14 | `GET /api/ports/summary` | 200 | 972 |
| 15 | `GET /api/weather/alerts` | 200 | 2 |
| 16 | `GET /api/weather/marine` | 200 | 660 |
| 17 | `GET /api/sentiment/volume` | 200 | 13.170 |
| 18 | `GET /api/sentiment/risk` | 200 | 172 |
| 19 | `GET /api/sentiment/status` | 200 | 34 |
| 20 | `GET /api/sentiment/headlines` | 200 | 40 |
| 21 | `GET /api/jodi/summary` | 200 | 608 |
| 22 | `GET /api/thermal/hotspots` | 200 | 2 |
| 23 | `GET /api/portwatch/summary` | 200 | 1.453 |
| 24 | `GET /api/signals/correlation` | 200 | 2.914 |

**Ergebnis: 24/24 OK**

### Datenbank-Tabellen

#### obsyd.db (17 MB)

| Tabelle | Rows | Beschreibung |
|---------|------|--------------|
| vessel_positions | 98.930 | AIS Tanker-Positionen in Geofence-Zonen |
| global_vessel_positions | 39.129 | AISHub Global-Snapshot (wird jede Minute ersetzt) |
| fred_series | 2.816 | FRED Makro-Daten (DXY, Yields, Fed Funds) |
| eia_prices | 416 | EIA Ölpreise + Inventories |
| gdelt_volume | 228 | GDELT News-Volumen nach Keyword |
| port_activity | 85 | PortWatch Port-Aktivität |
| jodi_production | 59 | JODI Ölproduktion nach Land |
| disruptions | 21 | PortWatch Disruption-Events |
| alerts | 14 | Signal-Alerts (floating_storage, refinery_thermal) |
| geofence_events | 8 | Tägliche Geofence-Aggregation (4 Zonen x 2 Tage) |
| sentiment_scores | 1 | Regelbasierter Sentiment Risk Score |
| thermal_hotspots | 0 | FIRMS Hotspots (aktuell keine aktiven) |
| weather_alerts | 0 | NOAA Alerts (aktuell keine aktiven) |

#### portwatch.db (6.7 MB)

| Tabelle | Rows | Beschreibung |
|---------|------|--------------|
| chokepoint_daily | 73.276 | IMF PortWatch tägliche Transit-Daten |
| oil_prices | 699 | Legacy Ölpreise (nicht mehr genutzt, ersetzt durch FRED) |
| disruptions | 21 | Disruption-Events (Duplikat von obsyd.db) |
| port_daily | 0 | Tägliche Port-Daten (nicht befüllt) |

### Codebase-Metriken

| Metrik | Wert |
|--------|------|
| Python-Dateien (Backend) | 47 |
| JSX/CSS-Dateien (Frontend) | 15 |
| Backend Lines of Code | 4.870 |
| Frontend Lines of Code | 2.149 |
| **Gesamt** | **7.019 LOC** |
| Größte Backend-Datei | portwatch_store.py (519 LOC) |
| Größte Frontend-Datei | VesselMap.jsx (408 LOC) |

---

## 2. WAS WURDE GEMACHT (Phase 1-3)

### Git History (chronologisch, älteste zuerst)

```
0db2b25 Initial commit
285f994 Add OBSYD backend and frontend
48b9c2d Add AIS vessel tracking: aisstream.io WebSocket + AISHub fallback
baf4934 Fix Alpha Vantage rate limit: add 2s delay between commodity API calls
acd170b Add IMF PortWatch collector for port activity and chokepoint transit data
b37021b Complete Phase 1: FRED macro panel, alerts panel, signal evaluator, AIS status dot
f2dafc1 Add NOAA weather alerts and Open-Meteo marine conditions
2e17b92 Add GDELT news sentiment: volume tracking + optional AI risk scoring
de07575 Fix alert duplicates (6h dedup window) and floating storage false positives
0c82caa Add EIA fundamentals (refinery util, imports/exports, SPR) and JODI oil production collector
471ae98 Add JODI production panel and one-time alert cleanup on startup
5a9618a Remove one-time alert cleanup code (duplicates cleared)
a76f83b Optimize AISHub: single global call instead of per-zone rotation
700edf2 Fix GDELT 429 rate-limiting and show AIS coverage gaps in frontend
1b52e60 Fix Suez and Panama bounding boxes (lon_min > lon_max bug)
463064b Add global vessel view with toggle between geofence and all-vessels mode
35c948a Add NASA FIRMS thermal hotspot integration with refinery anomaly detection
cfc7347 Update README with full feature documentation and setup guide
273575e feat: PortWatch integration, chokepoint alerts, correlation engine, sentinel PoC (parked)
```

### Phase 1 — Stabilisierung (committed)

| Change | Details |
|--------|---------|
| GDELT Query-Syntax gefixt | Parentheses um OR-Group, verhindert falsche Ergebnisse |
| Finnhub-Collector gelöscht | Endpoint returned 403, toter Code |
| Ölpreis-Quellen vereinheitlicht | FRED als Single Source of Truth, portwatch.db/oil_prices nicht mehr genutzt |
| Pydantic `extra: "ignore"` | .env mit unbekannten Variablen crasht nicht mehr |
| Geofence-Aggregator gebaut | `geofence_aggregator.py` — vessel_positions → geofence_events (täglich + stündlich) |
| Scheduler erweitert | PortWatch daily backfill, Geofence hourly, Sentiment 6h |
| SQLite WAL-Modus | Concurrent reads/writes ohne "database is locked" |

### Phase 2 — Produkt schärfen (uncommitted lokal, deployed auf VPS)

| Change | Details |
|--------|---------|
| Signal-Rules getuned | floating_storage: 7d-Baseline required. flow_anomaly: ±30% Fallback für 3-6 Tage. refinery_thermal: nur Alert wenn Area Hotspots hat |
| Sentiment Risk Score | `sentiment_scorer.py` — regelbasierter Tone→Risk (1-10), Volume-Multiplikator |
| Frontend Error Handling | 12x `.catch(() => {})` → `console.error` + Error-State UI |
| Frontend Loading States | 4 Panels mit `<SkeletonCard>` statt `return null` |
| Health Endpoint | `GET /api/health/collectors` — prüft ob EIA/FRED/AIS/GDELT Daten haben |
| Header Status-Dots | Pollt `/api/health/collectors` alle 60s statt hardcoded `ok` |
| Google Fonts | JetBrains Mono via CDN in index.html |
| Weather-Fetch dedupliziert | Von VesselMap + AlertsPanel nach App.jsx gehoben, Props |
| Suez-Zone untersucht | Ergebnis: AIS-Coverage-Limitation, kein Bug. PortWatch liefert Daten |
| database.py Production | `pool_pre_ping=True`, `PRAGMA busy_timeout=30000` |

### Phase 3 — Deployment (deployed auf VPS)

| Change | Details |
|--------|---------|
| VPS Setup | Ubuntu 24.04, Pakete, obsyd User, UFW Firewall |
| systemd Service | `obsyd.service` — auto-start, auto-restart, 1 Worker |
| nginx Reverse Proxy | Static dist/ + API proxy auf 127.0.0.1:8000 |
| Voyager-Container entfernt | Altes Projekt gestoppt, Docker cleanup (436 MB frei) |
| DBs migriert | obsyd.db (17 MB) + portwatch.db (6.7 MB) auf VPS |
| .env deployed | Alle API-Keys, chmod 600 |
| Health-Check Cron | Alle 5 Minuten, restart bei Ausfall |
| Deploy-Scripts | `deploy/obsyd.service`, `deploy/obsyd.nginx`, `deploy/setup-vps.sh` |
| README aktualisiert | Alle Features, Endpoints, Setup-Guide, Live-Link |

### Bugs gefixt (kumulativ)

1. **GDELT 429 Rate-Limiting** — Query-Throttling, Secondary-Keywords hourly statt 15min
2. **Suez/Panama Bounding-Box Bug** — lon_min > lon_max vertauscht
3. **Alert-Duplikate** — 6h Dedup-Window in evaluator.py
4. **Floating Storage False Positives** — SOG < 0.5kn in Häfen ist normal, 7d-Baseline required
5. **Refinery Thermal False Positives** — VIIRS-Lücken ≠ Shutdown, Area-Check
6. **Alpha Vantage Rate-Limit** — 2s Delay zwischen API-Calls
7. **Pydantic ValidationError** — `extra: "ignore"` für unbekannte .env-Variablen
8. **SQLite "database is locked"** — WAL-Modus + busy_timeout=30s
9. **Frontend silent failures** — `.catch(() => {})` → `console.error` + UI-Feedback
10. **Header Status-Dots hardcoded** — Jetzt live aus /api/health/collectors
11. **Double Weather-Fetch** — VesselMap + AlertsPanel holten beide /api/weather/alerts
12. **nginx 500 Permission Denied** — /home/obsyd chmod 755 für www-data

---

## 3. OFFENE PUNKTE

### Nicht committed (lokal)

Phase 2 + 3 Änderungen sind auf dem VPS deployed aber **nicht auf GitHub gepusht**. Betroffen:
- 25 geänderte Dateien
- 3 neue Dateien (geofence_aggregator.py, sentiment_scorer.py, Skeleton.jsx)
- 3 neue Dateien (deploy/)
- 1 gelöschte Datei (finnhub.py)

**Aktion:** Commit + Push erforderlich. Danach `git tag v0.1.0` + GitHub Release.

### Domain + HTTPS

- Keine Domain konfiguriert. Dashboard läuft nur unter HTTP-IP.
- Let's Encrypt braucht eine Domain (certbot).
- **Aktion:** Domain kaufen/konfigurieren → A-Record → certbot.

### GitHub Release

- Kein Tag, kein Release auf GitHub.
- README ist aktualisiert aber nicht gepusht.
- Screenshots fehlen.
- **Aktion:** Nach Commit: `git tag -a v0.1.0` + GitHub Release erstellen.

### Bekannte Limitationen

| Limitation | Impact | Workaround |
|-----------|--------|------------|
| **Suez/Panama: kein AIS** | Keine Live-Tanker-Positionen | PortWatch liefert Transit-Counts |
| **GDELT Rate-Limiting** | Headlines manchmal leer | Throttled auf 15min-Intervall |
| **FIRMS Satellite-Lücken** | "Kein Hotspot" ≠ "keine Aktivität" | Area-Check: nur Alert wenn Region Daten hat |
| **SQLite Single-Writer** | Lock-Errors beim Startup (10 in 10 Min) | WAL + busy_timeout, löst sich nach ~2 Min |
| **portwatch.db Legacy** | oil_prices-Tabelle (699 Rows) nicht mehr genutzt | FRED ist jetzt Source of Truth |
| **port_daily leer** | PortWatch Port-Daten werden nicht gespeichert | Nur Chokepoint-Daten aktiv |
| **Sentiment nur regelbasiert** | Kein LLM (nur GDELT Tone → Risk Score) | LLM optional per BYOK |
| **Kein Retention-Policy** | vessel_positions wächst ~36 MB/Woche | Nach ~1 Jahr ~1.9 GB, dann cleanup nötig |
| **Frontend Chunk-Größe** | JS-Bundle 1.5 MB, MapLibre 1 MB | Code-Splitting für spätere Optimierung |

---

## 4. VERBESSERUNGSVORSCHLÄGE

### Hoch-Impact, Niedrig-Aufwand (Quick Wins)

| # | Vorschlag | Impact | Aufwand |
|---|-----------|--------|---------|
| 1 | **Retention-Policy für vessel_positions** — Cron-Job: `DELETE FROM vessel_positions WHERE timestamp < datetime('now', '-90 days')`. Verhindert DB-Wachstum. | Hoch | 30 min |
| 2 | **Startup-Tasks staffeln** — `asyncio.sleep(5)` zwischen den create_task() Calls in main.py. Eliminiert Lock-Errors beim Boot. | Mittel | 15 min |
| 3 | **portwatch.db aufräumen** — Legacy oil_prices und leere port_daily löschen. Reduziert Verwirrung. | Niedrig | 15 min |
| 4 | **`print()` → `logger`** — 20+ print-Statements in portwatch_store.py → logging. Bessere Observability. | Niedrig | 30 min |

### Hoch-Impact, Mittel-Aufwand (Next Sprint)

| # | Vorschlag | Impact | Aufwand |
|---|-----------|--------|---------|
| 5 | **Domain + HTTPS** — SSL-Zertifikat. Aktuell sendet der Browser API-Keys und Daten über unverschlüsseltes HTTP. Nicht kritisch (keine User-Credentials), aber für Vertrauen wichtig. | Hoch | 1h |
| 6 | **Einzelschiff-Tracking** — Entry/Exit/Dwell-Berechnung pro MMSI statt aggregierte Zone-Counts. Zeigt welche Schiffe wo liegen und wie lange. Deutlich höherer analytischer Wert. | Sehr hoch | 2-3 Tage |
| 7 | **Error Alerting** — Wenn ein Collector 3x hintereinander failed, Notification (Email/Webhook). Aktuell sieht man Ausfälle nur in journalctl. | Hoch | 2-3h |
| 8 | **Response Caching** — /api/portwatch/summary (ArcGIS-Call) dauert 1-3s. Redis/In-Memory-Cache mit 5min TTL. Schnelleres Dashboard-Laden. | Mittel | 2-3h |
| 9 | **Frontend Code-Splitting** — `React.lazy()` für VesselMap (408 LOC + deck.gl + MapLibre). Initialer Load geht von 2.5 MB → ~500 KB. | Mittel | 1-2h |

### Hoch-Impact, Hoch-Aufwand (Roadmap)

| # | Vorschlag | Impact | Aufwand |
|---|-----------|--------|---------|
| 10 | **PostgreSQL-Migration** — Löst alle SQLite-Concurrency-Probleme permanent. Nötig ab ~10 gleichzeitige User oder wenn vessel_positions > 1M Rows. | Hoch | 1-2 Tage |
| 11 | **LLM Sentiment** — GPT-4o/Claude für Headline-Analyse statt nur GDELT-Tone. Qualitativ deutlich bessere Risk Scores. Kostet ~$0.50/Tag bei 6h-Intervall. | Hoch | 1 Tag |
| 12 | **Historical Replay** — Zeitachse im Dashboard: "Wie sah Hormuz am 15. Januar aus?" Vessel-Positionen sind historisch vorhanden, Frontend fehlt. | Mittel | 3-5 Tage |
| 13 | **CI/CD** — GitHub Actions: Test → Build → Deploy bei Push auf main. Aktuell manueller Upload via SCP. | Mittel | 1 Tag |
| 14 | **User Accounts + API Keys** — Multi-User-Zugang, eigene Watchlists, Alert-Preferences. Aktuell ist das Dashboard public ohne Auth. | Mittel | 3-5 Tage |

### Code-Qualität (aus Code-Audit)

| Problem | Stellen | Empfehlung |
|---------|---------|------------|
| **Broad `except Exception`** | 25+ Stellen in Collectors | Spezifische Exceptions (httpx.HTTPError, ValueError) |
| **Hardcoded Thresholds** | rules.py, firms.py, aishub.py | Nach config.py verschieben mit ENV-Overrides |
| **Duplicate Patterns** | portwatch.py + portwatch_store.py: identische `_parse_date()` | Shared Utils-Modul |
| **portwatch_store.py zu groß** | 519 LOC, mischt CLI + Library | Split in store.py + cli.py |
| **Keine Tests** | 0 Unit-Tests, 0 Integration-Tests | Mindestens Smoke-Tests für kritische Endpoints |
| **Keine Type Hints in Frontend** | Alle Props untyped | TypeScript-Migration oder PropTypes |

### UX-Verbesserungen

| # | Vorschlag | Warum |
|---|-----------|-------|
| 1 | **Mobile Layout** — ChokePointMonitor und CorrelationPanel sind auf Handy kaum benutzbar (12-Column Grid). | ~30% der User kommen über Mobile |
| 2 | **Auto-Refresh** — Dashboard-Daten werden nur beim Laden geholt (außer Live-Preise alle 15min). Geofence-Events und Alerts sollten alle 5min pollen. | Stale Daten nach 30min Idle |
| 3 | **Zeitraum-Selector** — PriceChart zeigt immer alle Daten. "7D / 1M / 3M / 1Y" Toggle wäre intuitiver. | Zu viel Noise bei langem Zeitraum |
| 4 | **Alert-Notifications** — Browser-Push oder Sound wenn ein neuer Critical-Alert kommt. | User bemerkt Alerts nur beim Scrollen |
| 5 | **Dark/Light Theme** — Aktuell nur Dark. Manche User bevorzugen Light, besonders tagsüber. | Accessibility |

---

## Fazit

OBSYD ist ein funktionierendes MVP mit 9 Datenquellen, 24 API-Endpoints und einem interaktiven Dashboard. Der VPS läuft stabil, die Daten-Pipeline sammelt in Echtzeit. Die größten offenen Punkte sind:

1. **Git Push + Release** — Phase 2+3 Code ist deployed aber nicht versioniert
2. **Domain + HTTPS** — Für Vertrauen und SEO
3. **Retention-Policy** — Bevor vessel_positions die DB sprengt
4. **Einzelschiff-Tracking** — Der eigentliche analytische Mehrwert gegenüber bestehenden Tools

Die Codebasis ist mit 7.019 LOC schlank und wartbar. Die größten technischen Schulden sind fehlende Tests und die SQLite-Limitierung, beides akzeptabel für ein MVP.
