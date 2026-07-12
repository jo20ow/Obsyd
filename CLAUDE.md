# OBSYD — Strategischer Kontext & Arbeitsprinzipien

> ## ⇒ IST-STAND-UPDATE 2026-07-11 (Datentiefe-Roadmap KOMPLETT LIVE, PRs #56–#59)
> Die „gridstatus-Lücke"-Roadmap ist umgesetzt und deployed. Für Sessions ab jetzt:
> - **37 Zonen sind enabled und befüllt** (Prod-`.env` ENABLED_ZONES = volle Registry) — nicht 3.
>   Default-Tab ist **EUROPE** (All-Zonen-Übersicht), nicht POWER.
> - **Neue Serien in `power_hourly`:** `price.dayahead.qh` + `imbalance.price.qh` (rohe 15-min-
>   Auflösung — SDAC handelt seit 2025-10-01 in 15-min-MTUs), `generation.forecast` (A71),
>   `hydro.reservoir` (A72 wöchentlich, eigene Zonenliste in `entsoe_hydro.HYDRO_ZONES`),
>   `flow.<COUNTERPARTY>` unter Zone `<FROM>` (stündliche Cross-Border-Flows, Block 2.4;
>   kanonische sortierte Border, net_mw > 0 = `<FROM>` exportiert; Backfill via
>   `power_backfill --sources flows`, /cbpf-Monatsblobs im raw_cache).
>   DE_LU-Imbalance läuft über den LÄNDER-EIC `10Y1001A1001A83F` (reBAP).
> - **Forced-Outage-Schwellen sind capacity-relativ** (warn ≥3 %/crit ≥8 % der A68-Fleet, Floors
>   300/500 MW), absoluter 1/3-GW-Fallback für die 18/37 Zonen ohne A68 (IT-Subzonen, SK, CH,
>   Nordics); eine Ableitung für Radar-Detektor UND Hero-Flag (`forced_outage_severity`).
> - **Neue Vertikale: Ausfälle (A77)** — `PowerOutage`-Events mit Revisions-Semantik (höchste
>   Revision je mRID zählt, Withdrawals verschwinden; die meisten Roh-Meldungen sind withdrawn!).
>   `/api/power/outages`, OutagePanel, `forced_outages`-Detektor im Radar, Hero-Flag.
>   ENTSO-E-Fallen: `offset`-Param IMMER explizit senden (auch 0), Fenster < 1 Jahr.
> - **Neue Endpoints:** `/api/power/{hydro,outages,records,forecast-error}`,
>   `/day-ahead/hourly?resolution=qh`.
> - **Eindeutigkeits-Konventionen (verbindlich):** jede Response trägt `as_of`/`age_days`/`stale`
>   (Schwellen gekoppelt an `collectors/freshness.py::SPECS` — auch die neuen Serien sind dort
>   überwacht); alle Zeiten UTC; per-Komponenten-Staleness im Situation-Hero (worst-of);
>   Panels verschwinden nie kommentarlos. Auto-Refresh via `useFetchWithError({pollMs})`.
> - Details + offene Follow-ons: Memory `project_obsyd_datentiefe_roadmap`, Plan-Doc
>   `wir-brauchen-f-r-obsyd-enumerated-otter.md`. Der „Build-Stand"-Abschnitt unten ist HISTORIE.

> ## ⇒ AKTUELLE AUSRICHTUNG 2026-07-03 (überschreibt ALLE Positionierungs-Absätze unten)
> **Endziel (Owner-Entscheidung 2026-07-03): Obsyd = „gridstatus.io für Europa" — ein fokussierter
> europäischer STROM-Desk. Alles Nicht-Strom wird in ein ZWEITPROJEKT aufgespalten.**
> - **Behalter (Obsyd):** der EU-Elektrizitäts-Desk — Day-Ahead-Preise (+Negativpreise), Load/
>   Residuallast, Generation-Mix, Wind/Solar, 20 Cross-Border-Flows, Last-/Residuallast-**Prognose**,
>   Spark-Spread; DE-LU/FR/NL; All-Zones-Übersicht → Zonen-Detail; Anomalie-Radar (nur power/gas);
>   „vs. normal" + „This means" Lesbarkeit. **Gas BLEIBT** (Brennstoff-Seite: Spark + Gasbilanz).
> - **RAUS ins Zweitprojekt (bewahrt, nicht gelöscht — inkl. AIS-/Chokepoint-Karte):** Öl/Maritim/AIS,
>   Metalle, Atlas, Crypto/Rates/Filings/Econ, News, Sentiment.
> - **Sequenz: erst refokussieren (Phase 1, LIVE), dann extrahieren (Phase 2).** Phase 1 deployed
>   2026-07-03: Frontend nur POWER/GAS/ALERTS; Nicht-Strom-Scheduler-Jobs + Startups + AIS AUS;
>   situation/detectors/alerts/scorecards/watchlist/daily_email/evaluator auf power/gas getrimmt.
>   Nicht-Strom-Routes bleiben registriert-DORMANT (Produkt zeigt sie nicht, keine frischen Daten) bis
>   zur physischen Extraktion (Phase 2: eigenes Repo, zweiter Service/DB/Deploy). Kartierung + Plan im
>   Plan-Doc „gridstatus.io für Europa + Obsyd aufspalten"; Vorbilder in [[reference_obsyd_comparables]].
> - **Preis:** komplett gratis, KEINE Umsatzmaschine live. „50–70 Abos"-Ziel = offene Owner-Entscheidung.
> - Alles unten (inkl. der 2026-07-02-„physisches Energiesystem"-Nische + „Gratis-Bloomberg" + „Strom-
>   Desk als Modul") ist HISTORIE.
>
> Verbindliche Single Source of Truth für Positionierung, Ist-Stand und nächste Schritte.
> Strategie ist **state-agnostisch** formuliert (Prinzipien & Entscheidungen), der **Ist-Stand**
> (Abschnitt „Build-Stand") ist zum Datum unten verifiziert und veraltet schneller — bei Konflikt
> gewinnt der Code.
>
> **Stand: 2026-06-24** · **Posture-Entscheidung 2026-06-24: Obsyd ist ein deskriptiver,
> kuratierter ANOMALIE-RADAR (Posture B) — kein Prognose-Anspruch. Exposure-Mapping (Signal→Ticker→
> Richtung) ist verworfen.** Vertikale live in Production: Gas (#14/#15), ENERGY (Spark #16,
> Residuallast/Dunkelflaute #17/#18, Track-Record #20, Generation-Mix #21, Negativpreise #22,
> Multi-Zone DE-LU/FR/NL #24, Cross-Border via Energy-Charts #27), METALS/Kupfer (#28–#30).
> **Anomalie-Radar Phase 1 live (#32):** 7 kuratierte Detektoren über die Vertikal-Flags →
> Cross-Vertical-Feed auf obsyd.dev.
> Offen: Radar Phase 2 (Dunkelflaute/Rerouting/PortWatch persistieren), Slice 4 (Clean-/Dark-Spread)
> blockiert an freier EUA-/Kohle-Quelle; Payment (LS) ungesetzt.
>
> **Vordertür-Entscheidung 2026-06-26: Der Europäische Strom-Desk ist das EINE fokussierte
> Produkt** (Default-Tab POWER, Always-On-Situation-Header-Hero, übrige Verticals depriorisiert).
> Siehe Abschnitt „Vordertür: Europäischer Strom-Desk".

---

## Leitentscheidung: Posture B — deskriptiver Anomalie-Radar

Die früher offene „Hybrid-Posture" ist **am 2026-06-24 zugunsten von Posture B entschieden**. Die
zwei historischen Optionen waren:

- **Mai-Pivot** (`c929043`, 2026-05-23): „freie öffentliche Daten liefern keinen Trading-Edge" →
  ehrliche Open-Source-Aggregation, AGPL-3.0, prädiktive Claims raus, deskriptiv.
- **Edge-These** (verworfen): Premium = Exposure-Mapping (Signal→Ticker→Richtung) + Track Record
  als belegbarer Vorteil, Zielpreis 20–30 €.

**Verbindlich = B (deskriptiv).** Begründung des Owners: ein Prognose-Anspruch wäre **nicht
zuverlässig** — die Validierung zeigt z. B. Gas-Residual IC −0,17 (signifikant, aber *kein*
positiver Edge). Daraus folgt:

- **Obsyd ist ein deskriptiver, kuratierter ANOMALIE-RADAR.** Eine Engine erkennt automatisch, was
  *ungewöhnlich gegenüber der Historie* ist (statistische Abweichung — eine deskriptive Aussage,
  kein Preis-Call), und stellt es als „was ist gerade auffällig"-Feed dar. Primärmodus =
  **Hintergrund-Radar**; die Charts sind die **Drill-Down-/Beweis-Ebene**, nicht zum Draufstarren.
- **Exposure-Mapping (Signal→Ticker→Richtung) ist gestrichen.** Kein Aktien-Richtungs-Call, kein
  „Ticker + Richtung". Aktien bleiben höchstens deskriptiver Kontext (siehe Tier 3).
- **Glaubwürdigkeit = Vollständigkeit + Transparenz + Tempo**, NICHT Trefferquote. Die
  Validierungs-Engine bleibt erhalten, wird aber als *historische Ko-Bewegung (Kontext, keine
  Prognose)* dargestellt, nicht als „Edge". Kein LLM (Texte template-/regelbasiert).
- **Preis: komplett gratis** (Entscheidung 2026-06-25, siehe Abschnitt „Gratis / Preis") — kein
  Pro-Tier mehr. Die früheren €15- / €19,90- / 20–30 €-Thesen sind **alle tot**. (Historischer
  Hinweis: dieser Block nannte bis 2026-06-26 noch „Preis bleibt €15" — das war ein Überrest und
  widersprach der Gratis-Entscheidung; bereinigt.)

---

## Vordertür: Europäischer Strom-Desk (Entscheidung 2026-06-26)

Posture B sagt *was* Obsyd ist (deskriptiver Radar); diese Entscheidung sagt, *welches eine Produkt*
dem Kunden die Vordertür ist. **Die Vordertür ist der Europäische Strom-Desk** — die tiefste eigene
Engine (Day-Ahead inkl. Negativpreise, Residuallast/Dunkelflaute, Spark Spread, Generation-Mix, 20
Cross-Border-Flows, Multi-Zone DE-LU/FR/NL). Ziel = **ein kohärentes, fertiges Produkt**, nicht noch
ein Vertical. Engpass war nie Daten, sondern dass das fokussierte Produkt nur *obenauf* dem breiten
Stapel lag.

- **Im Code durchgesetzt (diese Iteration):** Default-Tab = `POWER` (`DEFAULT_TAB` in `App.jsx`; die
  drei früher auf `'critical'` hartkodierten Stellen ziehen zusammen um). Die übrigen Verticals
  (Öl/Maritim `overview`/`market`/`signals`, `critical`, `metals`, `atlas`, `sentiment`) sind in eine
  **sekundäre Tab-Gruppe** hinter einem Divider depriorisiert (`primary`-Flag in `TABS`) — die Breite
  bleibt der Burggraben *hinter* der Tür, nie co-equal. Damit ist das CLAUDE.md-Prinzip „dem Kunden
  EIN fokussiertes Produkt" endlich im Code, nicht nur auf dem Papier.
- **Always-On-Hero = `PowerSituationHeader`** über `GET /api/power/situation?zone=`
  (`backend/routes/power.py::build_power_situation`, getestet in `test_power_situation.py`): joint
  Day-Ahead → Residuallast → Spark zu *einem* deskriptiven Lagebild (`state` CALM/ELEVATED/STRESSED +
  Flags Dunkelflaute/Negativpreise + z-Kontext vs. Eigenhistorie). Ersetzt den DE-only-Spark als Hero;
  die maritime VesselMap + Briefing sind in den sekundären OVERVIEW-Tab gewandert. Der Zone-Selector
  (DE-LU/FR/NL) sitzt jetzt im Always-On-Shell und steuert Hero + Power-Tab gemeinsam. Spark ist
  DE-only und im Header als `supported:false` für FR/NL signposted.
- **Positionierung angeglichen:** Landing, `index.html` (Title/Meta), `Header`-Subtitle und README
  führen jetzt mit „European Power Desk" statt gemischt „energy market intelligence/AIS" vs. „critical
  materials". Persona: Power-Trader / Energie-Analyst ohne Montel/EEX/Bloomberg-Seat. Header-Mode-Toggle
  (CRUDE/LNG/ALL) entfernt, Ticker TTF/NG-first.
- **Offen (Phase 2 — kohärent fertig):** Strom ins Morgen-Briefing (`briefing.py` ignoriert Power
  noch), Zone-Kohärenz-Löcher (Spark/Flows/Track-Record DE-only sauber zonen oder signposten),
  TTF-Serie als Endpoint exponieren. Clean-Spark/CO₂ bleibt blockiert (keine freie EUA-Quelle).

## Positionierung
- Bezahlbare Down-Market Commodity-/Energie-Intelligence: die zugängliche Alternative für alle,
  die aus Kpler/Bloomberg ausgepreist sind (Muster: Koyfin, TankerTrackers.com).
- Aufbau Knoten für Knoten. Die „Breite" lebt in einer wiederverwendbaren Engine im Hintergrund —
  dem Kunden wird immer EIN fokussiertes Produkt gezeigt, nie „wir machen alles".
- Brand-frontiert / anonym betrieben. Kein öffentliches Gesicht. Glaubwürdigkeit kommt aus
  **Vollständigkeit + Transparenz + Tempo** (vollständige offizielle Daten, ehrlich über Abdeckung,
  schneller als 6 Tabs selbst), nicht aus Ruf — und nicht aus einem behaupteten Trading-Edge.

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
- **Tier 3 (Glück):** → Aktien. Konfundiert, niedrige Konfidenz. Unter Posture B **kein
  Richtungs-Call** — Aktien sind höchstens deskriptiver Kontext, nie ein Signal→Ticker→Richtung.

## Datenprinzipien (nicht verhandelbar)
- Sichtbarer Gratis-Kern = freie, redistributierbare, VOLLSTÄNDIGE offizielle Daten: AGSI
  (Gasspeicher), ALSI (LNG-Terminal-Flüsse), ENTSO-E/SMARD (Strom), EU-ETS (CO₂). Bei diesen
  Rohdaten Parität mit den teuren Anbietern — der Edge ist Aufbereitung & Verbindung.
- Aktien-/Börsen-KURSDATEN NICHT weiterverbreiten. Persönlich ~20–100 €/Monat; kommerzielle
  Display-Lizenz ~300–2500+ €/Monat. Unter Posture B nennt OBSYD **keine Ticker + Richtung** mehr
  (Exposure-Mapping gestrichen) — der Wert ist das physische Lagebild + die Anomalie-Erkennung.
- NIE Absolutwerte zeigen, die nicht vollständig erfassbar sind. Stattdessen relative
  Veränderung/Trend/Index aus einer KONSISTENTEN Teilmenge — der Delta bleibt wahr, auch bei
  unvollständiger Abdeckung, solange diese stabil ist.
- Transparenz über Abdeckung/Konfidenz ist ein GLAUBWÜRDIGKEITS-FEATURE (gegen Kplers Blackbox) —
  der Kern der deskriptiven Posture.
- Proxys gegen offizielle Totale kalibrieren, sobald diese erscheinen (Eurostat, Zoll, GIE).

## Stärken vs. Schwächen
- **STARK:** vollständige offizielle europäische Energie-Register (Gas/LNG/Strom/CO₂); die
  Synthese (Spark/Dark Spread, Merit-Order Gas+CO₂→Strom, Residuallast, Dunkelflaute-/
  Negativpreis-Erkennung, Speicher-vs-Flow-vs-Preis); AIS für ANOMALIE-/Verhaltenserkennung, WO
  die Station Abdeckung hat (Dark Ships, AIS-Lücken, Ship-to-Ship, lokaler Stau).
- **SCHWACH (nie als Aushängeschild):** globale/vollständige Schiffs-Zählungen & Flow-Totale über
  eigenes AIS; Echtzeit-Decision-Grade-Tickdaten; deckende globale Abdeckung. Über offizielle
  Quellen lösen oder weglassen.

## Produkt-Kern: Anomalie-Radar + deskriptive Validierung
- **Anomalie-Radar (GEBAUT, Phase 1 live #32):** kuratierte Detektoren über die persistierten
  Vertikal-Flags erkennen, was *abnormal vs. Historie* ist, und schreiben in den anonymen
  `Alert`-Backbone → Cross-Vertical-Feed („ANOMALY RADAR") auf obsyd.dev, nach Vertikal gruppiert,
  severity-sortiert, Drill-Down zum Evidenz-Chart. Detektoren in `backend/signals/detectors/`
  (gas_balance, days_of_supply, supply_demand_divergence, freight_divergence, floating_storage,
  negative_prices, sentiment_risk), gefahren vom 5-Min-`evaluate_signals`-Job; reine DB-Reads,
  deskriptiv, template-basiert (kein LLM), fehler-isoliert. **NICHT** zu verwechseln mit dem
  Pro-Regel-Baukasten (`AlertRule`/`user_alert_rules`/ALERTS-Tab) — der bleibt unangetastet.
  *Phase 2 offen:* Dunkelflaute/Rerouting/PortWatch erst persistieren, dann verdrahten.
- **Deskriptive Validierungs-Schicht (GEBAUT, umgedeutet):** jedes Signal ist eine Hypothese;
  forward-Bewegung über Fenster (1T/1W/1M) loggen. Rigor: n immer mitzeigen (`n<30 → nie
  „confident"`); RELATIV messen; Overfitting meiden (walk-forward); muss ehrlich zeigen können,
  dass ein Signal NICHT zusammenhängt. Unter Posture B wird das als **historische Ko-Bewegung
  (Kontext, keine Prognose)** dargestellt, nicht als „Edge" (`TrackRecordBadge` entsprechend
  umformuliert). *Ist heute:* **target-aware** Scorecard scort **7 Signale** — Öl/Maritim
  (disruption_score/tonne_miles/freight_proxy gegen Brent), Gas (gas_residual gegen TTF), Energy
  (power_residual/spark_spread gegen Strompreis), Metall (copper_stocks gegen Kupferpreis).

## Gratis / Preis (Ist-Stand — Entscheidung 2026-06-25: KOMPLETT GRATIS, kein Premium)
- **Obsyd ist fürs erste vollständig gratis — es gibt KEIN Pro-Tier mehr.** Lemon-Squeezy-Checkout
  wurde verworfen. Gating-Modell: **Read-Daten public** (Dashboard, Radar, Critical-Materials,
  Spark/Crack/Equities/STS/Validation); **persönliche Features login-gated** (Watchlist + Alert-Rules
  via Free-Magic-Link, `require_auth`); **Admin-Collection-Trigger owner-only** (`require_pro`,
  erfüllt durch das Comp-Subscription via `backend/scripts/grant_pro.py`).
- Die alten Kill-Switch-Flags (`DISABLE_PRO_GATE`, `VITE_DISABLE_PROGATE`) + PricingModal/ProGate/
  Trial/Drip sind **entfernt**; gratis ist der echte Default (kein Flag). Premium-Maschinerie
  (`Subscription`/`webhooks`/`subscription_check`) bleibt dormant im Code (reversibel).
- **Gratis-Logik:** physische Charts (Lagebild) + Anomalie-Radar = Distributionsmagnet. Persönliche
  Features (Watchlist/Alerts/Brief) brauchen nur Login, kein Geld.
- **Lizenz:** AGPL-3.0 — §13 zwingt Netzwerk-Anbieter zur Quelloffenlegung.

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
  Newey-West-HAC-t, Scorecards (`scorecards.py`, `SIGNAL_SPECS` jetzt **7 Signale**, target-aware),
  OOS-Weight-Backtest (`weights.py`), Weekly-Job (`signal_scorecards_weekly`),
  `routes/validation.py`. `TrackRecordBadge.jsx` unter Posture B **deskriptiv umformuliert**
  („historical co-movement … context, not a forecast"), Engine selbst bleibt.

**EU-Gas-Balance-Vertikal — jetzt mit UI** (`backend/gas/`: ENTSOG, AGSI/GIE, ALSI,
ENTSO-E-Power-Burn, Open-Meteo-HDD, Eurostat; Residual-Engine in `balance.py` = *„this is the
product"*). Seit 2026-06-24 (PR #14): **GAS-Tab im Frontend** — Pro-Residual-Hero
(`GasBalancePanel`, RESIDUAL⇄IMPLIED-Toggle, Flag-Marker) + freie Treiber-Panels
`GasStoragePanel`/`GasSupplyPanel`/`GasDemandPanel`; shared `frontend/src/utils/chart.js`.
Gating: **Rohdaten frei, Residual Pro**. ENTSO-E-Token + GIE-Key sind **lokal eingebaut &
validiert**, voller Backfill 2023→heute gelaufen → Power-Burn/Demand/Balance laufen **real,
nicht mehr PRELIMINARY** (`/api/gas/*` liefert ~121 Zeilen @ days=120). **Prod (2026-06-24):**
ENTSO-E-Token + GIE-Key sind auf dem VPS gesetzt, Gas+Energy-Backfill gelaufen, Vertikale
live auf obsyd.dev; der tägliche Scheduler hält alles aktuell.

**Gas-Track-Record (PR #15):** der Gas-Residual ist im Validierungs-Scorecard, **gegen TTF**
gescort (nicht Brent — der Scorecard ist jetzt target-aware, `SIGNAL_SPECS` 4-Tupel). Neue
tägliche **TTF-Preisserie** (`EnergyPrice` + `energy_prices`-Collector, yfinance `TTF=F`).
`TrackRecordBadge` am Balance-Hero. Gemessen: IC −0,17 @ 7d, p=0,001, n=1226 (signifikant).

**ENERGY-Vertikal — live in Prod (PRs #16–#22), Module `backend/power/`:**
- **Day-Ahead-Strompreis** (ENTSO-E A44, DE-LU) → `EnergyPrice(POWER_DE)`; `/api/power/day-ahead`
  (frei) inkl. **Negativpreis-Erkennung** (`PowerPriceDaily`: min/max + resolution-gewichtete
  negative Stunden; rote Marker im Panel).
- **Spark-Spread** (`power − gas·heat_rate`, heat_rate=1/`gas_ccgt_efficiency`) in
  `SparkSpreadHistory`; `/api/power/spark-spread` (Pro).
- **Residuallast + Dunkelflaute** (A65 Last + A75 Wind/Solar → `PowerGrid`, residual_mw gespeichert;
  Flag bei Renewable-Share < 15%); `/api/power/grid` (frei).
- **Generation-Mix** (voller A75-Mix → `PowerGenMix`); `/api/power/generation-mix` (frei).
- **Multi-Zone (PR #24):** Day-Ahead/Last/Mix/Residuallast für **DE-LU/FR/NL** mit Zone-Selector
  (`?zone=`); `POWER_ZONES`. (Spark + Scorecard-Signale bleiben vorerst DE-only — A1-Follow-on.)
- **Cross-Border-Flows (PR #27):** **20 reale Borders** via **Fraunhofer Energy-Charts `/cbpf`**
  (frei, **CC BY 4.0**) → `PowerFlow`; `/api/power/flows` (frei). Ersetzt den ENTSO-E-A11-Versuch;
  FR↔NL existiert physisch nicht (kein Interconnector) → bewusst draußen.
- **Track-Record:** `power_residual` + `spark_spread` im Scorecard gegen **Strompreis** gescort
  (target-aware), Badges auf den Panels.
- **Zurückgestellt (Slice 4) — A0-Spike abgeschlossen 2026-06-24:** Clean-Spark (− CO₂) +
  Dark-Spread (Kohle) bleiben geparkt. **Entscheidung:** keine bestätigte freie, programmatisch
  abrufbare, redistributierbare Tages-EUA-/Kohle-Quelle (yfinance leer, EEX lizenzgesperrt, ICAP
  Terms-restricted, Energy-Charts CO₂ via API unbestätigt). Voller Befund + Unblock-Pfade:
  `docs/findings/2026-06-24-eua-coal-data-source.md`. `co2_price`/`clean_spark_spread` bleiben null.
  Bester Unblock-Pfad: Energy-Charts-CO₂-Code pinnen (CC BY 4.0) oder CSV-Stopgap (Bruegel-Muster).

**METALS-Vertikal — Kupfer-Knoten live (PRs #28–#30, A4):** `backend/metals/usgs_copper.py` +
`backend/models/metals.py`. Monatliches **Angebots-Signal** aus **USGS Mineral Industry Surveys**
(Public Domain, XLSX: Minen-Produktion/Raffinade/Bestände → `CopperSupply`) + **Kupfer-Preis**
(`EnergyPrice("COPPER")`, yfinance `HG=F`); `/api/metals/copper` (frei) + **METALS-Tab** mit
`CopperPanel`. Track-Record: `copper_stocks` (USGS-Bestände) gegen Kupferpreis gescort (JODI-Monats-
muster; aktuell „building n<30"). Quelle: USGS public domain.

**ANOMALIE-RADAR — Phase 1 live (#32):** `Alert.vertical`-Spalte + Migration; `_upsert_alert`
um `vertical=` erweitert; `GET /api/alerts` um `vertical`-Filter + `group_by_vertical`
(severity-sortiert, ungated); `backend/signals/detectors/` (base + 7 Detektoren), eingehängt in
den 5-Min-`evaluate_signals`-Job via `run_all_detectors`; `AlertsPanel.jsx` → Cross-Vertical
„ANOMALY RADAR"-Feed mit Drill-Down. Prod zeigt reale Anomalien (floating_storage, days_of_supply,
supply_demand_divergence). Detektoren werten den NEUESTEN Tag (kein Stale-Flag), deskriptiv, kein
LLM. **Phase 2 offen:** Dunkelflaute/renewable_share, Rerouting-State, PortWatch-Chokepoint erst
persistieren, dann Detektoren ergänzen; bestehende `_check_rerouting`/`/alerts/portwatch`
konsolidieren.

**Fehlt noch:** CO₂/EU-ETS-Feed + Clean-/Dark-Spread, Merit-Order, gas→power→CO₂-Synthese,
Cross-Commodity-Fusion (Vertikale siliert), weitere Knoten (Solar). Spark/Energy-Scorecard
multi-zone (A1-Follow-on). (Exposure-Mapping ist **nicht** „fehlt" — bewusst gestrichen, siehe
Leitentscheidung.)

### Single Source of Truth (vor Fehlern bewahren)
- **Preis:** keiner — Obsyd ist gratis (kein Pro-Tier; PricingModal/PRO_PRICE entfernt).
- **Gating:** `backend/auth/dependencies.py` — `require_auth` (Login: Watchlist/Alert-Rules) vs.
  `require_pro` (nur noch Admin-Collection-Trigger, erfüllt vom Comp-Sub). `is_pro()` in
  `backend/auth/subscription_check.py` ist dormant. Frontend-Gate = `user?.authenticated`.
- **Owner/Comp-Zugang:** `backend/scripts/grant_pro.py` (gibt eine aktive `Subscription` → erfüllt
  `require_pro` für die Admin-Trigger + macht den Daily Brief erreichbar).
- **Lizenz** = `LICENSE` (AGPL-3.0). Premium-Maschinerie (`Subscription`/`webhooks`) dormant.

---

## Roadmap (abgeleitet aus „zwei verbundene Knoten = Launch")

1. ~~**Gas-Vertikal sichtbar machen**~~ — **erledigt 2026-06-24 (PR #14):** GAS-Tab mit
   Residual-Hero + freien Treiber-Panels; ENTSO-E-Token + GIE-Key live (lokal), Backfill
   2023→heute. Rest-Aufgabe: Keys + Backfill in **Prod** (siehe Build-Stand-Caveat).
2. ~~**Gas-Residual in die Validierungs-Schicht**~~ — **erledigt (PR #15):** target-aware
   Scorecard, Gas-Residual gegen TTF, `TrackRecordBadge` am Balance-Panel.
3. ~~**Gas→Energy: ENERGY-Vertikal**~~ — **erledigt (PRs #16–#22), live in Prod:** Day-Ahead-Preis
   + Negativpreise, Spark-Spread, Residuallast/Dunkelflaute, Generation-Mix, Energy-Track-Record.
   **A1 erledigt:** Multi-Zone DE-LU/FR/NL (#24) + Cross-Border-Flows (#27, **20 reale Borders via
   Energy-Charts CC BY 4.0**; ersetzte den ENTSO-E-A11-Versuch, FR↔NL existiert physisch nicht).
   **Slice 4 geparkt (A0-Spike abgeschlossen 2026-06-24):** EUA/Clean-Spark + Dark-Spread — keine
   bestätigte freie Datenquelle, siehe `docs/findings/2026-06-24-eua-coal-data-source.md`.
   **Weiter offen:** Merit-Order, gas→power→CO₂-Synthese, Cross-Commodity-Fusion;
   Spark/Energy-Scorecard multi-zone (A1-Follow-on).
4. **Anomalie-Radar** (Produkt-Kern unter Posture B) — ~~Phase 1~~ **erledigt (#32, live):**
   7 kuratierte Detektoren über die Vertikal-Flags → Cross-Vertical-Feed. **Phase 2 (nächster
   Radar-Schritt):** compute-on-read-Signale persistieren (Dunkelflaute/renewable_share,
   Rerouting-State, PortWatch-Chokepoint), dann Detektoren ergänzen + `_check_rerouting`/
   `/alerts/portwatch` konsolidieren. **Phase 3 (später):** Zustellung über on-site hinaus
   (E-Mail via Resend ist verdrahtet / Push). Exposure-Mapping ist **gestrichen**, nicht geplant.
5. ~~**Metall/Kupfer-Knoten (A4)**~~ — **erledigt (PRs #28–#30), live:** METALS-Tab, USGS-Angebot
   (Public Domain) + Preis (HG=F) + `copper_stocks`-Track-Record. **Danach** weitere Knoten (Solar …),
   jeder als fokussierter Zusatz.
6. **Track B / Launch & Payment (parallel, Umsatz-Engpass):** Lemon-Squeezy-Checkout-URL +
   Webhook-Secret in Prod setzen (heute ungesetzt → niemand kann zahlen); den **Anomalie-Radar +
   das physische Lagebild** auf Landing/Pricing als Wert zeigen (deskriptiv, kein Edge-Claim);
   Impressum/Datenschutz/AGB; Plausible/Mail-Erfassung.

---

## Bekannte Inkonsistenzen (Cleanup)
- ~~Preis-Leiche in Trial-Mails (`trial_drip.py`): Pre-Pivot **€19,90/199 €**~~ — **behoben
  2026-06-24**, auf €15/€149 angeglichen. (Lehre: Preis-Strings leben verstreut; bei künftigen
  Preisänderungen `trial_drip.py`, `PricingModal.jsx` und README zusammen anfassen.)
- ~~**ENTSO-E-Token in Prod** unbestätigt~~ — **erledigt 2026-06-24:** Keys auf dem VPS gesetzt,
  Prod-Backfill gelaufen, Gas+Energy live. (Historie:) `ENTSOE_API_TOKEN`
  + `GIE_API_KEY` (letzterer aus `commodity-signal` übernommen) eingebaut & validiert, Backfill
  lief. **Offen für Prod:** beide Keys in die VPS-Prod-`.env` + dortiger Backfill — sonst Gas-Tab
  in Production leer. (Entscheidung „kein LLM in Obsyd / keine commodity-signal-Fusion" siehe Memory.)
- **Deploy-Docs widersprüchlich:** README „Tech Stack" nennt nginx/systemd/Let's-Encrypt, die
  `deploy/`-Skripte sind Caddy-zentriert (obsyd.dev teilt Caddy mit ValueKick).
- **`docs/signal-validation.md` Status-Banner** (P1 shipped) hinkt dem Ist nach — Scorecards sind
  persistiert, Routes live (faktisch P2/P3-Teile gebaut). Zudem noch prädiktiv geframt — sollte
  unter Posture B deskriptiv umformuliert werden (wie `TrackRecordBadge`).
- ~~**README** spiegelt evtl. noch Pre-Posture-B-Framing (Edge/Exposure)~~ — **behoben 2026-06-26:**
  README-Lede, `index.html` (Title/Meta), `Header`-Subtitle und Landing führen jetzt mit „European
  Power Desk" (deskriptiv). Das Features-**Inventar** im README listet bewusst weiter die volle
  Engine-Breite (das ist der Burggraben, nicht die Vordertür).
- **`PROJECT_STATUS.md`** war ein vor-Pivot-Statusdoc (2026-03-06) und wurde durch diesen Abschnitt
  ersetzt (siehe Git-Historie für das Archiv).
