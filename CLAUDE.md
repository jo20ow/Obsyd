# OBSYD — Strategischer Kontext & Arbeitsprinzipien

> Verbindliche Single Source of Truth für Positionierung, Ist-Stand und nächste Schritte.
> Strategie ist **state-agnostisch** formuliert (Prinzipien & Entscheidungen), der **Ist-Stand**
> (Abschnitt „Build-Stand") ist zum Datum unten verifiziert und veraltet schneller — bei Konflikt
> gewinnt der Code.
>
> **Stand: 2026-06-24** · Gas-Vertikal-UI (PR #14) + ENTSO-E/GIE-Keys lokal eingebaut + Backfill 2023→heute

---

## Leitentscheidung: Hybrid-Posture

Es gibt eine bewusst aufgelöste Spannung zwischen zwei Strategien:

- **Mai-Pivot** (`c929043`, 2026-05-23, *gelieferter Code*): „freie öffentliche Daten liefern
  keinen Trading-Edge" → Reposition zu **ehrlicher Open-Source-Aggregation + Convenience-Tier**.
  Konkret: AGPL-3.0, Preise Self-Host €0 / Cloud-Free €0 / Cloud-Pro €15, prädiktive Claims aus
  dem UI entfernt, Disruption-Score **deskriptiv, nicht prädiktiv**.
- **Edge-These** (dieser Doc): Premium = **Exposure-Mapping (Signal→Ticker→Richtung) +
  validierter Track Record** als echter, belegbarer Vorteil; Zielpreis 20–30 €.

**Verbindlich = Hybrid:** *Heute* gilt die Pivot-Posture (Open-Source/AGPL, deskriptive Framing,
€15). Exposure-Mapping und Track Record sind die verbindliche **Premium-Roadmap** — der Edge wird
**belegt, nicht behauptet**, über die bereits gebaute Signal-Validierungs-Schicht. Preiserhöhung
Richtung 20–30 € **erst**, wenn der Edge messbar ist. Das ist konsistent mit „Knoten für Knoten"
und der schon existierenden Validierungs-Engine.

---

## Positionierung
- Bezahlbare Down-Market Commodity-/Energie-Intelligence: die zugängliche Alternative für alle,
  die aus Kpler/Bloomberg ausgepreist sind (Muster: Koyfin, TankerTrackers.com).
- Aufbau Knoten für Knoten. Die „Breite" lebt in einer wiederverwendbaren Engine im Hintergrund —
  dem Kunden wird immer EIN fokussiertes Produkt gezeigt, nie „wir machen alles".
- Brand-frontiert / anonym betrieben. Kein öffentliches Gesicht. Glaubwürdigkeit kommt aus
  nachweisbarem Track Record, nicht aus Ruf.

## Kunde (WER)
- Primär: der „Trader ohne Bloomberg-Terminal" — ernsthafte Retail-/Semi-Pro-Trader und kleine
  Fonds-Analysten in Energie/Rohstoffen. Echte Entscheidungen → echte Zahlungsbereitschaft, aber
  höhere Qualitätslatte als Hobbyisten.
- OBSYD ist die PHYSISCHE-FLOW-INTELLIGENCE-Schicht NEBEN Charting-Tool/Broker — KEIN
  Bloomberg-Ersatz (keine Order-Ausführung, keine deckenden Echtzeit-Tickdaten, nicht alles).
- Drei Jobs: (1) Morgen-Lagebild in einem Dashboard statt 6 Tabs; (2) Alert → zeitnahe
  Entscheidung; (3) Thesen-Check mit verbundener Evidenz. Plus Cross-Commodity-„Aha" über Knoten.
- Ziel: Cashflow/Einkommen ersetzen (~50–70 Abos à 20–30 € = Lebenshaltung). Solides
  Down-Market-Geschäft, kein Einhorn.

## Tier-Framework (Signal-zu-Rauschen) — ordnet ALLES
- **Tier 1 (Wissen):** physisches Signal → Rohstoff-/Strompreis. Direkt, rational, validierbar.
  HIER führen.
- **Tier 2 (noch Wissen):** → Spreads/Beziehungen (z. B. Spark Spread).
- **Tier 3 (Glück):** → Aktien. Konfundiert, niedrige Konfidenz. Immer ausdrücklich als
  Spekulation kennzeichnen.

## Datenprinzipien (nicht verhandelbar)
- Sichtbarer Gratis-Kern = freie, redistributierbare, VOLLSTÄNDIGE offizielle Daten: AGSI
  (Gasspeicher), ALSI (LNG-Terminal-Flüsse), ENTSO-E/SMARD (Strom), EU-ETS (CO₂). Bei diesen
  Rohdaten Parität mit den teuren Anbietern — der Edge ist Aufbereitung & Verbindung.
- Aktien-/Börsen-KURSDATEN NICHT weiterverbreiten. Persönlich ~20–100 €/Monat; kommerzielle
  Display-Lizenz ~300–2500+ €/Monat. Premium = die Verbindungs-/Exposure-Schicht (eigenes IP),
  nicht rohe Feeds. OBSYD nennt **Ticker + Richtung**; den Kurs schaut der Nutzer selbst.
- NIE Absolutwerte zeigen, die nicht vollständig erfassbar sind. Stattdessen relative
  Veränderung/Trend/Index aus einer KONSISTENTEN Teilmenge — der Delta bleibt wahr, auch bei
  unvollständiger Abdeckung, solange diese stabil ist.
- Transparenz über Abdeckung/Konfidenz ist ein GLAUBWÜRDIGKEITS-FEATURE (gegen Kplers Blackbox)
  und trägt die Track-Record-These.
- Proxys gegen offizielle Totale kalibrieren, sobald diese erscheinen (Eurostat, Zoll, GIE).

## Stärken vs. Schwächen
- **STARK:** vollständige offizielle europäische Energie-Register (Gas/LNG/Strom/CO₂); die
  Synthese (Spark/Dark Spread, Merit-Order Gas+CO₂→Strom, Residuallast, Dunkelflaute-/
  Negativpreis-Erkennung, Speicher-vs-Flow-vs-Preis); AIS für ANOMALIE-/Verhaltenserkennung, WO
  die Station Abdeckung hat (Dark Ships, AIS-Lücken, Ship-to-Ship, lokaler Stau).
- **SCHWACH (nie als Aushängeschild):** globale/vollständige Schiffs-Zählungen & Flow-Totale über
  eigenes AIS; Echtzeit-Decision-Grade-Tickdaten; deckende globale Abdeckung. Über offizielle
  Quellen lösen oder weglassen.

## Premium-Kern: Exposure-Mapping + Signal-Validierung
- **Exposure-Mapping (eigenes IP, ROADMAP — noch nicht gebaut):** physisches Signal → welche
  börsennotierten Namen → welche Richtung. Kein Kursfeed nötig. *Ist heute:* nur statische
  15-Ticker-Liste mit Korrelationen + Prosa im Market-Report — **keine** strukturierte
  Signal→Ticker→Richtung-Tabelle.
- **Signal-Validierungs-/Backtest-Schicht (GEBAUT):** jedes Mapping/Signal ist eine Hypothese;
  bei Auslösung Zeitstempel + tatsächliche Bewegung über Fenster (1T/1W/1M) loggen; Trefferquote
  über Zeit. Rigor: n immer mitzeigen (`n<30 → nie „confident"`); RELATIV zu Index/Sektor messen;
  Overfitting meiden (walk-forward, FDR); muss ehrlich zeigen können, dass ein Mapping NICHT
  funktioniert. Das ist die Glaubwürdigkeits-Engine. *Ist heute:* `backend/analytics/validation/`
  scort 3 Öl/Maritim-Signale; Gas-Residual noch nicht erfasst (siehe Roadmap).

## Gratis / Premium & Preis (Ist-Stand)
- **Self-Host €0** (AGPL-3.0, eigene Infra) · **Cloud-Free €0** (obsyd.dev, 30-Tage-Historie,
  bis 3 Alerts) · **Cloud-Pro €15/Monat (€149/Jahr, −17 %)** — volle Historie ab 2019, unlimited
  Alerts, API (rate-limited), CSV/JSON-Export, Daily Brief (Mo–Fr 07:00 UTC), Custom-Geofences.
  14-Tage-Trial ohne Karte. Zahlung via **Lemon Squeezy**.
- **Gratis-Logik:** physische Charts (Lagebild) = Distributionsmagnet, SEO, teilbar.
- **Preis-Roadmap:** Ziel 20–30 € erst nach belegtem Edge (Exposure-Mapping + Track Record live).
- **Lizenz:** AGPL-3.0 — §13 zwingt Netzwerk-Anbieter zur Quelloffenlegung; schützt das Cloud-Tier
  vor Closed-Source-Forks.

## Arbeits- & Sequenzprinzipien
- Engpass ist Fertigstellen & Monetarisieren, nicht Ideen. „Gewinnen" = ein Fremder hat bezahlt,
  nicht „ich hab gebaut/gelernt".
- Zwei verbundene Knoten = minimale lebensfähige Breite = Launch. NICHT die ganze Plattform vor
  dem ersten zahlenden Kunden bauen.
- Neue Knoten (z. B. Metall via Eisenerz auf Dry-Bulk-AIS; Kupfer via LME-Bestände; Solar) kommen
  DANACH, jeder als eigener fokussierter Zusatz, der die Engine wiederverwendet.
- Wo möglich englisch/global bauen für Reichweite.

---

## Build-Stand (Ist — Stand 2026-06-24)

**Gebaut & gewired (Öl/Maritim-Vertikal, front-to-back):** Preise (WTI/Brent/NG/JKM/TTF +
Gold/Silber/Kupfer als Quotes), EIA (WPSR/STEO), FRED-Makro, AIS-Voyages/Geofences/Fleet/STS,
IMF-PortWatch-Chokepoints, Thermal (NASA FIRMS), NOAA-Wetter, JODI, GDELT+Finnhub-Sentiment,
Crack-Spreads, 15-Ticker-Equity-Universe. 47 Scheduler-Jobs (`backend/collectors/scheduler.py`).
- Synthese (`backend/analytics/`): **Disruption-Composite-Score** (6 Signale, feste Gewichte,
  alle 2 h), Tonne-Miles-Index, Freight-Proxy, Supply-Demand-Balance, Days-of-Supply,
  EIA-Prediction, Market-Report-Narrativ.
- **Signal-Validierung — stark gebaut** (`backend/analytics/validation/`): Rank-IC,
  Newey-West-HAC-t, Scorecards (`scorecards.py`, `SIGNAL_SPECS` = 3 Signale: disruption_score,
  tonne_miles, freight_proxy), OOS-Weight-Backtest (`weights.py`), Weekly-Job
  (`signal_scorecards_weekly`), `routes/validation.py`, `TrackRecordBadge.jsx` in 3 Panels.

**EU-Gas-Balance-Vertikal — jetzt mit UI** (`backend/gas/`: ENTSOG, AGSI/GIE, ALSI,
ENTSO-E-Power-Burn, Open-Meteo-HDD, Eurostat; Residual-Engine in `balance.py` = *„this is the
product"*). Seit 2026-06-24 (PR #14): **GAS-Tab im Frontend** — Pro-Residual-Hero
(`GasBalancePanel`, RESIDUAL⇄IMPLIED-Toggle, Flag-Marker) + freie Treiber-Panels
`GasStoragePanel`/`GasSupplyPanel`/`GasDemandPanel`; shared `frontend/src/utils/chart.js`.
Gating: **Rohdaten frei, Residual Pro**. ENTSO-E-Token + GIE-Key sind **lokal eingebaut &
validiert**, voller Backfill 2023→heute gelaufen → Power-Burn/Demand/Balance laufen **real,
nicht mehr PRELIMINARY** (`/api/gas/*` liefert ~121 Zeilen @ days=120). ⚠️ **Prod-Caveat:**
beide Keys müssen noch in die Prod-`.env` auf dem VPS + dort ein Backfill, sonst ist das
Vertikal in Production leer.

**Fehlt komplett:** Exposure-Mapping (Signal→Ticker→Richtung), CO₂/EU-ETS (0 Treffer im Code),
Spark/Dark-Spread, Merit-Order, gas→power→CO₂-Synthese, Cross-Commodity-Fusion (Öl- und
Gas-Vertikal siliert; Gas-Residual **nicht** in den Scorecards → kein Track Record fürs
Gas-Vertikal), Metalle/Kupfer/Solar als Analytik-Knoten (nur Preis-Quotes).

### Single Source of Truth (vor Fehlern bewahren)
- **Preis** lebt nur im Frontend (`frontend/src/components/PricingModal.jsx`: `PRO_PRICE='€15'`,
  `PRO_YEAR_NOTE='€149/year (−17%)'`) + README — **nicht** in `backend/config.py`.
- **Pro-Status** = `backend/auth/subscription_check.py` (`is_pro()`); Gate-Dependency
  `backend/auth/dependencies.py` (`require_pro`); Frontend-Gate `components/ProGate.jsx`.
- **Lizenz** = `LICENSE` (AGPL-3.0). Zahlung = Lemon Squeezy (`LEMONSQUEEZY_*` in `.env`).

---

## Roadmap (abgeleitet aus „zwei verbundene Knoten = Launch")

1. ~~**Gas-Vertikal sichtbar machen**~~ — **erledigt 2026-06-24 (PR #14):** GAS-Tab mit
   Residual-Hero + freien Treiber-Panels; ENTSO-E-Token + GIE-Key live (lokal), Backfill
   2023→heute. Rest-Aufgabe: Keys + Backfill in **Prod** (siehe Build-Stand-Caveat).
2. **Gas-Residual in die Validierungs-Schicht** — `SIGNAL_SPECS` (`backend/analytics/validation/
   scorecards.py:31`) um das Gas-Signal erweitern → Track Record + `TrackRecordBadge` auf dem
   Balance-Panel. **← nächster Schritt.**
3. **Exposure-Mapping v1** (Premium-Kern) — strukturierte Signal→Ticker→Richtung-Tabelle statt
   statischer Liste + Prosa; als Hypothese durch die bestehende Validierungs-Schicht laufen
   lassen, **bevor** sie als Edge verkauft wird. Erst dann Preis-Diskussion 20–30 €.
4. **Danach** neue Knoten — CO₂/EU-ETS (vervollständigt die gas→power→CO₂-Kette), Metall/Kupfer,
   Solar; jeder als fokussierter Zusatz, Engine wiederverwendet.

---

## Bekannte Inkonsistenzen (Cleanup)
- ~~Preis-Leiche in Trial-Mails (`trial_drip.py`): Pre-Pivot **€19,90/199 €**~~ — **behoben
  2026-06-24**, auf €15/€149 angeglichen. (Lehre: Preis-Strings leben verstreut; bei künftigen
  Preisänderungen `trial_drip.py`, `PricingModal.jsx` und README zusammen anfassen.)
- ~~**ENTSO-E-Token in Prod** unbestätigt~~ — **lokal gelöst 2026-06-24:** `ENTSOE_API_TOKEN`
  + `GIE_API_KEY` (letzterer aus `commodity-signal` übernommen) eingebaut & validiert, Backfill
  lief. **Offen für Prod:** beide Keys in die VPS-Prod-`.env` + dortiger Backfill — sonst Gas-Tab
  in Production leer. (Entscheidung „kein LLM in Obsyd / keine commodity-signal-Fusion" siehe Memory.)
- **Deploy-Docs widersprüchlich:** README „Tech Stack" nennt nginx/systemd/Let's-Encrypt, die
  `deploy/`-Skripte sind Caddy-zentriert (obsyd.dev teilt Caddy mit ValueKick).
- **`docs/signal-validation.md` Status-Banner** (P1 shipped) hinkt dem Ist nach — Scorecards sind
  persistiert, Routes live (faktisch P2/P3-Teile gebaut).
- **`PROJECT_STATUS.md`** war ein vor-Pivot-Statusdoc (2026-03-06) und wurde durch diesen Abschnitt
  ersetzt (siehe Git-Historie für das Archiv).
